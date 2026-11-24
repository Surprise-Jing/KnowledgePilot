# 设计：Docling 结构化解析、Parser Adapter 与可溯源引用

- 日期：2026-11-23
- 状态：草案，待确认
- 影响范围：后端解析/入库/检索/文档 API、共享契约（contracts v1.6 → v1.7，schema v4 → v5）、前端类型与分期展示

## 1. 目的与背景

KnowledgePilot 需要解析金融财报这类文档：版面复杂、表格密集，有时还是扫描件。现在的 PDF 处理只用 `pypdf.extract_text()` 按页抽纯文本，表格结构、阅读顺序和标题层级都会丢失，扫描件直接失败；引用只能定位到"第 N 页"。

目标架构（已确认）：

> KnowledgePilot 采用可插拔 Parser 架构，对 PDF/DOCX 使用 Docling 进行结构化解析，对 TXT 使用轻量解析器。基于 Docling HybridChunker 完成结构感知、Token 约束的分块，使用 BGE-M3 构建向量索引，并通过 SQLite 持久化 Chunk 及来源元数据。同时保存 Markdown 和结构化解析产物，使 Agent 引用能够回溯到文档元素及其原始页面位置，支持后续 PDF 页面跳转和区域高亮。

引用溯源的最终体验（已确认）：用户点击 AI 回答里的引用，右侧打开原始文档，跳到对应位置并突出证据区域。具体是：引用原文片段 → 打开 PDF 并跳到对应页 → 在 PDF 上高亮对应区域。先做后端，前端按难度分期实现。

## 2. 目标 / 非目标 / 反目标

**目标**

1. PDF、DOCX 改用 Docling 解析；TXT 保留轻量解析器。三者都通过统一的 Parser Adapter 接口输出。
2. 两种解析模式，由用户选择：
   - **普通模式 `standard`**：版面分析 + 表格结构识别（TableFormer）；关闭 OCR、公式增强和图片描述。
   - **增强模式 `enhanced`**：在普通模式基础上开启 OCR（用于扫描件、复杂文档）。
3. 使用 HybridChunker 分块：按文档结构切分，块大小以 BGE-M3 分词器的 token 数为上限；标题层级进入元数据。
4. 每个 chunk 持久化来源元数据：页码范围、文档元素引用（Docling `self_ref`）、元素类型、元素所在页和归一化 bbox。
5. 每份文档保存 Markdown 和 Docling 结构化 JSON 两种解析产物；提供 Markdown 导出接口和原文件访问接口。
6. 引用快照里带上元素和 bbox，为前端跳页和高亮提供数据。
7. 全部有自动化测试，测试不联网、不下载模型、不加载 Docling 模型。

**非目标（本期不做）**

- 图片描述（VLM）、公式增强、图表理解。
- BGE-M3 稀疏向量或 ColBERT 检索；ANN 索引或向量数据库。
- 跨页段落合并的特殊处理（交给 HybridChunker 的默认行为）。
- 不重新解析就重新分块（结构 JSON 已保存，留作后续能力）。
- 精确到表格单元格或行级别的 PDF 高亮（本期高亮到 Docling 元素一级，比如整张表或整段）。
- 前端 P2（PDF 渲染跳页）和 P3（区域高亮）不在本期实现，只保证数据和接口齐备，见第 9 节。

**反目标（出现即视为失败，即使"能跑"）**

- Web 主进程 import Docling 或 torch，导致 Web 服务常驻占用数 GB 内存，或被解析崩溃拖垮。
- Docling 解析失败时悄悄退回 pypdf 纯文本，让用户以为拿到了结构化结果。
- 模型可见的工具结果里塞进大量 bbox 坐标，浪费上下文。
- 测试依赖网络或真实模型权重。

## 3. 约束

