# Docling 结构化解析与可溯源引用 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PDF/DOCX 改用 Docling 解析（普通 / 增强两种模式），HybridChunker 分块。chunk 带元素级来源和归一化 bbox 存入 SQLite。保存 Markdown 和结构 JSON 产物，提供导出、重新解析、原文件接口。前端完成 P1。

**Architecture:** 新包 `app/parsers/` 提供统一的 Parser 接口：TXT 在 Web 进程内解析；PDF/DOCX 交给一个 spawn 子进程，Docling 只在子进程里加载。`ingest` 通过注入的 `ParserRegistry` 拿到解析器，把产物目录原子替换，并在一个事务里写入 chunks（含 `elements` JSON）和向量。

**Tech Stack:** Python 3.12、FastAPI、SQLite、uv、docling 2.152.0 / docling-core 2.108.0（已在临时环境验证 API）、React + TS + shadcn/ui。

**Spec:** `docs/superpowers/specs/2026-11-23-docling-parsing-design.md`（执行者必须同时阅读）

## Global Constraints

- 依赖：`docling>=2.152,<3` 是必装产品依赖；`pypdf`、`python-docx` 从产品依赖移到 `[dependency-groups].dev`。不加其他产品依赖。
- Web 进程（导入 `app.main` 后）的 `sys.modules` 里不得出现 `docling` 或 `docling_core`；只有 `app/parsers/docling_runner.py` 和 `app/parsers/docling_mapping.py` 可以 import 它们，并且只能被子进程和测试导入。
- 坐标约定：`bbox = [x0, y0, x1, y1]`，左上原点，按页宽和页高归一化到 0–1，保留 4 位小数；`page` 从 1 开始。
- 每个 chunk 的 `elements` 按 `(ref, page)` 去重，最多 64 项。
- 模型可见的工具结果（HTTP 和 MCP 一致）里的 `location` 不含 `elements`。
- 面向用户的错误文案用中文，代码注释用英文；Ruff 行宽 100；不静默吞异常；DB 连接不跨 `await`；日志不输出文档全文。
- 测试不联网、不加载 Docling 模型；真实 Docling 测试只在 `PK_RUN_DOCLING_TESTS=1` 时运行。
- 契约版本 v1.7，schema 版本 5。
- 提交：只有用户同意后才提交，在 `feat/docling-parsing` 分支上提交，不 push。下文的 "Checkpoint" 指运行门禁命令，不代表提交。

## Review Focus

1. **中文文件名导出**：`年报-2024.pdf` 导出为 `年报-2024.md`，使用 RFC 5987 `filename*=UTF-8''…`，不能抛出 latin-1 编码错误。测试放在 Task 7。
2. **解析排队**：第一份 PDF 正在解析时上传第二份，第二份必须保持 `queued`，不能提前进入 `parsing`。测试放在 Task 6。
3. **子进程超时后恢复**：一份文档超时被杀掉后，下一份文档仍能正常解析。测试放在 Task 5。
4. **解析中途删除文档**：不写入 chunk，不留下产物目录或临时目录。测试放在 Task 6。
5. **缺少页尺寸或 bbox 的元素**：页尺寸为 0、缺失，或 prov 没有 bbox 时，该元素的 `bbox=None`，不报错。测试放在 Task 3。

---

### Task 1: 契约、配置、Schema v5、依赖

**Files:**
- Modify: `backend/pyproject.toml`、`backend/uv.lock`（执行 `uv add` / `uv remove`）
- Modify: `backend/app/config.py`、`backend/app/schemas.py`、`backend/app/db.py`、`backend/.env.example`
- Modify: `docs/contracts.md`（v1.7：§2 错误码，§3.1 接口，§5.2 工具 location 说明，§8 变更记录）、`docs/data-model.md`
- Modify: `frontend/src/api/types.ts`（只同步类型，UI 在 Task 9）
- Test: `backend/tests/test_data_model.py`、`backend/tests/test_platform.py`

**Interfaces — Produces:**
- `app.schemas`: `ParseMode = Literal["standard", "enhanced"]`；`class SourceElement(BaseModel): ref: str; label: str; page: int | None = None; bbox: list[float] | None = None`；`Location.elements: list[SourceElement] = []`；`Location.from_row` 读取 `row["elements"]`（JSON）；`DocumentOut` 新增 `parse_mode: ParseMode`、`parser: str | None`、`page_count: int | None`；`class ReparseRequest(BaseModel): parse_mode: ParseMode | None = None`。
- `Location.label`：`pdf_page` 且 `table` 不为空时，返回 `"<标题路径> › 表 {table} 第 N 页"`（跨页时为 `第 a–b 页`）。
- `app.config.Settings` 新增：`parse_timeout: float = Field(1800, gt=0)`、`chunk_tokenizer: str = "BAAI/bge-m3"`、`chunk_max_tokens: int = Field(512, ge=64, le=8192)`、`docling_artifacts_path: str = ""`、`docling_device: Literal["auto","cpu","mps","cuda"] = "auto"`、`docling_threads: int = Field(4, ge=1, le=64)`、`docling_ocr_engine: Literal["rapidocr","easyocr","tesseract","ocrmac"] = "rapidocr"`、`docling_force_full_page_ocr: bool = True`。
- `app.db`: `SCHEMA_VERSION = 5`，`_migrate_v5` 执行 spec §6.6 中的 4 条 `ADD COLUMN`。
- 新错误码：`document_busy`（409，retryable=True）、`markdown_not_available`（409）。

