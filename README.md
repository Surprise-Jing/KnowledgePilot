# KnowledgePilot

Agentic Personal Knowledge Workspace（个人知识管理智能助手）：面向复杂知识任务的 Agent 工具调用与证据管理——多知识库与目录管理、文档解析入库、检索、多文档对比、引用聚合、多轮对话、流式回答与可追溯调用轨迹。

完整需求见 [功能需求](docs/README.md)，开发计划见 [任务与编排](docs/tasks.md)。

## 当前状态

P0 主流程已完成，P1 已实现 MCP＋Skill、工具失败降级、评测集、多文档对比和引用聚合。查询顺序与任务拆解继续由模型自主决定，暂不增加独立规划器。

基础流程已有真实模型与 BGE-M3 端到端验证。新增复杂任务使用易方达四份官方原始季报构建 24 题评测集；当前已用真实 Docling 解析、混合检索和聊天模型跑通双基金对比、四报告引用聚合两道冒烟题，尚未完成全部 24 题的真实模型评测。实现与验证边界见 [多文档对比交付记录](docs/reviews/2026-11-24-multidoc-citations.md)。

### 已实现

**知识库、目录与文件**
- 用户名 + 密码注册登录，会话保存在 HttpOnly Cookie 中；所有数据按用户隔离，访问他人数据返回“不存在”。账号管理（为老用户设置密码、删除用户）用 `uv run --directory backend python -m app.users`，见 [docs/backend.md](docs/backend.md#账号与登录)。
- 知识库创建、列表、重命名、删除（同时清理原文件）。
- 任意层级目录：创建、重命名、移动（防止移动成环）、删除（非空目录需确认递归删除）。
- 文件上传到指定目录、按目录列出、重命名、在同一知识库内移动、删除；删除操作幂等。

**解析入库**
- 支持 TXT（UTF-8 / GBK）、PDF、DOCX；校验格式、大小（默认 10 MiB，财报等大文件可调大 `PK_MAX_UPLOAD_BYTES`）和空文件。
- PDF/DOCX 使用 [Docling](https://github.com/docling-project/docling) 结构化解析（版面分析 + 表格结构识别），TXT 使用轻量解析器，统一经过 Parser Adapter 层（`backend/app/parsers/`）。PDF 有两种模式，上传时选择，也可对已解析文档切换模式重新解析：
  - **普通模式**：版面分析 + 表格结构识别，关闭 OCR、公式增强和图片描述。
  - **增强模式**：另开 OCR（默认 RapidOCR），用于扫描件或复杂文档；更慢。
- Docling HybridChunker 按文档结构分块，块大小以 BGE-M3 分词器计数（默认 512 token），表格以 Markdown 表格保存，拆块时重复表头。
- 每个片段记录来源元素（Docling 元素引用、页码、归一化坐标框），引用可回溯到原始 PDF 页面区域；为后续“跳页 + 高亮”预留数据和原文件接口。
- 保存 Markdown 和 Docling 结构化 JSON 解析产物，文件列表可“导出 Markdown”。
- 同一知识库内按内容（SHA-256）去重，重复上传返回已有记录并标记 `duplicate`。
- 异步解析，状态为排队、解析中、成功、失败；失败显示原因并可重试；重启后中断的任务标记为失败，不会卡在中间状态。
- 按格式切片并记录结构化位置（行号、页码、标题路径、表格序号与来源元素），可直接定位原文。

**检索**
- 按一个或多个知识库检索，返回内容、来源、位置和相关度，去重排序。
- 支持词频余弦基线、BM25、BGE-M3 稠密向量和 BM25＋向量 RRF 混合检索（`PK_RETRIEVER`）。
- HTTP 搜索和 `search_chunks` 支持可选 `document_ids`，在召回和截取 Top-K 前限定文档。指定多份文档时分别保留候选、按文档轮流返回结果，避免一份文档占满对比依据。
- 同文档内去重，不因文本相同丢弃另一份报告的来源；工具返回各目标文档的本次覆盖条数，未覆盖时可继续定向检索或读取原文。

**工具与 Agent 对话**
- 4 个工具：`list_knowledge_bases`、`search_chunks`、`list_files`、`read_document`，带参数校验、结构化错误、权限与知识库范围检查。
- 模型自主选择工具，限制最大步数；工具错误回传给模型；模型异常、超时有明确提示。
- 多轮对话与指代消解（如“这份文档”），按字符预算管理上下文。
- 通过有长度限制的本轮来源索引复用文档 ID 与引用标记；旧工具结果压缩时保留少量分文档原文摘录，减少重复查找。
- SSE 流式回答，工具调用轨迹实时推送；引用只接受本次工具实际返回的片段，编造的引用会被丢弃并记录。
- 会话、消息、调用轨迹、引用快照持久化。

**前端**（Vite + React + shadcn/ui）
- 登录/注册与账号菜单、知识库与目录树、文件管理与解析状态、对话（多选知识库、流式回答、停止）、引用面板（查看原文）、工具轨迹面板。
- 回答中的连续引用聚合为可展开的来源组；引用面板将同文档相邻位置分组，保留各条原文快照和独立定位，历史回答同样可用。

**多文档对比与引用聚合**（P1）
- 按相同维度比较不同文档，分别标注依据，说明共同点、差异、冲突和信息缺失；比较前区分日期、版本、单位与分母。
- 一条结论可关联多份来源，展开后逐项查看原文。相同文本来自不同文档时保留独立来源；引用分组不代表系统已证明结论的语义正确性。

**工程基础**
- 分层：路由 → 服务 → 数据库；工具层与 Agent 只调用服务层。
- 统一错误结构 `{"error": {"code", "message", "retryable", "details"}}`；JSON 结构化日志，带请求 ID 与提问 ID。
- SQLite 持久化与版本迁移（schema v5）；多文档对比与引用聚合没有新增迁移。
- 当前门禁：后端 403 项通过、3 项真实解析测试按需跳过，前端 82 项通过。默认自动化测试不依赖网络和模型密钥，真实服务冒烟单独运行。

**工具失败降级**（P1）
- 可重试的工具错误自动重试一次（每次提问最多 3 次）；向量服务故障时检索自动改用 BM25；读原文失败时改用本次已检索到的片段；模型在输出前出错时重试一次。
- 降级在轨迹中标出，回答下方提示“回答可能不完整”，模型会说明哪些内容依据有限。

**MCP＋Skill**（P1）
- 自带 stdio MCP Server（服务名 `knowledgepilot`），与应用内 Agent 共用同一工具注册表，参数、错误码、权限和降级完全一致。
- 启动：`PK_MCP_USERNAME=demo uv run --directory backend python -m app.mcp_server`（通常由 MCP 客户端自动拉起）。
- Claude Code：`claude mcp add --transport stdio --env PK_MCP_USERNAME=demo knowledgepilot -- uv run --directory <仓库绝对路径>/backend python -m app.mcp_server`。
- 不需要 AI 客户端的调用示例：`uv run --directory backend python ../skills/knowledgepilot/scripts/demo_client.py --query "删除文档时如何清理索引"`。
- Skill 包与更多客户端配置见 [skills/knowledgepilot/](skills/knowledgepilot/)。MCP Server 启动时只做数据库迁移，可与 Web 应用同时运行。

**评测**（P1 与算法拓展）
- 原有回归集：自编虚构语料 5 份（TXT/DOCX/PDF，3 个知识库）、20 条用例，覆盖单库、多库、多轮指代、工具选择、引用定位、无结果和异常。
- 原工程基线的检索对照（Hit@5 / MRR）：cosine 0.769 / 0.731，BM25 0.769 / 0.731，向量 1.000 / 0.962，混合 1.000 / 0.840。该次 Agent 混合检索结果为 20/20 通过、引用正确率 0.793，属于结构化解析升级前的历史结果，见 [原基线报告](docs/eval-report.md)。
- 真实复杂任务集：易方达上证50ETF（510100）、沪深300ETF（510310）的 2024Q4/2025Q1 官方季报，共 4 份、58 页；24 题、137 个核验要点、142 条事实证据和 20 项计算，划分为 16 道开发题、8 道保留测试题。
- 覆盖跨期变化、份额合并、持仓交集、同结论多来源和金额/份额/分母口径辨析。保存官方链接、文件哈希、页码与原文锚点，原始 PDF 按需下载到忽略目录。
- 自动数值和证据覆盖检查与语义核查分开；规则通过不等于最终语义正确。来源、题目和运行方法见 [复杂任务评测集](eval/finance/README.md)。

### 已知限制

- 用户身份由请求头声明，不做认证，只适合本地演示。
- PDF/DOCX 在独立的解析子进程中串行执行（一次一份，其余排队），超过 `PK_PARSE_TIMEOUT`（默认 30 分钟）会终止并标记失败；重启时中断的任务标记为失败、需手动重试。工具超时后后台线程无法强制终止。
- Docling 及其依赖（torch 等）使安装体积增加约 1 GB，模型约 0.7 GB；首次解析需联网下载模型，或先执行 prefetch（见下文）。解析速度取决于页数：在 Apple M5 上，一份 91 页的财报普通模式约 1 分钟（含向量生成），增强模式（OCR）约 6 分钟。
- 重新解析期间文档不可检索；DOCX 没有页面坐标，只能定位到元素和标题路径；高亮精度为元素级（如整张表），不到单元格。
- 文件只能在同一知识库内移动；不支持旧版 `.doc`。
- TXT 约 800 字合并为一个片段，引用只能定位到行范围；混合检索的 RRF 会压低只被一路召回的结果，在原 20 题检索基线上纯向量检索更优。
- 检索未覆盖不代表文档未提及；`top_k` 是总返回上限，过小可能无法覆盖全部目标文档。引用有效性检查验证来源，结论是否被原文充分支持仍需核对。
- 没有独立查询规划器；模型自主拆解与调度。后续可针对依赖型问题、覆盖缺口补查和追问改写做对照实验，再决定是否增加显式规划阶段。

## 快速开始

前置条件：[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Node.js（含 npm）。先按下文配置模型，然后在项目根目录执行：

```bash
./start.sh
```

脚本会安装前端依赖（首次）、构建前端，再启动服务。打开 <http://127.0.0.1:8000> 使用；接口文档在 <http://127.0.0.1:8000/docs>。首次启动会自动安装 Python 和依赖，需要联网。端口可用 `PORT=9000 ./start.sh` 修改。

前端开发时可分别启动后端（`uv run --directory backend uvicorn app.main:app --port 8000`）和前端（`npm --prefix frontend run dev`，地址 <http://127.0.0.1:5173>，接口自动代理到 8000）。

### 解析模型（Docling）

PDF/DOCX 解析所需的 Docling 版面/表格/OCR 模型和 BGE-M3 分词器会在首次解析时自动下载（需联网）。网络不稳定或需离线运行时，先执行一次：

```bash
uv run --directory backend python -m app.parsers.prefetch
```

模型默认下载到 `~/.cache/docling/models`，之后解析直接从本地加载、不再访问 Hugging Face。也可用 `PK_DOCLING_ARTIFACTS_PATH` 指定目录。其他解析配置（超时、分块大小、OCR 引擎、设备）见 `backend/.env.example`。

### 配置模型

将 `backend/.env.example` 复制为 `backend/.env`，聊天模型填写以下三项：

```bash
PK_LLM_BASE_URL=https://你的兼容服务地址/v1
PK_LLM_MODEL=支持工具调用的模型名
PK_LLM_API_KEY=你的密钥
```

模型服务需兼容 OpenAI Chat Completions 并支持工具调用。`.env` 已被 Git 忽略，不要提交密钥。其余配置都有默认值，见 [后端开发说明](docs/backend.md#启动与配置)。

### 配置 BGE-M3 与混合检索

在 `backend/.env` 配置独立的 Embedding 服务：

```dotenv
PK_RETRIEVER=hybrid
PK_EMBEDDING_BASE_URL=https://你的兼容服务地址/v1
PK_EMBEDDING_MODEL=bge-m3
PK_EMBEDDING_API_KEY=你的密钥
PK_EMBEDDING_DIMENSIONS=1024
```

程序追加 `/embeddings`；免认证的本地服务可留空密钥。`vector` 仅使用向量，
`hybrid` 使用 BM25 + 稠密向量 RRF 融合，`bm25` 与默认 `cosine` 不调用 Embedding。
向量和原文片段一起持久化到 `backend/data/knowledge.db`，原文件仍在 `uploads/`。
当前为精确向量扫描，适合本地小规模数据，无需独立向量数据库服务。

已有文档切换到 vector/hybrid 后需补建向量；先停止应用，再为每个用户执行：

```bash
uv run --directory backend python -m app.reindex --username demo
```

更换 Embedding 服务、模型或维度后也需重建；同名模型更新权重时提升
`PK_EMBEDDING_REVISION`。重建不改变片段 ID；模型故障会明确报错，不静默退回关键词检索。

## 测试

```bash
uv run --directory backend pytest -q
uv run --directory backend ruff check app tests
uv run --directory backend ruff format --check app tests
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run lint
```

真实模型冒烟（需先启动服务并配置 `backend/.env`）：`uv run --directory backend python ../scripts/smoke.py --base-url http://127.0.0.1:8000 --repo-root "$PWD"`。

### 复杂知识任务与评测

使用界面时，先上传报告并选择相关知识库，在问题中明确文件名、基金代码或报告期，例如：

- “比较两只基金 2025 年一季度收益及超越各自基准的幅度，分别引用两份报告。”
- “四份季报是否均主要采用完全复制法？用一条综合结论回答，并保留四份来源。”
- “比较同一基金两个季末的份额变化，区分申购、赎回与份额合并调整。”

回答中的来源组可展开查看文件和位置，点击某项在引用面板查看对应原文。

从仓库根目录运行评测；脚本创建独立临时知识库，不改动现有用户数据：

```bash
# 无需网络：校验评测结构、范围与计算标注
uv run --directory backend python ../eval/finance/run.py

# 首次下载官方 PDF，并核验哈希、页数和事实锚点
uv run --directory backend python ../eval/finance/run.py --fetch --verify-sources

# 真实 Docling + 已配置的模型服务：复现两道冒烟题
uv run --directory backend python ../eval/finance/run.py \
  --mode agent --retriever hybrid --env-file backend/.env --only CMP01,AG01

# 后续开发使用开发集，保留测试集用于最终对照
uv run --directory backend python ../eval/finance/run.py \
  --mode agent --retriever hybrid --env-file backend/.env --split dev
```

首次真实解析需准备上文的 Docling 模型；Agent 评测会调用配置中的聊天与 Embedding 服务。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/README.md](docs/README.md) | 功能需求与验收清单 |
| [docs/backend.md](docs/backend.md) | 后端启动、接口示例、当前边界与选型理由 |
| [docs/data-model.md](docs/data-model.md) | 数据模型与迁移 |
| [docs/contracts.md](docs/contracts.md) | 共享契约（即 API 文档）：接口、错误码、SSE 事件、工具定义、Agent 循环、降级、MCP |
| [docs/eval-report.md](docs/eval-report.md) | 评测报告：语料、20 条用例、检索策略对照、Agent 结果与失败分析 |
| [eval/finance/README.md](eval/finance/README.md) | 易方达真实复杂任务评测：官方来源、题目、证据账本、指标与复现命令 |
| [docs/reviews/2026-11-24-multidoc-citations.md](docs/reviews/2026-11-24-multidoc-citations.md) | 多文档对比与引用聚合实现、真实模型冒烟与查询规划取舍 |
| [skills/knowledgepilot/](skills/knowledgepilot/) | Skill 包：`SKILL.md`、MCP 客户端配置、调用示例 |
| [docs/tasks.md](docs/tasks.md) | 开发计划、文件所有权、阶段安排 |
| [docs/briefs/](docs/briefs/) | 前端、测试等任务说明 |
| [AGENTS.md](AGENTS.md) | 参与开发的 Agent 必须遵守的规则 |

## 项目结构

```text
backend/
  app/
    main.py            应用装配：错误处理、日志中间件、路由
    api/               HTTP 路由
    services/          解析入库、权限校验、检索、回答
    llm/base.py        模型客户端接口
    db.py              表结构与迁移
    schemas.py         请求、响应和事件模型
  tests/               自动化测试
docs/                  需求、契约、计划与设计文档
examples/              演示资料
eval/finance/          真实报告的复杂任务评测与证据标注
```