- AGENTS.md：分层是路由 → 服务 → 数据库；不静默吞异常；不新增 Redis、队列、微服务这类大型依赖；DB 连接不跨 `await`；日志不输出文档全文。Docling 是用户明确要求引入的依赖，作为本次的决策记录在第 10 节。
- 共享契约文件（`contracts.md`、`schemas.py`、`db.py`、`config.py`、`main.py`、`pyproject.toml`、`.env.example`）由主 Agent 修改。本次由主 Agent 直接实施。
- 运行环境：Apple M5、16GB 内存、Python 3.12（`requires-python >= 3.11`）。
- `PK_MAX_UPLOAD_BYTES` 默认 10MB，财报 PDF 可能更大，需要在文档里说明可调大。

## 4. 方案比较与选择

### 4.1 Docling 在哪里运行

| 方案 | 说明 | 取舍 |
| --- | --- | --- |
| a. Web 进程内 | 沿用 `BackgroundTasks` 线程池，缓存转换器，用锁串行 | 最简单；但无法超时中止，原生库崩溃会拖垮 Web，模型常驻 Web 进程 |
| **b. 独立解析子进程（已选）** | 单个 spawn 子进程一次处理一份文档；主进程只传路径和模式，子进程写产物并返回分块结果 | 能超时杀进程，崩溃隔离，Web 进程不 import Docling；多一个进程管理模块 |
| c. docling-serve HTTP 服务 | 独立部署官方服务 | 违反"不加微服务"约束，排除 |

### 4.2 BGE-M3 怎么部署

| 方案 | 说明 | 取舍 |
| --- | --- | --- |
| **a. 沿用现有 OpenAI 兼容 `/embeddings` 服务（已选）** | `PK_EMBEDDING_MODEL=bge-m3` 不变；只加载 BGE-M3 分词器，供 HybridChunker 计算 token | 复用已有的评测、签名、重建和 BM25 降级逻辑；需要一个外部（或本地）Embedding 服务 |
| b. 进程内加载权重 | sentence-transformers / FlagEmbedding | 不依赖服务，可用稀疏向量；但要下载约 2.3GB 权重，CPU/MPS 上入库更慢，稀疏向量与现有 BM25 作用重叠 |

### 4.3 来源元数据怎么存（主 Agent 决定）

| 方案 | 取舍 |
| --- | --- |
| **a. `chunks.elements` JSON 列（已选）** | 只需 `ADD COLUMN`，不重建表；元素只会随 chunk 一起读，不需要单独查询 |
| b. 新表 `chunk_elements` | 便于按页或按元素查询，但当前没有这种需求，徒增 join |

沿用现有的 `loc_kind` 值（`pdf_page`、`docx_paragraphs`、`docx_table_rows`、`txt_lines`），不改 CHECK 约束，因此迁移不需要重建表。

## 5. 总体架构

```
上传 ──► documents(queued, parse_mode)
            │ BackgroundTasks
            ▼
ingest(db, id, settings, parsers)            ← app/services/ingestion.py（Web 进程）
  ├─ registry.parser_for(file_type) ─► Parser 接口                 ← app/parsers/
  │     ├─ TextParser（进程内）
  │     └─ DoclingParser（进程内代理）──Pipe──► 解析子进程            ← app/parsers/worker.py
  │                                         ├─ DocumentConverter(standard|enhanced)  缓存
  │                                         ├─ 写 data/artifacts/{id}.tmp/{document.json, document.md}
  │                                         └─ HybridChunker(BGE-M3 tokenizer) → ParsedChunk[]
  ├─ 产物目录原子替换  data/artifacts/{id}/
  ├─ embed(heading 上下文 + content)  （vector / hybrid 模式）
  └─ BEGIN IMMEDIATE：替换 chunks（含 elements）+ embeddings，documents → success
```

## 6. 详细设计

### 6.1 Parser Adapter 层（新包 `backend/app/parsers/`）