- [ ] **Step 1: 写失败测试**
  - `test_migrate_v4_to_v5_adds_columns`：先建好 v4 库并插入一个文档和一个 chunk，再执行 `migrate()`。断言 `user_version == 5`；文档 `parse_mode == "standard"`、`parser is None`、`page_count is None`；chunk `elements == "[]"`；向 `parse_mode` 写入 `'x'` 时抛出 `sqlite3.IntegrityError`。
  - `test_location_label_pdf_table`：`Location(kind="pdf_page", start=5, end=6, table=2, heading_path=["合并资产负债表"]).label == "合并资产负债表 › 表 2 第 5–6 页"`。
  - `test_location_elements_roundtrip`：`Location.from_row` 读取 `elements='[{"ref":"#/texts/1","label":"text","page":1,"bbox":[0.1,0.2,0.3,0.4]}]'` 后，`elements[0].bbox == [0.1, 0.2, 0.3, 0.4]`。
  - `test_settings_parse_defaults`：`Settings(_env_file=None)` 的默认值 `parse_timeout == 1800`、`chunk_max_tokens == 512`、`docling_ocr_engine == "rapidocr"`。
- [ ] **Step 2: 运行**：`uv run --directory backend pytest -q tests/test_data_model.py tests/test_platform.py`。预期上面 4 个新测试失败。
- [ ] **Step 3: 实现上面列出的接口**；在 `.env.example` 中加入 spec §6.9 的变量（带注释，不写真实值）；更新契约和数据模型文档；`types.ts` 增加 `ParseMode`、`SourceElement`、`Location.elements`、`DocumentOut` 的新字段。
- [ ] **Step 4: 依赖**：`uv add --directory backend "docling>=2.152,<3"`；`uv remove --directory backend pypdf python-docx`；`uv add --directory backend --dev pypdf python-docx`。
- [ ] **Step 5: 运行全部后端测试**：`uv run --directory backend pytest -q`。现有测试此时仍在使用 `app.services.ingestion` 里的 pypdf 和 python-docx（已是 dev 依赖），应全部通过。

### Task 2: Parser Adapter 基础、TextParser、测试注入

**Files:**
- Create: `backend/app/parsers/__init__.py`、`base.py`、`registry.py`、`text.py`
- Create: `backend/tests/fakes/parsers.py`、`backend/tests/conftest.py`
- Test: `backend/tests/test_parsers_text.py`

**Interfaces — Produces（`app.parsers.base`）:**
- `ParseRequest(path: Path, file_type: str, mode: ParseMode, output_dir: Path)`；`SourceElementData(ref: str, label: str, page: int | None, bbox: tuple[float, float, float, float] | None)`；`ParsedChunk(content, kind, start, end, table=None, heading_path=(), char_start=None, char_end=None, elements: tuple[SourceElementData, ...] = ())`；`ParseResult(chunks: list[ParsedChunk], parser: str, page_count: int | None, has_markdown: bool, has_structure: bool)`。以上全部是 `@dataclass(frozen=True)`，可以 pickle。
- `class ParseError(Exception)`：`str(exc)` 是面向用户的中文消息。
- `class Parser(Protocol)`：`def parse(self, request: ParseRequest) -> ParseResult`；`def exclusive(self) -> AbstractContextManager[None]`（TextParser 返回 `nullcontext()`）。
- `MAX_ELEMENTS = 64`；`def limit_elements(items: Iterable[SourceElementData]) -> tuple[SourceElementData, ...]`：按 `(ref, page)` 去重并截断，超出时 `logger.warning` 记录被丢弃的数量。
- `app.parsers.registry`：`class ParserRegistry: def __init__(self, parsers: dict[str, Parser])`、`def for_type(self, file_type: str) -> Parser`（未知类型抛 `ParseError("不支持的文档格式")`）、`def close(self) -> None`（对实现了 `close` 的解析器逐个调用）；`def build_registry(settings: Settings) -> ParserRegistry`（txt → `TextParser()`，pdf/docx → Task 5 的 `DoclingParser`；在 Task 5 之前先只注册 txt）。
- `app.parsers.text.TextParser`：沿用现有 `read_text` 和 `build_chunks` 的行为，常量 `MAX_CHUNK_CHARS=800`、`SPLIT_OVERLAP=200`；把原文写入 `output_dir/document.md`；`elements` 每行一项（`ref=f"line:{n}"`、`label="text"`）；返回 `parser="text"`、`page_count=None`、`has_markdown=True`、`has_structure=False`。编码无法识别时抛 `ParseError("无法识别文本编码，请转换为 UTF-8 或 GBK 后重新上传")`。
- `tests/fakes/parsers.py`：`LegacyPdfDocxParser` 用 pypdf / python-docx 复现现有 `parse_blocks` 中 PDF 和 DOCX 两个分支的定位方式（逻辑从 `ingestion.py` 搬过来），写出 `document.md`（各块用 `\n\n` 拼接），`elements` 为空，`parser="legacy-test"`，`page_count` 为 PDF 页数；`def test_registry() -> ParserRegistry`。
- `tests/conftest.py`：autouse fixture，用 monkeypatch 把 `app.parsers.registry.build_registry` 替换成返回 `test_registry()`。带 `@pytest.mark.real_parsers` 的测试跳过这一替换。在 `pyproject.toml` 的 pytest 配置里登记 `markers = ["real_parsers: …"]`。

