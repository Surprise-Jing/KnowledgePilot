# KnowledgePilot

Agentic Personal Knowledge Workspace（个人知识管理智能助手）：让模型像使用工具一样使用知识库——多知识库与目录管理、文档解析入库、检索、模型自主调用工具、多轮对话、流式回答、引用可追溯、调用轨迹可见。

完整需求见 [功能需求](docs/README.md)，开发计划见 [任务与编排](docs/tasks.md)。

## 当前状态

后端已完成首日最小闭环（上传 → 解析 → 检索 → 回答）和并行开发所需的公共基础。Agent 对话、目录管理、前端、MCP 正在开发中。

### 已实现

**知识库与用户隔离**
- 通过请求头 `X-User-Id` 区分用户（缺省为 `demo`），所有知识库、文档、检索只能访问自己的数据，访问他人数据返回“不存在”。这是本地演示用的身份传递方式，不做认证。
- 创建和列出知识库。

**文档上传与解析**
- 支持 TXT（UTF-8 / GBK）、文本型 PDF、DOCX（段落和表格）；校验格式、大小（默认 10 MiB）和空文件。
- 同一知识库内按内容（SHA-256）去重，重复上传返回已有记录。
- 异步解析，状态为排队、解析中、成功、失败；失败显示原因并可重试，同一文档不会并发解析。
- 服务重启时，中断的解析任务标记为失败，可重新解析，不会卡在中间状态。
- 删除文档同步删除片段和原文件；解析中被删除的文档不会被写回。

**切片与来源定位**
- 按格式切分：TXT 合并连续行，PDF 按页，DOCX 按标题小节合并段落、按表格合并行；每段约 800 字符，超长内容带重叠拆分。
- 每个片段记录结构化位置（行号、页码、段落或表格行、DOCX 标题路径、拆分时的字符范围），并生成可读标签，如 `第一章 › 1.1 小节 › 第 4–6 段`。

**检索**
- 按一个或多个知识库检索，返回内容、来源文件、位置和相关度，去重后排序。
- 当前算法为中文单字/双字及英文词项的词频余弦相似度，作为后续算法优化的基线。

**单轮问答（调试接口）**
- `POST /api/chat`：检索后回答，默认为原文摘录，也可配置为调用模型；回答中的 `[1]` 对应返回的来源。正式的 Agent 对话接口开发中。

**工程基础**
- 统一错误结构 `{"error": {"code", "message", "retryable", "details"}}`，参数校验错误也使用该结构；未预期异常记录日志后返回 `internal_error`。
- JSON 结构化日志，每个请求有 `X-Request-Id`，与后续的提问 ID 关联。
- SQLite 持久化，带版本迁移（当前 schema v2），已为目录、会话、消息、运行、工具调用和引用建好表。
- 26 项自动化测试，不依赖网络和模型密钥。

### 开发中

目录管理与文件重命名/移动、知识库重命名/删除、工具层与 Agent 循环、多轮对话、SSE 流式回答、引用过滤与调用轨迹、React 前端、MCP＋Skill、20 条评测与算法优化。进度和分工见 [docs/tasks.md](docs/tasks.md)。

## 快速开始

安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 后，在项目根目录执行：

```bash
uv run --directory backend uvicorn app.main:app --host 127.0.0.1 --port 8000
```

首次启动会自动安装 Python 和依赖，需要联网。接口文档：<http://127.0.0.1:8000/docs>。

### 配置模型

将 `backend/.env.example` 复制为 `backend/.env`，只需填写三项：

```bash
PK_LLM_BASE_URL=https://你的兼容服务地址/v1
PK_LLM_MODEL=支持工具调用的模型名
PK_LLM_API_KEY=你的密钥
```

模型服务需兼容 OpenAI Chat Completions 并支持工具调用。`.env` 已被 Git 忽略，不要提交密钥。其余配置都有默认值，见 [后端开发说明](docs/backend.md#启动与配置)。

## 测试

```bash
uv run --directory backend pytest -q
uv run --directory backend ruff check app tests
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