```python
# app/parsers/base.py
ParseMode = Literal["standard", "enhanced"]

@dataclass(frozen=True)
class ParseRequest:
    path: Path            # 原文件
    file_type: str        # "txt" | "pdf" | "docx"
    mode: ParseMode
    output_dir: Path      # 本次产物写入的临时目录（解析器负责创建内容，不负责替换）

@dataclass(frozen=True)
class SourceElement:
    ref: str              # Docling self_ref，如 "#/texts/42"、"#/tables/3"；TXT 为 "line:12"
    label: str            # Docling DocItemLabel 值：text / section_header / table / list_item / caption …
    page: int | None      # 1-based；DOCX/TXT 为 None
    bbox: tuple[float, float, float, float] | None  # 归一化 [x0, y0, x1, y1]，左上原点，0–1

@dataclass(frozen=True)
class ParsedChunk:
    content: str                       # chunk 原文（不含标题前缀），用于展示与引用
    kind: LocationKind                 # 沿用现有四种
    start: int; end: int               # 单位由 kind 决定，1-based 闭区间
    table: int | None = None
    heading_path: tuple[str, ...] = ()
    char_start: int | None = None
    char_end: int | None = None
    elements: tuple[SourceElement, ...] = ()

@dataclass(frozen=True)
class ParseResult:
    chunks: list[ParsedChunk]
    parser: str                        # 如 "docling 2.x / standard"、"text"
    page_count: int | None             # 仅 PDF
    has_markdown: bool                 # output_dir/document.md 已写出
    has_structure: bool                # output_dir/document.json 已写出

class Parser(Protocol):
    def parse(self, request: ParseRequest) -> ParseResult: ...
```

- `app/parsers/registry.py`：`ParserRegistry` 按 `file_type` 返回 Parser。默认注册 `txt → TextParser`、`pdf/docx → DoclingParser`。`create_app` 把它放进 `app.state.parsers`，测试可以替换（与 `llm_factory` 的注入方式一致）。`ingest` 增加参数 `parsers: ParserRegistry`。
- `TextParser`：把现有 TXT 逻辑原样搬过来（UTF-8 / GB18030 解码、按行分块、超长行加窗口重叠）。`elements` 每行一项（`ref="line:N"`、`label="text"`），不带 page 和 bbox。Markdown 产物就是原文，不写结构 JSON。现有 `build_chunks` 合并逻辑随 TextParser 迁入 `app/parsers/text.py`。
- 出错约定：解析器抛 `ParseError(message)`（中文、面向用户），`ingest` 把它写进 `documents.error`。其他异常由 `ingest` 按现有规则 `logger.exception` 后转成通用失败消息。

### 6.2 解析子进程（`app/parsers/worker.py` + `app/parsers/docling_runner.py`）

- **只有子进程 import Docling**。`docling_runner.py` 是子进程的入口模块，Web 进程不 import 它（加一个测试断言：导入 `app.main` 后 `sys.modules` 里没有 `docling`）。
- `ParserWorker`（Web 进程侧）：
  - 用 `multiprocessing.get_context("spawn")` 惰性启动 1 个子进程，通过 `Pipe` 发 `(job_id, ParseRequest)`、收 `(job_id, ok, ParseResult | 错误消息)`。数据都是可 pickle 的 dataclass，不传 DoclingDocument 对象。
  - 用 `threading.Lock` 串行化，同一时间只处理一份文档。
  - 等待时用 `conn.poll(timeout)`。超过 `PK_PARSE_TIMEOUT` 就 `terminate()` 再 `join()`，抛 `ParseError("解析超时……")`，下一份文档到来时重新拉起子进程。
  - 子进程意外退出（EOF / 非零退出码）时抛 `ParseError("解析进程异常退出……")` 并记录 `logger.error`（只记退出码和文档 id）。
  - 子进程设为 `daemon=True`。应用 lifespan 结束时调用 `worker.close()`，退出时不残留进程。
  - 子进程入口函数可注入（`target="module:function"` 字符串），测试用 `tests/fakes` 里的假入口验证超时、崩溃和正常返回，无需 Docling。