- [ ] **Step 1: 写失败测试** `test_parsers_text.py`：
  - `test_text_parser_lines_and_markdown`：输入 GBK 编码的 3 行文本（含一个空行）；断言 `kind=="txt_lines"`、只有一个 chunk 且 `start,end == 1,3`；`document.md` 内容等于解码后的原文；`elements` 的 ref 依次为 `line:1`、`line:3`。
  - `test_text_parser_long_line_windows`：输入一行 2000 字；断言各 chunk 的 `(char_start, char_end)` 为 `(0,800)`、`(600,1400)`、`(1200,2000)`。
  - `test_text_parser_bad_encoding`：输入 `b"\xff\xfe\x00\xd8"`，抛出 `ParseError`。
  - `test_limit_elements_dedup_and_cap`：输入 70 个不同元素加 1 个重复元素，结果长度为 64。
- [ ] **Step 2: 运行**：`uv run --directory backend pytest -q tests/test_parsers_text.py`，预期 FAIL（模块不存在）。
- [ ] **Step 3: 实现上述接口**。
- [ ] **Step 4: 运行并确认通过。**

### Task 3: Docling → ParsedChunk 映射（纯函数）

**Files:**
- Create: `backend/app/parsers/docling_mapping.py`（只 import docling_core）
- Create: `backend/tests/fakes/tokenizer.py`
- Test: `backend/tests/test_docling_mapping.py`

**Interfaces — Consumes:** Task 2 中的 `ParsedChunk`、`SourceElementData`、`limit_elements`。**Produces:**
- `class MarkdownTableProvider(ChunkingSerializerProvider)`：`get_serializer(doc)` 返回 `ChunkingDocSerializer(doc=doc, table_serializer=MarkdownTableSerializer())`。
- `def build_chunker(tokenizer: BaseTokenizer) -> HybridChunker`：`HybridChunker(tokenizer=tokenizer, merge_peers=True, repeat_table_header=True, serializer_provider=MarkdownTableProvider())`。
- `def chunks_from_document(doc: DoclingDocument, chunker: HybridChunker, file_type: Literal["pdf","docx"]) -> list[ParsedChunk]`：规则按 spec §6.4。
  - PDF：`kind="pdf_page"`；`start/end` 取 elements 中的最小和最大页码；没有 prov 的 chunk 沿用上一个 chunk 的页码（第一个 chunk 就没有时用 1），并 `logger.warning` 计数。
  - 表格 chunk 的 `table` 是该表在 `doc.tables` 中的序号加 1。
  - DOCX：文本 chunk 用 `kind="docx_paragraphs"`，序号是该元素在 `doc.iterate_items()` 中所有非表格 `DocItem` 里的位置（从 1 开始）；表格 chunk 用 `kind="docx_table_rows"`，`start=1`、`end=table.data.num_rows`。
  - `content = chunk.text`；`heading_path = tuple(chunk.meta.headings or ())`；`char_start/char_end = None`。
- `def normalize_bbox(bbox: BoundingBox, width: float, height: float) -> tuple[float,float,float,float] | None`：先 `to_top_left_origin(height)`，再除以页宽和页高，裁剪到 [0,1]，`round(…, 4)`；宽或高 ≤ 0 时返回 None。
- `tests/fakes/tokenizer.py`：`class CharTokenizer(BaseTokenizer)`，字段 `max_tokens: int = 60`；`count_tokens = len`；`get_max_tokens` 返回 `max_tokens`；**`get_tokenizer()` 必须返回可调用对象 `len`**（semchunk 要求，已验证）。

