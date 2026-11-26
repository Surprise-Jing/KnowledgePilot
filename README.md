# KnowledgePilot

KnowledgePilot 是一个本地运行的个人知识管理智能助手：多知识库与目录管理、PDF / Word / TXT 解析入库、混合检索，由大模型自主调用工具取证，流式输出带引用的回答，并完整展示工具调用轨迹。同一套知识库工具通过本地 stdio MCP Server 与 Skill 包暴露给外部 AI 客户端。

## 功能

**知识库、目录与文件**
- 用户名 + 密码注册登录，会话保存在 HttpOnly Cookie 中；数据按用户隔离，访问他人数据返回“不存在”。
- 知识库创建、列表、重命名、删除（同时清理原文件）。
- 任意层级目录：创建、重命名、移动（防止成环）、删除（非空目录需确认递归删除）。
- 文件上传到指定目录、按目录列出、重命名、在同一知识库内移动、删除；删除幂等。

**解析入库**
- 支持 TXT（UTF-8 / GBK）、PDF、DOCX；校验格式、大小（默认 10 MiB，可通过 `PK_MAX_UPLOAD_BYTES` 调整）和空文件。
- PDF/DOCX 使用 [Docling](https://github.com/docling-project/docling) 结构化解析（版面分析 + 表格结构识别），在独立子进程中执行。PDF 支持两种模式，上传时选择，也可对已解析文档切换后重新解析：
  - **普通模式**：版面分析 + 表格结构识别，不做 OCR。
  - **增强模式**：另开 OCR（默认 RapidOCR），适用于扫描件或复杂版面，速度较慢。
- 按文档结构分块（BGE-M3 分词器计数，默认 512 token），表格以 Markdown 表格保存并在拆块时重复表头。
- 每个片段记录结构化来源位置（页码、行号、段落、表格、标题路径）与来源元素坐标，引用可回溯到原始 PDF 页面区域。
- 同一知识库内按内容（SHA-256）去重，重复上传返回已有记录。
- 异步解析，状态为排队、解析中、成功、失败；失败显示原因并可重试；服务重启后中断的任务标记为失败，不会停留在中间状态。
- 保存 Markdown 与结构化 JSON 解析产物，可导出 Markdown。

**检索**
- 按一个或多个知识库检索，返回内容、来源、位置和相关度，去重排序。
- 四种策略可切换（`PK_RETRIEVER`）：词频余弦、BM25、BGE-M3 稠密向量、BM25 + 向量 RRF 混合。
- 可限定文档检索；指定多份文档时按文档分别保留候选、轮流返回结果，避免一份文档占满结果。

**工具与 Agent 对话**
- 4 个工具：`list_knowledge_bases`、`search_chunks`、`list_files`、`read_document`，带参数校验、结构化错误、权限与范围检查。
- 模型自主选择工具，限制最大步数；工具错误回传给模型；模型异常与超时有明确提示。
- 多轮对话与指代消解（如“这份文档”），按字符预算管理上下文；会话焦点与本轮来源索引帮助复用已找到的文档与引用。
- 提问时可选择自动查找资料，或指定知识库、目录、文档作为范围；范围由服务端强制执行。
- SSE 流式回答，工具调用轨迹实时推送；引用只接受本次工具实际返回的片段，编造的引用会被丢弃并记录。
- 会话、消息、调用轨迹、引用快照持久化。

**多文档对比与引用聚合**
- 按相同维度比较不同文档，分别标注依据，说明共同点、差异、冲突和缺失；比较前区分日期、版本、单位与分母。
- 回答中连续引用聚合为可展开的来源组；引用面板将同文档相邻位置分组，保留各条原文快照与独立定位。

**工具失败降级**
- 可重试的工具错误自动重试一次（每次提问最多 3 次）；向量服务故障时检索改用 BM25；读取原文失败时改用本次已检索到的片段；模型在输出前出错时重试一次。
- 降级在轨迹中标出，回答下方提示“回答可能不完整”，模型说明哪些内容依据有限。

**MCP + Skill**
- stdio MCP Server（服务名 `knowledgepilot`）与应用内 Agent 共用同一工具注册表，参数、错误码、权限和降级完全一致。
- Skill 包见 [skills/knowledgepilot/](skills/knowledgepilot/)，含 `SKILL.md`、客户端配置与调用示例。

**前端**（Vite + React + shadcn/ui）
- 登录注册与账号管理、知识库与目录树、文件管理与解析状态、对话（资料范围选择、流式回答、停止生成）、引用面板（原文快照、PDF 跳页高亮）、工具轨迹面板。

**工程**
- 分层：路由 → 服务 → 数据库；工具层、Agent 与 MCP 只调用服务层。
- 统一错误结构 `{"error": {"code", "message", "retryable", "details"}}`；JSON 结构化日志，带请求 ID 与提问 ID。
- SQLite 持久化与版本迁移（schema v7）。
- 自动化测试不依赖网络和模型密钥。

## 快速开始

前置条件：[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Node.js（含 npm）。先按下文配置模型，然后在项目根目录执行：

```bash
./start.sh
```

脚本会安装前端依赖（首次）、构建前端并启动服务。打开 <http://127.0.0.1:8000> 使用，接口文档在 <http://127.0.0.1:8000/docs>。首次启动会安装 Python 和依赖，需要联网。端口可通过 `PORT=9000 ./start.sh` 修改。

前端开发时可分别启动后端（`uv run --directory backend uvicorn app.main:app --port 8000`）和前端（`npm --prefix frontend run dev`，地址 <http://127.0.0.1:5173>，接口自动代理到 8000）。

### 配置模型

将 `backend/.env.example` 复制为 `backend/.env`，聊天模型填写以下三项：

```bash
PK_LLM_BASE_URL=https://你的兼容服务地址/v1
PK_LLM_MODEL=支持工具调用的模型名
PK_LLM_API_KEY=你的密钥
```

模型服务需兼容 OpenAI Chat Completions 并支持工具调用，可使用公开 API 或 Ollama、vLLM 等本地部署。`.env` 已被 Git 忽略。其余配置均有默认值，见[后端开发说明](docs/backend.md#3-配置)。

### 配置 BGE-M3 与混合检索

```dotenv
PK_RETRIEVER=hybrid
PK_EMBEDDING_BASE_URL=https://你的兼容服务地址/v1
PK_EMBEDDING_MODEL=bge-m3
PK_EMBEDDING_API_KEY=你的密钥
PK_EMBEDDING_DIMENSIONS=1024
```

程序追加 `/embeddings`；免认证的本地服务可留空密钥。`vector` 仅使用向量，`hybrid` 使用 BM25 + 稠密向量 RRF 融合，`bm25` 与 `cosine` 不调用 Embedding。向量与片段一起保存在 `backend/data/knowledge.db`。

已有文档切换到 `vector` / `hybrid`，或更换 Embedding 服务、模型、维度后，停止应用并为每个用户重建向量：

```bash
uv run --directory backend python -m app.reindex --username <用户名>
```

### 解析模型（Docling）

PDF/DOCX 解析所需模型在首次解析时自动下载（需联网）。离线运行前先执行：

```bash
uv run --directory backend python -m app.parsers.prefetch
```

模型默认保存在 `~/.cache/docling/models`，也可用 `PK_DOCLING_ARTIFACTS_PATH` 指定目录。

### MCP Server

```bash
PK_MCP_USERNAME=<用户名> uv run --directory backend python -m app.mcp_server
```

Claude Code 中注册：

```bash
claude mcp add --transport stdio --env PK_MCP_USERNAME=<用户名> knowledgepilot -- uv run --directory <仓库绝对路径>/backend python -m app.mcp_server
```

不依赖 AI 客户端的调用示例：

```bash
uv run --directory backend python ../skills/knowledgepilot/scripts/demo_client.py --query "删除文档时如何清理索引"
```

## 测试

```bash
uv run --directory backend pytest -q
uv run --directory backend ruff check app tests
uv run --directory backend ruff format --check app tests
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run lint
```

## 评测

评测集与运行方法见 [eval/README.md](eval/README.md)。三套评测分别用于功能回归（虚构语料 20 题）、金融多文档复杂任务（4 份季报 24 题）和综合评测（8 个真实公开资料库、20 组 75 题，覆盖检索、回答、引用、范围隔离、工具选择、多轮指代、不编造、复杂任务、异常降级 9 个维度）。评测脚本创建独立临时数据目录，不改动现有用户数据。

```bash
# 校验综合评测集的标注与覆盖约束（无需网络）
uv run --directory backend python ../eval/suite/run.py

# 检索策略对照（需配置 Embedding 服务）
uv run --directory backend python ../eval/suite/run.py --mode retrieval --retriever hybrid --env-file backend/.env --split dev

# Agent 评测（真实模型）
uv run --directory backend python ../eval/suite/run.py --mode agent --retriever hybrid --env-file backend/.env --split dev --repeat 3
```

## 已知限制

- PDF/DOCX 解析串行执行（一次一份），超过 `PK_PARSE_TIMEOUT`（默认 30 分钟）终止并标记失败；服务重启时中断的任务需手动重试。
- Docling 及其依赖使安装体积增加约 1 GB，模型约 0.7 GB。
- 重新解析期间文档不可检索；DOCX 没有页面坐标，只能定位到元素和标题路径；高亮精度为元素级（如整张表）。
- 文件只能在同一知识库内移动；不支持旧版 `.doc`。
- TXT 约 800 字合并为一个片段，引用定位到行范围；混合检索的 RRF 会压低只被一路召回的结果。
- 向量检索为精确扫描，适合本地规模；检索未覆盖不代表文档未提及；引用校验验证来源真实性，结论是否被原文充分支持仍需核对。
- 登录失败限流为进程内计数，服务重启后清零。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/README.md](docs/README.md) | 功能需求与验收要点 |
| [docs/design.md](docs/design.md) | 系统设计：架构、技术选型、模块设计、关键算法、非功能设计 |
| [docs/contracts.md](docs/contracts.md) | 接口契约（API 文档）：HTTP 接口、错误码、SSE 事件、工具定义、Agent 循环、降级、MCP |
| [docs/data-model.md](docs/data-model.md) | 数据模型：表结构、约束、删除规则、版本迁移 |
| [docs/backend.md](docs/backend.md) | 后端开发说明：启动、配置、命令行工具、接口示例、测试 |
| [docs/eval-report.md](docs/eval-report.md) | 评测报告 |
| [eval/README.md](eval/README.md) | 评测集说明与复现命令 |
| [skills/knowledgepilot/](skills/knowledgepilot/) | Skill 包：`SKILL.md`、MCP 客户端配置、调用示例 |
| [AGENTS.md](AGENTS.md) | 参与开发的 Agent 须遵守的规则 |

## 项目结构

```text
backend/
  app/
    api/               HTTP 路由
    services/          业务服务：认证、权限、文档、解析入库、检索、向量、会话
    tools/             工具注册表与内置工具
    agent/             Agent 循环、上下文管理、SSE
    llm/               模型客户端接口与 OpenAI 兼容实现
    parsers/           解析适配层（TXT、Docling 子进程）
    mcp_server.py      stdio MCP Server
    main.py            应用装配
    db.py              表结构与迁移
  tests/               自动化测试
frontend/
  src/
    api/               接口与 SSE 客户端
    features/          认证、知识库、文件、对话、引用、轨迹
    lib/               引用解析、PDF 坐标、目录树等工具函数
skills/knowledgepilot/ Skill 包
eval/                  评测集、运行器与评分
examples/              演示资料
docs/                  需求、设计、接口、数据模型、评测文档
start.sh               一条命令启动
```