- **文档状态时序**：`ingest` 先在 Web 进程拿到 worker 锁，再把 `queued → parsing`。排队等锁的文档保持 `queued`，前端能区分"排队中"和"解析中"。TXT 不经过 worker，也不等锁。
- 子进程内的 Docling 转换器按模式缓存（`standard` / `enhanced` 各一个，首次使用时创建）。模型只在子进程加载一次。

### 6.3 Docling 配置

普通模式 `standard`（PDF）：

```python
PdfPipelineOptions(
    do_ocr=False,
    do_table_structure=True,
    table_structure_options=TableStructureOptions(mode=TableFormerMode.ACCURATE, do_cell_matching=True),
    do_formula_enrichment=False,
    do_picture_description=False,
    do_picture_classification=False,
    generate_page_images=False,
    accelerator_options=AcceleratorOptions(device=settings.docling_device, num_threads=settings.docling_threads),
    artifacts_path=settings.docling_artifacts_path or None,
)
```

增强模式 `enhanced`：在上面基础上 `do_ocr=True`，`ocr_options` 由 `PK_DOCLING_OCR_ENGINE` 选择（默认 `rapidocr`，支持中英文、基于 onnxruntime、不需要系统二进制；也允许 `easyocr`、`tesseract`、`ocrmac`）。`force_full_page_ocr` 由 `PK_DOCLING_FORCE_FULL_PAGE_OCR` 控制（默认 `true`，扫描件更稳）。

DOCX：使用 Docling 的 Word 后端（不需要模型）。两种模式效果相同，`parse_mode` 照常记录，但不影响结果。

**表格在财报场景是重点**：TableFormer 使用 ACCURATE；分块时表格用 Markdown 表格序列化（而不是 HybridChunker 默认的三元组），大表拆块时重复表头（以锁定版本的 docling-core API 为准，见 §12）。

### 6.4 分块（HybridChunker）

- 分词器：`HuggingFaceTokenizer(AutoTokenizer.from_pretrained(PK_CHUNK_TOKENIZER), max_tokens=PK_CHUNK_MAX_TOKENS)`，默认 `BAAI/bge-m3`、`512`。离线环境可把 `PK_CHUNK_TOKENIZER` 设成本地目录。
- `HybridChunker(tokenizer=..., merge_peers=True, serializer_provider=<Markdown 表格>)`。
- 每个 Docling chunk 映射成一个 `ParsedChunk`：
  - `content = chunk.text`（不带标题前缀）。
  - `heading_path = tuple(chunk.meta.headings or ())`。
  - `elements`：遍历 `chunk.meta.doc_items`，每个元素的每条 `prov` 产出一个 `SourceElement`（同一元素跨页时会有多条）。bbox 先转成左上原点 `bbox.to_top_left_origin(page_height)`，再分别除以页宽、页高，裁剪到 [0, 1]，保留 4 位小数。页尺寸来自 `document.pages[page_no].size`。同一 `(ref, page)` 去重。
  - **PDF**：`kind="pdf_page"`，`start/end` = elements 中的最小页码和最大页码。没有 prov 的 chunk（极少见）沿用前一个 chunk 的页码，并把这种情况写进 warning 日志计数，不写内容。表格 chunk 的 `table` = 该表在文档中的序号（1-based）。
  - **DOCX**：文本 chunk 用 `kind="docx_paragraphs"`，`start/end` 是首、末文本元素在文档阅读顺序中的序号（1-based，只计文本类元素）。表格 chunk 用 `kind="docx_table_rows"`，`table` = 表序号，`start=1`、`end=表行数`（大表被拆成多块时也记录整表的行范围，本期不追踪行级范围）。
  - `char_start/char_end`：Docling 分块不提供可靠的字符范围，统一为 `None`。