- [ ] **Step 1: 写失败测试**。fixture 文档用 `DoclingDocument` 构造：两页，每页 600×800；`add_heading("合并资产负债表")`，prov 在 page 1，bbox 为 `BOTTOMLEFT l=50,t=780,r=300,b=760`；一段正文；一个 3×2 表格，prov 在 page 1，bbox 为 `l=50,t=600,r=550,b=400`；一段 260 字的正文，prov 有两条（page 1 和 page 2）。（构造方式参考计划作者在临时环境里验证过的写法：`TableData(num_rows, num_cols, table_cells=[TableCell(...)])`。）
  - `test_pdf_table_chunk`：表格 chunk 满足 `kind=="pdf_page"`、`table==1`、`(start,end)==(1,1)`；content 以 `"| 项目 | 2024 |"` 开头；`heading_path==("合并资产负债表",)`；`elements[0].bbox == (0.0833, 0.25, 0.9167, 0.5)`。
  - `test_pdf_cross_page_text`：跨页段落被拆成的每个 chunk 都满足 `(start,end)==(1,2)`，且 elements 同时含 page 1 和 page 2。
  - `test_missing_size_bbox_none`（Review Focus 5）：把 page 2 的 size 设为 0×0，page 2 元素的 `bbox is None`，不抛异常。
  - `test_docx_numbering`：同样的结构去掉 prov，按 `file_type="docx"` 处理。断言正文 chunk 为 `docx_paragraphs`、`start>=1`、所有 `elements[i].page is None`；表格 chunk 为 `docx_table_rows`、`table==1`、`(start,end)==(1,3)`。
  - `test_empty_document`：空文档返回 `[]`。
- [ ] **Step 2: 运行**：`uv run --directory backend pytest -q tests/test_docling_mapping.py`，预期 FAIL。
- [ ] **Step 3: 实现。**
- [ ] **Step 4: 运行并确认通过。**

### Task 4: Docling 子进程入口与 prefetch

**Files:**
- Create: `backend/app/parsers/docling_runner.py`、`backend/app/parsers/prefetch.py`
- Test: `backend/tests/e2e/test_docling_real.py`（模块级 `pytest.mark.skipif(os.getenv("PK_RUN_DOCLING_TESTS") != "1")`，并加 `real_parsers` 标记）

**Interfaces — Consumes:** Task 2 的数据类，Task 3 的 `build_chunker` 和 `chunks_from_document`。**Produces:**
- `@dataclass(frozen=True) class DoclingConfig`：字段与 Settings 对应，`artifacts_path: str`、`device: str`、`threads: int`、`ocr_engine: str`、`force_full_page_ocr: bool`、`tokenizer: str`、`max_tokens: int`；另有 `@classmethod from_settings(cls, settings) -> DoclingConfig`。**这个类放在 `app/parsers/base.py`**（Web 进程需要构造它）。
- `class DoclingRunner: def __init__(self, config: DoclingConfig)`、`def parse(self, request: ParseRequest) -> ParseResult`：
  - 按模式懒加载并缓存 `DocumentConverter`：`format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=...), InputFormat.DOCX: WordFormatOption()}`，PdfPipelineOptions 的取值见 spec §6.3。
  - OCR 引擎映射：`rapidocr → RapidOcrOptions(force_full_page_ocr=...)`、`easyocr → EasyOcrOptions`、`tesseract → TesseractCliOcrOptions`、`ocrmac → OcrMacOptions`。
  - 分词器：`HuggingFaceTokenizer(tokenizer=AutoTokenizer.from_pretrained(config.tokenizer), max_tokens=config.max_tokens)`，懒加载并缓存。
  - 输出：`doc.save_as_json(output_dir/"document.json")`；`(output_dir/"document.md").write_text(doc.export_to_markdown(), "utf-8")`。
  - `page_count`：PDF 为 `len(doc.pages)`，DOCX 为 None；`parser = f"docling {version('docling')} / {mode}"`。
  - chunks 为空时抛出 spec §6.4 规定的两种 `ParseError` 文案之一。转换失败（`ConversionStatus` 不是 SUCCESS / PARTIAL_SUCCESS，或抛出异常）时抛 `ParseError("文档解析失败，请检查文件是否损坏或加密")`。模型缺失或下载失败（`OSError` 或 HF 相关异常）时抛 `ParseError("解析模型不可用，请联网后重试或先运行 python -m app.parsers.prefetch")`。原始异常只写进 `logger.exception`。
- `def serve(conn: Connection, config: DoclingConfig) -> None`：子进程主循环。`recv()` 收到 `("parse", job_id, ParseRequest)` 后返回 `(job_id, True, ParseResult)` 或 `(job_id, False, message)`；收到 `("stop",)` 时退出。
- `prefetch.py` 的 `main()`：调用 `download_models(output_dir=artifacts_path or None, with_layout=True, with_tableformer=True, with_rapidocr=True, with_code_formula=False, with_picture_classifier=False, progress=True)`，并执行 `AutoTokenizer.from_pretrained(tokenizer)`。

- [ ] **Step 1: 写可选真机测试** `test_docling_real.py`，共两个用例：
  - `test_real_pdf`：用 `eval/build_corpus.py` 里现有的 PDF 生成函数（dev 依赖 pypdf）生成 `solar_pilot_report.pdf`，用普通模式解析。断言 `has_markdown`、`has_structure`、`page_count == 6`，且每个 chunk 的 elements 都至少有一个 bbox 不为 None。
  - `test_real_docx_table`：用 python-docx 生成一份含标题和 2×2 表格的 DOCX。断言存在 `kind=="docx_table_rows"` 的 chunk，其 content 含 `"| 项目 |"`，并且 `heading_path` 非空。
