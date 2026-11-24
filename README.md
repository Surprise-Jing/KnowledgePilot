# KnowledgePilot

Agentic Personal Knowledge Workspace（个人知识管理智能助手）：让模型像使用工具一样使用知识库——多知识库与目录管理、文档解析入库、检索、模型自主调用工具、多轮对话、流式回答、引用可追溯、调用轨迹可见。

完整需求见 [功能需求](docs/README.md)，开发计划见 [任务与编排](docs/tasks.md)。

## 当前状态

P0 主流程已打通，并已用真实模型（OpenAI 兼容服务）和 BGE-M3 混合检索完成端到端验证：建库 → 建目录 → 上传 PDF/DOCX/TXT → 解析 → 提问 → 模型自主调用工具 → 流式回答 → 引用与调用轨迹 → 追问 → 删除 → 重启后数据仍在。MCP＋Skill、工具失败降级、20 条评测在第二轮开发中。

### 已实现

**知识库、目录与文件**
- 通过请求头 `X-User-Id` 区分用户（缺省为 `demo`，前端可切换），所有数据按用户隔离，访问他人数据返回“不存在”。本地演示用，不做认证。
- 知识库创建、列表、重命名、删除（同时清理原文件）。
- 任意层级目录：创建、重命名、移动（防止移动成环）、删除（非空目录需确认递归删除）。
- 文件上传到指定目录、按目录列出、重命名、在同一知识库内移动、删除；删除操作幂等。

**解析入库**
- 支持 TXT（UTF-8 / GBK）、文本型 PDF、DOCX（段落和表格）；校验格式、大小（默认 10 MiB）和空文件。
- 同一知识库内按内容（SHA-256）去重，重复上传返回已有记录并标记 `duplicate`。
- 异步解析，状态为排队、解析中、成功、失败；失败显示原因并可重试；重启后中断的任务标记为失败，不会卡在中间状态。
- 按格式切片并记录结构化位置（行号、页码、DOCX 标题路径与段落、表格行），可直接定位原文。

**检索**
- 按一个或多个知识库检索，返回内容、来源、位置和相关度，去重排序。
- 支持词频余弦基线、BM25、BGE-M3 稠密向量和 BM25＋向量 RRF 混合检索（`PK_RETRIEVER`）。

**工具与 Agent 对话**
- 4 个工具：`list_knowledge_bases`、`search_chunks`、`list_files`、`read_document`，带参数校验、结构化错误、权限与知识库范围检查。
- 模型自主选择工具，限制最大步数；工具错误回传给模型；模型异常、超时有明确提示。
- 多轮对话与指代消解（如“这份文档”），按字符预算管理上下文。
- SSE 流式回答，工具调用轨迹实时推送；引用只接受本次工具实际返回的片段，编造的引用会被丢弃并记录。
- 会话、消息、调用轨迹、引用快照持久化。

**前端**（Vite + React + shadcn/ui）
- 用户切换、知识库与目录树、文件管理与解析状态、对话（多选知识库、流式回答、停止）、引用面板（查看原文）、工具轨迹面板。

**工程基础**
- 分层：路由 → 服务 → 数据库；工具层与 Agent 只调用服务层。
- 统一错误结构 `{"error": {"code", "message", "retryable", "details"}}`；JSON 结构化日志，带请求 ID 与提问 ID。
- SQLite 持久化与版本迁移（schema v3）。
- 后端 191 项、前端 41 项自动化测试，不依赖网络和模型密钥。

### 开发中

MCP＋Skill、工具失败降级、20 条评测与检索策略对照、前端体验完善。进度见 [docs/tasks.md](docs/tasks.md)。

## 快速开始

前置条件：[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Node.js（含 npm）。先按下文配置模型，然后在项目根目录执行：

```bash
./start.sh
```

脚本会安装前端依赖（首次）、构建前端，再启动服务。打开 <http://127.0.0.1:8000> 使用；接口文档在 <http://127.0.0.1:8000/docs>。首次启动会自动安装 Python 和依赖，需要联网。端口可用 `PORT=9000 ./start.sh` 修改。

前端开发时可分别启动后端（`uv run --directory backend uvicorn app.main:app --port 8000`）和前端（`npm --prefix frontend run dev`，地址 <http://127.0.0.1:5173>，接口自动代理到 8000）。

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
uv run --directory backend python -m app.reindex --user-id demo
```

更换 Embedding 服务、模型或维度后也需重建；同名模型更新权重时提升
`PK_EMBEDDING_REVISION`。重建不改变片段 ID；模型故障会明确报错，不静默退回关键词检索。

## 测试

```bash
uv run --directory backend pytest -q
uv run --directory backend ruff check app tests
npm --prefix frontend test
npm --prefix frontend run build
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/README.md](docs/README.md) | 功能需求与验收清单 |
| [docs/backend.md](docs/backend.md) | 后端启动、接口示例、当前边界与选型理由 |
| [docs/data-model.md](docs/data-model.md) | 数据模型与迁移 |
| [docs/contracts.md](docs/contracts.md) | 共享契约：API、错误码、SSE 事件、工具定义、Agent 循环 |
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
```