- 结果为空时：PDF 普通模式失败，提示"未提取到文本；如为扫描件，请使用增强模式（OCR）重新解析"；增强模式失败，提示"OCR 后仍未提取到文本，请检查文件"。

### 6.5 解析产物

- 目录 `{PK_DATA_DIR}/artifacts/{document_id}/`，包含：
  - `document.md`：Docling 的 `export_to_markdown()`，图片占位用 `<!-- image -->`，页与页之间不插分页标记。TXT 直接写原文。
  - `document.json`：Docling 的 `save_as_json()`，即结构化 DoclingDocument。TXT 不写这个文件。
- 原子性：解析写入 `artifacts/{id}.tmp-{uuid}/`；全部 chunk 和向量提交成功后，把旧目录改名备份、新目录改名为正式目录，再删除备份。入库失败就删除临时目录，旧产物不动（文档此时是 `failed`，导出不可用；下次成功时被替换）。
- 删除：所有删除原文件的路径（文档、目录递归、知识库）同时删除 `artifacts/{id}/`。失败时与原文件处理方式一致，记 warning、不回滚。
- 启动恢复：`db.initialize()` 之后清理残留的 `artifacts/*.tmp-*`。

### 6.6 数据模型（schema v5，仅 ADD COLUMN）

```sql
ALTER TABLE documents ADD COLUMN parse_mode TEXT NOT NULL DEFAULT 'standard'
    CHECK (parse_mode IN ('standard','enhanced'));
ALTER TABLE documents ADD COLUMN parser TEXT;          -- 例如 "docling 2.x / standard"；旧数据为 NULL
ALTER TABLE documents ADD COLUMN page_count INTEGER;   -- 仅 PDF
ALTER TABLE chunks ADD COLUMN elements TEXT NOT NULL DEFAULT '[]';  -- SourceElement[] JSON
```

- **不保留旧数据（用户决定）**：上线新版本时清空本地开发数据（`backend/data/knowledge.db` 及 `backend/data/uploads/`），按最新 schema 新建数据库，之后由用户重新上传文件。因此不需要兼容 pypdf / python-docx 时代的旧 chunk，也不需要升级后重建向量。迁移仍写成 v5（`ADD COLUMN`），保持迁移链完整、测试可验证。
- 每个 chunk 的 `elements` 按 `(ref, page)` 去重，最多保留 64 项（超出部分丢弃并记录 warning 计数），控制引用快照的体积。
- `docs/data-model.md` 同步更新。

### 6.7 检索与向量输入

- **向量输入文本**：`embedding_text(heading_path, content) = "\n".join([*heading_path, content])`，与 HybridChunker 的 `contextualize()` 格式一致。入库和 `app.reindex` 共用这个函数。这能显著提升财报表格块（比如"合并资产负债表"下的表格）的召回。
- 向量输入格式变了，**向量签名加入输入格式版本**（`profile` 的 identity 里加 `"text:v2"`）。数据库会清空重建，这一步只是为了防止将来误用旧向量。
- BM25 和余弦词频同样对 `heading_path + content` 计分。
- `SearchHit.content` 仍是原文 content，不带标题前缀。

### 6.8 契约变更（contracts v1.7）

**Location** 增加字段（`schemas.py` 与 `frontend/src/api/types.ts` 同步）：

```python
class SourceElement(BaseModel):
    ref: str
    label: str
    page: int | None = None          # 1-based
    bbox: list[float] | None = None  # [x0, y0, x1, y1]，归一化 0–1，左上原点

class Location(BaseModel):
    ...                              # 现有字段不变，label 计算规则不变
    elements: list[SourceElement] = Field(default_factory=list)
```