- [ ] **Step 2: 实现 `DoclingRunner`、`serve`、`prefetch`。**
- [ ] **Step 3: 真机验证（需要联网，只做一次）**：`uv run --directory backend python -m app.parsers.prefetch`，然后执行 `PK_RUN_DOCLING_TESTS=1 uv run --directory backend pytest -q tests/e2e/test_docling_real.py`，预期 PASS，并记录耗时。
- [ ] **Step 4: 门禁不受影响**：`uv run --directory backend pytest -q`，预期 PASS（真机测试被跳过）。

### Task 5: 解析子进程管理与 DoclingParser

**Files:**
- Create: `backend/app/parsers/worker.py`
- Modify: `backend/app/parsers/registry.py`（pdf/docx 注册为 `DoclingParser`）
- Create: `backend/tests/fakes/parser_worker.py`
- Test: `backend/tests/test_parser_worker.py`

**Interfaces — Produces:**
- `class ParserWorker: def __init__(self, target: str, config: DoclingConfig, timeout: float)`：`target` 的格式为 `"module:function"`，签名与 `serve` 相同。方法：`def run(self, request: ParseRequest) -> ParseResult`、`def lock(self) -> threading.Lock`、`def close(self) -> None`。
  - 子进程用 `multiprocessing.get_context("spawn")` 启动，`daemon=True`，懒启动。
  - 等待结果用 `poll(timeout)`。超时后 `terminate()`、`join(5)`，必要时 `kill()`，然后抛 `ParseError(f"解析超时（超过 {int(timeout)} 秒），可稍后重试")`。
  - 子进程 EOF 或异常退出时抛 `ParseError("解析进程异常退出，请重试")`，并用 `logger.error` 记录退出码。
  - 返回 `ok=False` 时抛 `ParseError(message)`。
  - `run` 本身不加锁。
- `class DoclingParser`（实现 `Parser`）：`__init__(self, worker: ParserWorker)`；`exclusive()` 返回 `worker.lock()`；`parse` 委托给 `worker.run`；`close` 调用 `worker.close`。
- `build_registry(settings)`：创建一个 `ParserWorker("app.parsers.docling_runner:serve", DoclingConfig.from_settings(settings), settings.parse_timeout)`，pdf 和 docx 共用这一个实例。**`worker.py` 和 `registry.py` 都不得 import docling_runner**，只传字符串。
- `tests/fakes/parser_worker.py`：
  - `serve_ok`：把 `"# ok"` 写入 `output_dir/document.md`，返回一个 `pdf_page` chunk。
  - `serve_sleep`：第一份请求 sleep 30 秒，之后的请求正常返回。用 `output_dir.parent` 下的标记文件判断是不是第一次。
  - `serve_crash`：调用 `os._exit(3)`。
  - `serve_error`：返回 `(job_id, False, "测试错误")`。

- [ ] **Step 1: 写失败测试**：
  - `test_worker_returns_result`（`serve_ok`）：返回一个 chunk，`document.md` 存在。
  - `test_worker_timeout_then_recovers`（Review Focus 3；`serve_sleep`，`timeout=1`）：第一次调用抛 `ParseError`，消息含"解析超时"；第二次调用成功；第一个子进程的 `is_alive()` 为 False。
  - `test_worker_crash`：抛 `ParseError("解析进程异常退出，请重试")`，再次调用时重新拉起子进程（`serve_crash` 每次都崩，所以两次都抛错，且都不会卡住）。
  - `test_worker_error_message`：`str(exc) == "测试错误"`。
  - `test_worker_close_no_orphans`：`close()` 之后子进程已退出。
  - `test_web_process_does_not_import_docling`：用 `subprocess.run([sys.executable, "-c", "import app.main, sys; print(any(m.split('.')[0] in ('docling','docling_core') for m in sys.modules))"], cwd=backend)`，输出为 `False`。
- [ ] **Step 2: 运行**：`uv run --directory backend pytest -q tests/test_parser_worker.py`，预期 FAIL。
- [ ] **Step 3: 实现。**
- [ ] **Step 4: 运行并确认通过**（单个测试不超过 10 秒）。

### Task 6: 入库流程改造（ingestion、产物、向量输入、检索文本）

**Files:**
- Modify: `backend/app/services/ingestion.py`（删除 pypdf 和 docx 的 import，以及 `parse_blocks`、`build_chunks`、`Block`、`Chunk`）
- Create: `backend/app/services/artifacts.py`
- Modify: `backend/app/services/embeddings.py`、`backend/app/services/retrieval.py`
- Test: `backend/tests/test_ingestion.py`（新建），并调整 `tests/test_retrieval.py` 中受签名影响的部分

**Interfaces — Consumes:** `ParserRegistry`、`Parser.exclusive`、`ParseError`、`ParseResult`。**Produces:**
- `app.services.artifacts`：`artifacts_root(settings) -> Path`（即 `settings.data_dir / "artifacts"`）；`final_dir(root, document_id) -> Path`；`new_staging_dir(root, document_id) -> Path`（创建 `{id}.tmp-{uuid4().hex}`）；`commit(staging: Path, final: Path) -> None`（旧目录先改名为 `{id}.old-{hex}`，再把 staging 改名为 final，最后删除旧目录）；`discard(path: Path) -> None`；`remove_documents(root, document_ids: Iterable[str]) -> None`（失败时 warning，不抛出）；`cleanup_staging(root) -> int`（删除 `*.tmp-*` 和 `*.old-*`）。
- `def ingest(db: Database, document_id: str, settings: Settings, parsers: ParserRegistry) -> None`，流程：
  1. 读取文档行；status 不是 `queued` 就直接返回。
  2. `with parser.exclusive():` 内部，用条件 UPDATE 把 `queued→parsing`，`rowcount` 为 0 就返回。
  3. 在 staging 目录中调用 `parse(ParseRequest(path, file_type, parse_mode, staging))`。
  4. 计算向量：输入为 `embedding_text(c.heading_path, c.content)`。
  5. 执行 `BEGIN IMMEDIATE`，检查文档还在：不在就 discard staging 并返回；在就替换 chunks（`elements` 用 `json.dumps([asdict(e) …], ensure_ascii=False)`，bbox 写成 list）和向量，并 `UPDATE documents SET status='success', parser=?, page_count=?`。
  6. 提交之后执行 `artifacts.commit(staging, final)`。
  - 出错时：`ParseError`、`AppError`、`ValueError` 的消息写入 `error`；其他异常 `logger.exception` 后写入通用文案。discard staging，status 改为 `failed`。
- `app.services.embeddings`：`def embedding_text(heading_path: Sequence[str], content: str) -> str`，返回 `"\n".join([*heading_path, content])`；`profile()` 的 identity 末尾追加 `"text:v2"`；`reindex` 改用 `embedding_text`，读取 `heading_path` 列。
- `app.services.retrieval`：lexical 打分的文本改为 `embedding_text(json.loads(row["heading_path"]), row["content"])`；`SearchHit.content` 保持原文不变。

- [ ] **Step 1: 写失败测试** `test_ingestion.py`（注入假 Parser 和 `FakeEmbeddings`）：
  - `test_ingest_success_persists_elements_and_artifacts`：假 Parser 返回一个带 bbox 的 chunk 并写出 md。断言 status 为 `success`、`parser`、`page_count`；`chunks.elements` 的 JSON 正确；`artifacts/{id}/document.md` 存在；没有 `*.tmp-*` 残留。
  - `test_ingest_embedding_input_has_headings`：FakeEmbeddings 收到的输入为 `"合并资产负债表\n收入 100"`。
  - `test_ingest_failure_keeps_old_artifacts`：先成功一次，再让 Parser 抛 `ParseError("x")`。断言 status 为 `failed`、`error=="x"`，旧的 `document.md` 仍在，且没有 staging 残留。
  - `test_second_document_stays_queued_while_first_parses`（Review Focus 2）：假 Parser 的 `exclusive()` 返回一把共享锁，`parse` 内部等待一个 `threading.Event`。线程 A 开始 ingest doc1 后，在线程 B 启动 ingest doc2，断言 doc2 的 status 仍为 `queued`。set event 后两个文档都变成 `success`。
  - `test_delete_during_parse_leaves_nothing`（Review Focus 4）：`parse` 内部直接执行 SQL 删除文档行。断言 chunks 为空，`artifacts/` 下没有该 id 的任何目录。
  - `test_cleanup_staging`：`cleanup_staging` 删除 `x.tmp-1` 和 `x.old-2`，保留 `x`。
  - `test_profile_includes_text_version`：签名与改动前（硬编码旧算法计算的值）不同。
- [ ] **Step 2: 运行**，预期 FAIL。
- [ ] **Step 3: 实现。**
- [ ] **Step 3b: 接线（为保持门禁通过，本 Task 内完成）**：在 `main.py` 中设置 `app.state.parsers = build_registry(settings)`，lifespan 退出时调用 `parsers.close()`；在 `deps.py` 中新增 `Parsers = Annotated[ParserRegistry, Depends(get_parsers)]`；`api/documents.py` 现有的两处 `ingest(...)` 调用改为传入 `parsers`。
- [ ] **Step 4: 运行全部后端测试**：`uv run --directory backend pytest -q`，预期 PASS（PDF/DOCX 经由 conftest 走 `LegacyPdfDocxParser`）。

### Task 7: HTTP 接口、删除清理、应用生命周期