- PDF 的 `label` 仍然是"标题路径 › 第 3–4 页"；表格 chunk 加上"表 N"前缀，例如"… › 表 2 第 5 页"。实现方式是放宽 `label` 里对 `table` 的处理：`pdf_page` 带 `table` 时也加表号。
- `ChunkOut`、`SearchHit`、`CitationOut` 和引用快照 `citations.location_json` 都带完整的 `elements`。HTTP 接口原样返回。
- **模型可见的工具结果**（`search_chunks`、`read_document`，HTTP 与 MCP 一致）里的 `location` 去掉 `elements`，只保留 `kind/start/end/table/heading_path/label`。`CitationSource` 仍然保存完整 location，所以引用快照有 bbox。

**DocumentOut** 增加：`parse_mode: "standard" | "enhanced"`、`parser: str | null`、`page_count: int | null`。

**上传**：multipart 增加可选字段 `parse_mode`（`standard` | `enhanced`，空字符串等于省略，默认 `standard`）。TXT 一律记为 `standard`。重复内容上传仍返回已有记录，不触发重新解析。

**新接口**：

| 方法 | 路径 | 规则 |
| --- | --- | --- |
| POST | `/api/documents/{id}/reparse` | 请求体 `{parse_mode?}`（省略时沿用文档当前模式）。只允许 `success` / `failed`；`queued` / `parsing` → 409 `document_busy`（retryable）。返回 202 和更新后的 `DocumentOut`（status=`queued`，parse_mode 为新值）。沿用现有状态机：**重新解析期间文档不可检索、不可读取、不可导出**；失败后为 `failed`，只能再试；中途重启同样被 `initialize()` 标为 `failed`。旧 chunk 和向量在新结果提交时才被替换（失败时留在库中但不可见，下次成功时覆盖）。 |
| GET | `/api/documents/{id}/markdown` | `200 text/markdown; charset=utf-8`，`Content-Disposition: attachment; filename*=UTF-8''<原文件名去扩展名>.md`。文档不是 success → `document_not_ready`；旧数据没有产物 → 409 `markdown_not_available`（"请重新解析后再导出"）。 |
| GET | `/api/documents/{id}/file` | 返回原文件，`Content-Type` 按类型给出，`Content-Disposition: inline`，供前端 PDF 渲染使用。原文件丢失 → 404 `document_not_found`。 |

`POST /api/documents/{id}/retry` 保持现有行为（只允许 failed），沿用文档当前的 `parse_mode`。

**新错误码**：`document_busy`（409，是）、`markdown_not_available`（409，否）。解析超时和子进程崩溃不是 HTTP 错误，只写入 `documents.error`。

### 6.9 配置（`config.py` / `.env.example`）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PK_PARSE_TIMEOUT` | `1800` | 单个文档解析超时（秒），超时会杀掉子进程 |
| `PK_CHUNK_TOKENIZER` | `BAAI/bge-m3` | HybridChunker 分词器（HF 名称或本地目录） |
| `PK_CHUNK_MAX_TOKENS` | `512` | 每块 token 上限（64–8192） |
| `PK_DOCLING_ARTIFACTS_PATH` | 空 | Docling 模型本地目录；空则使用 HF 缓存 |
| `PK_DOCLING_DEVICE` | `auto` | `auto` / `cpu` / `mps` / `cuda` |
| `PK_DOCLING_THREADS` | `4` | 推理线程数 |
| `PK_DOCLING_OCR_ENGINE` | `rapidocr` | 增强模式的 OCR 引擎 |
| `PK_DOCLING_FORCE_FULL_PAGE_OCR` | `true` | 增强模式是否整页 OCR |

**模型预下载**：新增命令 `uv run --directory backend python -m app.parsers.prefetch`，下载 Docling 的版面/表格模型和 BGE-M3 分词器到缓存（或 `PK_DOCLING_ARTIFACTS_PATH`），便于之后离线运行。文档说明首次解析会自动下载，需要联网。

## 7. 依赖变更