**Files:**
- Modify: `backend/app/main.py`（`app.state.artifacts_root`；lifespan 中 `db.initialize()` 之后执行 `artifacts.cleanup_staging`）
- Modify: `backend/app/deps.py`（新增 `ArtifactsRoot` 依赖）
- Modify: `backend/app/api/documents.py`、`backend/app/services/documents.py`、`backend/app/services/folders.py`、`backend/app/services/knowledge_bases.py`、`backend/app/api/folders.py`、`backend/app/api/knowledge_bases.py`
- Test: `backend/tests/test_api.py`、`backend/tests/test_documents_manage.py`

**Interfaces — Produces:**
- 上传接口增加表单字段 `parse_mode: Annotated[str | None, Form()] = None`。空值按 `standard` 处理；非法值返回 422 `invalid_argument`（"解析模式只能是 standard 或 enhanced"）；TXT 一律记为 `standard`。`create_document(..., parse_mode: ParseMode)` 把它写入新增的列。
- `service.requeue_document(db, user_id, document_id, parse_mode: ParseMode | None) -> dict`：status 为 `success` / `failed` 时改为 `queued`，并更新 parse_mode（TXT 强制 `standard`，传 None 时沿用原值），清空 error；`queued` / `parsing` 时抛 `AppError(409, "document_busy", "文档正在解析，请稍后再试", retryable=True)`。
- 路由 `POST /api/documents/{id}/reparse`，请求体 `ReparseRequest`（允许空 body），返回 202 `DocumentOut`，并调度 `ingest(db, id, settings, parsers)`。
- 路由 `GET /api/documents/{id}/markdown`，服务函数为 `service.markdown_export(connection, user_id, document_id, artifacts_root) -> tuple[Path, str]`（第二项是下载文件名：原文件名去掉扩展名后加 `.md`；去扩展名后为空时用 `document.md`）。status 不是 success 时返回 `document_not_ready`；文件不存在时返回 409 `markdown_not_available`（"请重新解析后再导出"）。响应使用 `FileResponse(path, media_type="text/markdown; charset=utf-8", filename=name)`（Starlette 会自动生成 `filename*=`，需由测试确认）。
- 路由 `GET /api/documents/{id}/file`：`FileResponse(path, media_type=<pdf: application/pdf; docx: application/vnd.openxmlformats-officedocument.wordprocessingml.document; txt: text/plain; charset=utf-8>, content_disposition_type="inline", filename=row.filename)`；磁盘上没有文件时返回 404 `document_not_found`。
- `remove_stored_files(files, artifacts_root: Path)` 同时删除产物目录。`delete_document`、`delete_folder`、`delete_knowledge_base` 增加关键字参数 `artifacts_root: Path`，由路由传入。

- [ ] **Step 1: 写失败测试**（conftest 已把 PDF/DOCX 替换为 `LegacyPdfDocxParser`）：
  - `test_upload_parse_mode`：PDF 带 `parse_mode=enhanced` 时返回体中 `parse_mode=="enhanced"`；`bogus` 返回 422；TXT 带 `enhanced` 时记为 `standard`。
  - `test_reparse_flow`：解析成功后 reparse `{"parse_mode":"enhanced"}` 返回 202，最终为 success 且 `parse_mode=="enhanced"`；把状态直接写成 `parsing` 再 reparse，返回 409 `document_busy` 且 `retryable` 为 true；空 body 时沿用原模式。
  - `test_markdown_export_chinese_filename`（Review Focus 1）：上传 `年报-2024.pdf`，GET markdown 返回 200，`content-type` 以 `text/markdown` 开头，`content-disposition` 含 `filename*=UTF-8''%E5%B9%B4%E6%8A%A5-2024.md`，响应体非空。
  - `test_markdown_not_available`：删掉产物目录后返回 409 `markdown_not_available`；文档为 `failed` 时返回 409 `document_not_ready`。
  - `test_original_file_inline`：返回 200、`content-type: application/pdf`、`content-disposition` 以 `inline` 开头，字节与上传内容一致。
  - `test_delete_removes_artifacts`：分别删除文档、递归删除目录、删除知识库，三种情况下 `artifacts/{id}` 都不存在。
  - 调整 `test_docx_and_pdf` 等现有测试中的断言（新增字段 `parse_mode`、`parser=="legacy-test"`）。
- [ ] **Step 2: 运行**，预期新增测试 FAIL。
- [ ] **Step 3: 实现。**
- [ ] **Step 4: 运行全部后端测试和 ruff**：执行 AGENTS.md §2 中的前三条命令，预期 PASS。

### Task 8: 工具和 MCP 中模型可见的 location 去掉 elements

**Files:**
- Modify: `backend/app/tools/builtin.py`（`SearchChunksTool.run`、`ReadDocumentTool.run`）
- Test: `backend/tests/test_tools.py`、`backend/tests/test_mcp.py`、`backend/tests/test_conversations.py`