- **Docling 是必装依赖（用户决定）**：`uv sync` 一次装好，PDF/DOCX 始终走 Docling。缺少模型且无法联网下载时，文档解析失败，错误信息提示运行 prefetch 命令；不提供降级路径。README 写明首次解析需联网，或先执行 prefetch。
- 后端新增 `docling`（以实施时 PyPI 最新的 2.x 为准，锁定小版本范围 `>=2.N,<3`）。torch、transformers、docling-core、docling-ibm-models 随之进入。如果 docling 提供 RapidOCR 的 extra 就用 extra，否则显式加 `rapidocr` + `onnxruntime`。实施时核对实际包名。
- `pypdf` 从产品依赖移到 dev 依赖组：产品代码不再使用，只用于测试和 `eval/build_corpus.py` 生成 PDF 样本，以及 §8 中的测试替身解析器。`python-docx` 同理：产品代码不再直接使用（docling 会传递依赖它），从直接依赖移到 dev 组，供测试生成 DOCX 样本。
- 新增依赖的磁盘开销粗估：torch 约 700MB，Docling 模型约 0.5–1GB（HF 缓存），BGE-M3 分词器约 20MB。
- 前端本期不加依赖。P2 时再评估 `pdfjs-dist`（或 `react-pdf`），需要列入 `frontend/package.json` 白名单。

## 8. 测试策略（不联网、不加载模型）

1. **Docling 映射单测**：用 docling-core 的类型手工构造一个 `DoclingDocument`（标题、段落、跨页段落、表格、带 prov 和 bbox，页尺寸已知），配合 `tests/` 里的确定性测试分词器（按字符计数、实现 docling-core 的 `BaseTokenizer`），验证页码范围、heading_path、表号、归一化 bbox（含左下转左上原点）、去重、空文档。这要求映射逻辑写成纯函数 `chunks_from_document(doc, chunker) -> list[ParsedChunk]`，放在 `docling_runner.py` 里，可以单独 import（只依赖 docling-core，测试环境可以 import，不加载模型）。
2. **Worker 单测**：注入 `tests/fakes` 里的假子进程入口，覆盖正常返回、超时杀进程后再次可用、子进程崩溃、错误消息透传、关闭时不残留进程。
3. **入库单测**：注入假 Parser（通过 `ParserRegistry`），覆盖产物目录原子替换、入库失败时保留旧产物、elements 落库、embedding 输入文本含标题、排队时保持 `queued`。
4. **API 测试**：上传带 `parse_mode`、非法模式 422、reparse 状态机（busy、模式切换）、Markdown 导出（含中文文件名、旧数据 409）、原文件接口、删除时清理产物。
5. **契约测试**：Location 带 elements 的序列化；工具结果里没有 elements；引用快照带 elements。
6. **迁移测试**：v4 数据库升级到 v5，旧 chunk `elements='[]'`，旧文档 `parse_mode='standard'`。
7. **Web 进程隔离断言**：导入 `app.main` 后 `"docling" not in sys.modules`。
8. **可选真机测试**（`tests/e2e`，环境变量 `PK_RUN_DOCLING_TESTS=1` 才运行）：用一份公开的小 PDF（含表格）跑真实 Docling 普通模式，检查有 bbox、有表格 chunk、Markdown 非空。
9. **现有门禁中走真实解析的测试**（`test_api.py::test_docx_and_pdf`、`tests/e2e/test_eval.py` 的语料解析检查等）：在 `tests/fakes/parsers.py` 提供测试替身解析器 `LegacyPdfDocxParser`，用 pypdf / python-docx（dev 依赖）复现旧的按页、按段定位，通过 `ParserRegistry` 注入。这样门禁保持离线，评测用例里已有的页码和段落标注不用改。真实 Docling 下的端到端覆盖交给第 8 条的可选测试。
10. **评测重标注**：用真实 Docling 重新标注 `eval/cases.jsonl` 并重跑 `docs/eval-report.md`，**不在本期**。等用户上传真实文件、Docling 结果稳定后，作为后续任务。

门禁命令（AGENTS.md §2）全部通过。

## 9. 前端分期