**Interfaces — Produces:** `builtin.py` 中新增 `def model_location(location: Location) -> dict`，返回 `location.model_dump(exclude={"elements"})`，`label` 照常保留。两个工具输出的 `data` 都改用它。`CitationSource.location` 继续使用完整的 `location.model_dump()`。

- [ ] **Step 1: 写失败测试**：
  - `test_search_hits_hide_elements`：把一个 chunk 的 elements 写成带 bbox 的值，`search_chunks` 返回的 `data.hits[0].location` 里没有 `"elements"` 键，但有 `label`。
  - `test_read_document_hides_elements`：同理。
  - `test_citation_snapshot_keeps_elements`：使用 ScriptedLLM 运行一次带引用的回答，`GET /api/conversations/{id}` 返回的 citation 中 `location.elements[0].bbox` 存在。
  - `test_mcp_search_hides_elements`：MCP 的 `search_chunks` 结果里同样没有 elements。
- [ ] **Step 2–4: 运行（预期 FAIL）→ 实现 → 运行（预期 PASS）。**

### Task 9: 前端 P1

**Files:**
- Modify: `frontend/src/api/documents.ts`、`frontend/src/api/client.ts`（如果需要支持二进制响应）、`frontend/src/features/files/FilesView.tsx`、`frontend/src/features/files/FileTable.tsx`
- Test: `frontend/src/features/files/FileTable.test.tsx`、`frontend/src/api/client.test.ts`

**Interfaces — Produces:**
- `uploadDocument(kbId, file, folderId, parseMode: ParseMode = 'standard')`，非 `standard` 时才追加表单字段 `parse_mode`。
- `reparseDocument(id: string, parseMode?: ParseMode): Promise<DocumentOut>`。
- `exportMarkdown(id: string): Promise<{ blob: Blob; filename: string }>`：带上 `X-User-Id` 请求头，从 `content-disposition` 的 `filename*` 解出文件名；错误按契约中的 JSON 错误处理。
- FilesView：上传按钮旁加一个 shadcn `Select`，选项为"普通模式 / 增强模式（OCR）"，默认普通，作用于本次上传的所有文件；导出时用 Blob 生成 object URL，触发下载后 revoke。
- FileTable：
  - 类型列显示 `PDF · 12 页`（`page_count` 不为 null 时）。
  - `parse_mode==='enhanced'` 时显示 `Badge`"增强"。
  - 更多菜单增加："导出 Markdown"（仅 success）、"重新解析"（success 或 failed）。PDF 的重新解析分两项："普通模式重新解析" 和 "增强模式（OCR）重新解析"。
  - 回调 props 新增 `onReparse(doc, mode)` 和 `onExport(doc)`。
  - 按 AGENTS.md 先用 shadcn Skill / `npx shadcn@latest docs select` 查找组件；不硬编码颜色。

- [ ] **Step 1: 写失败测试**（vitest）：
  - `FileTable` 渲染 PDF 文档（`page_count: 12, parse_mode: 'enhanced'`）时出现文本 `PDF · 12 页` 和 `增强`。
  - 在菜单中点击"增强模式（OCR）重新解析"后，`onReparse` 以 `(doc, 'enhanced')` 被调用；TXT 行没有增强选项；`queued` 行没有"重新解析"。
  - `exportMarkdown` 能从 `content-disposition: attachment; filename*=UTF-8''%E5%B9%B4%E6%8A%A5.md` 中解出 `年报.md`。
- [ ] **Step 2: 运行** `npm --prefix frontend test`，预期 FAIL。
- [ ] **Step 3: 实现。**
- [ ] **Step 4: 运行** `npm --prefix frontend test && npm --prefix frontend run build`，预期 PASS。

### Task 10: 文档、数据重置、全量验证

**Files:**
- Modify: `README.md`（安装体积、首次联网与 prefetch、两种模式、Markdown 导出）、`docs/backend.md`（解析架构与配置）
- 删除（用户已批准）：`backend/data/knowledge.db`（及 `-wal` / `-shm`）、`backend/data/uploads/`

- [ ] **Step 1: 写文档**，包括 spec §11 的风险与限制，以及 Task 4 Step 3 实测得到的耗时。
- [ ] **Step 2: 数据重置**：先 `ls -la backend/data backend/data/uploads`，确认就是之前告知用户的那几项（knowledge.db 约 2.7MB、2 个上传文件），然后删除。下次启动时会自动按 schema v5 建库。
- [ ] **Step 3: 全量门禁**：执行 AGENTS.md §2 的 6 条命令，全部 PASS。
- [ ] **Step 4: 端到端冒烟**：通过 `.claude/launch.json` 启动后端和前端（使用 preview 工具），上传一份含表格的 PDF。确认它从排队、解析中变为成功；导出的 Markdown 含表格；`/chunks` 返回的 elements 带 bbox；在问答中点击引用，能看到原文片段和"表 N 第 X 页"标签（前提是已配置模型，未配置则只验证检索）。
- [ ] **Step 5: 交付报告**：按 AGENTS.md §5 的格式交付，并请用户决定是否提交。