| 期 | 内容 | 本期 |
| --- | --- | --- |
| P1 | 类型同步（`Location.elements`、`DocumentOut` 新字段）；上传时选择解析模式；文件列表显示模式与页数，提供"重新解析（普通/增强）"和"导出 Markdown"；引用卡片已经显示原文片段（现有 `CitationsPanel`），标签改为新的 PDF 表格标签 | ✅ |
| P2 | 点击引用后右侧打开 PDF（`/api/documents/{id}/file`），跳到 `location.start` 页；非 PDF 保持现有片段列表 | 后续 |
| P3 | 在 P2 的页面上，按 `elements` 中当前页的 bbox 画半透明覆盖层；多页时可切换 | 后续 |

P2、P3 各自单独设计和计划。本期后端保证它们需要的数据和接口完整。

## 10. 关键决策记录

| 决策 | 由谁决定 |
| --- | --- |
| PDF / DOCX 用 Docling，TXT 用轻量解析器；HybridChunker；BGE-M3；SQLite；保存 Markdown 和结构产物 | 用户 |
| 普通 / 增强两种模式，增强模式由用户选择后启用 OCR | 用户 |
| 溯源最终形态是跳到原 PDF 页并高亮；先做后端，前端分期 | 用户 |
| 独立解析子进程；BGE-M3 沿用现有服务，只加载分词器 | 用户（采纳推荐） |
| 清空现有数据库和上传文件，按新 schema 重建，用户之后重新上传 | 用户 |
| Docling 为必装依赖，不做可选 extra、不降级 | 用户（采纳推荐） |
| 重新解析沿用现有状态机（期间不可用，失败为 failed） | 主 Agent（用户不保留旧数据，无需更复杂的版本切换） |
| 门禁用测试替身解析器保持离线；pypdf / python-docx 移入 dev 组；评测重标注延后 | 主 Agent |
| 每个 chunk 的 elements 最多 64 项 | 主 Agent |
| elements 用 JSON 列，不建新表；沿用现有 loc_kind | 主 Agent |
| 上传时选模式（默认普通）+ 重新解析时可切换；文字为空时提示改用增强模式 | 主 Agent |
| 表格使用 Markdown 序列化，TableFormer 使用 ACCURATE | 主 Agent |
| 增强模式默认 RapidOCR、整页 OCR | 主 Agent |
| 向量输入带标题上下文，签名升级后需要重建一次 | 主 Agent |
| Docling 失败不回退 pypdf | 主 Agent |
| 重新解析期间文档不可检索 | 主 Agent |
| 不做 Docling 重新分块、单元格级高亮、VLM | 主 Agent（非目标） |

## 11. 风险与已知限制

- **首次运行要联网下载模型**；离线部署需要先执行 prefetch。
- **解析速度**：普通模式在 M5 上粗估每页 0.5–2 秒，OCR 增强模式每页可能数秒。300 页的财报可能需要数分钟到十几分钟。同一时间只处理一份，后续文档排队。实施后用真实样本测量并写进文档。
- **内存**：解析子进程峰值预计 2–4GB，Web 进程不受影响。
- **bbox 精度**：高亮到元素一级。HybridChunker 把超长元素拆成多块时，每块都指向整个元素的 bbox。
- **DOCX 没有页码和坐标**，只能定位到元素和标题路径。
- 上线时清空本地数据库和上传文件，之前上传的文档需要重新上传。
- 必装 Docling 后，`uv sync` 安装体积增加 GB 级。

## 12. 留给实施者

- 锁定的 docling / docling-core 版本里的具体 API 名称（Markdown 表格序列化器的 provider 写法、重复表头选项、OCR options 类名、`HuggingFaceTokenizer` 的导入路径），以实际版本为准，并在代码注释里写明版本。
- 测试分词器的具体实现方式。
- prefetch 命令的实现细节（可以调用 docling 自带的模型下载工具）。
- Markdown 导出文件名的边界处理（没有扩展名、全是非法字符等）。
