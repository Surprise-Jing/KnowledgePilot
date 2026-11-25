# 后端开发说明

本文说明后端的代码结构、启动与配置、命令行工具、接口调用示例和测试方法。架构与算法见[系统设计文档](design.md)，接口定义见[接口契约](contracts.md)，表结构见[数据模型设计](data-model.md)。

## 1. 代码结构

```text
backend/
  app/
    main.py              应用工厂：错误处理、日志中间件、路由装配、前端静态托管
    config.py            环境变量配置（前缀 PK_）
    db.py                SQLite 表结构、版本迁移、启动恢复
    deps.py              共享依赖：配置、数据库、当前用户
    errors.py            业务错误 AppError
    schemas.py           请求、响应、SSE 事件与工具结果模型
    logging_setup.py     JSON 日志、X-Request-Id 与 run_id 关联
    api/                 HTTP 路由：auth、knowledge_bases、folders、documents、search、conversations
    services/            业务服务：auth、access、knowledge_bases、folders、documents、ingestion、
                         retrieval、embeddings、chat_scope、conversations、artifacts、answering
    tools/               工具注册表、四个内置工具、参数校验、工具运行时上下文
    agent/               Agent 循环、上下文组装、来源索引、系统提示、SSE 帧
    llm/                 LLMClient 协议、OpenAI 兼容实现、默认工厂
    parsers/             Parser 接口、TXT 解析、Docling 子进程与结构映射、模型预下载
    mcp_server.py        stdio MCP Server
    users.py             账号管理命令行
    reindex.py           向量重建命令行
  tests/                 自动化测试；fakes/ 为测试替身，e2e/ 为端到端与评测管线测试
  .env.example           配置模板
  pyproject.toml         依赖与工具配置
  uv.lock                依赖锁定文件
```

## 2. 启动

前置条件：安装 [uv](https://docs.astral.sh/uv/)。首次运行会安装 Python 3.11+ 及依赖，需要联网。

```bash
uv run --directory backend uvicorn app.main:app --host 127.0.0.1 --port 8000
```

命令以 `backend` 为工作目录，默认数据保存在 `backend/data/`。只能以单个 worker 运行：解析队列与 Agent 运行状态保存在进程内。

| 地址 | 内容 |
| --- | --- |
| <http://127.0.0.1:8000/> | 前端（需先执行 `npm --prefix frontend run build`，或使用根目录 `./start.sh`） |
| <http://127.0.0.1:8000/health> | 健康检查 |
| <http://127.0.0.1:8000/docs> | Swagger 接口文档 |
| <http://127.0.0.1:8000/openapi.json> | OpenAPI 定义 |

## 3. 配置

将 `backend/.env.example` 复制为 `backend/.env`。聊天模型必须配置以下三项：

| 变量 | 用途 |
| --- | --- |
| `PK_LLM_BASE_URL` | OpenAI 兼容服务地址，程序追加 `/chat/completions` |
| `PK_LLM_MODEL` | 模型名称，需支持工具调用 |
| `PK_LLM_API_KEY` | 模型密钥，只在后端使用 |

其余配置均有默认值：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `PK_DATA_DIR` | `data` | 数据库、原文件和解析产物目录，相对于进程工作目录 |
| `PK_LOG_LEVEL` | `INFO` | 日志级别 |
| `PK_MAX_UPLOAD_BYTES` | `10485760` | 上传大小上限（10 MiB） |
| `PK_SESSION_TTL_DAYS` | `30` | 登录会话有效期（天） |
| `PK_COOKIE_SECURE` | `false` | 会话 Cookie 是否只经 HTTPS 发送 |
| `PK_ALLOW_REGISTRATION` | `true` | 是否开放网页注册 |
| `PK_MCP_USERNAME` | `demo` | MCP Server 以哪个已注册用户运行 |
| `PK_LLM_TIMEOUT` | `45` | 模型请求超时（秒） |
| `PK_LLM_STREAM` | `true` | 模型服务不支持流式工具调用时设为 `false` |
| `PK_AGENT_MAX_STEPS` | `6` | Agent 最大执行步数（1–20） |
| `PK_TOOL_TIMEOUT` | `15` | 单次工具调用超时（秒） |
| `PK_CONTEXT_BUDGET_CHARS` | `24000` | 对话上下文预算（字符） |
| `PK_RETRIEVER` | `cosine` | 检索策略：`cosine`、`bm25`、`vector`、`hybrid`；`.env.example` 推荐 `hybrid` |
| `PK_RETRIEVAL_CANDIDATES` | `50` | 混合检索每路候选数量，至少为 top_k |
| `PK_EMBEDDING_BASE_URL` | 空 | Embedding 服务地址，程序追加 `/embeddings`；`vector` / `hybrid` 必填 |
| `PK_EMBEDDING_MODEL` | `bge-m3` | Embedding 模型 |
| `PK_EMBEDDING_API_KEY` | 空 | Embedding 密钥；免认证本地服务可留空 |
| `PK_EMBEDDING_DIMENSIONS` | `1024` | 期望维度，严格校验服务返回 |
| `PK_EMBEDDING_TIMEOUT` | `45` | 入库时每批向量请求超时（秒） |
| `PK_EMBEDDING_QUERY_TIMEOUT` | `8` | 检索时查询向量化超时（秒），需小于 `PK_TOOL_TIMEOUT` 才能触发 BM25 降级 |
| `PK_EMBEDDING_BATCH_SIZE` | `32` | 每批向量化文本数 |
| `PK_EMBEDDING_REVISION` | `1` | 同名模型更换权重时手动提升，并重建向量 |
| `PK_PARSE_TIMEOUT` | `1800` | 单个文档解析超时（秒），超时终止解析进程 |
| `PK_CHUNK_TOKENIZER` | `BAAI/bge-m3` | 分块计数用分词器；离线时可设为本地目录 |
| `PK_CHUNK_MAX_TOKENS` | `512` | 分块 token 上限 |
| `PK_DOCLING_ARTIFACTS_PATH` | 空 | Docling 模型本地目录；留空使用 Hugging Face 缓存 |
| `PK_DOCLING_DEVICE` | `auto` | `auto` / `cpu` / `mps` / `cuda` |
| `PK_DOCLING_THREADS` | `4` | Docling 线程数 |
| `PK_DOCLING_OCR_ENGINE` | `rapidocr` | 增强模式 OCR 引擎：`rapidocr` / `easyocr` / `tesseract` / `ocrmac` |
| `PK_DOCLING_FORCE_FULL_PAGE_OCR` | `true` | 增强模式是否整页 OCR |
| `PK_ANSWER_MODE` | `extractive` | 仅影响调试接口 `/api/chat`：`extractive` 摘录，`openai` 调用模型 |

模型异常会明确返回错误，不会改用摘录回答。调用外部模型与 Embedding 服务会发送问题与所选资料片段，只应上传公开或脱敏资料。

## 4. 解析模型准备

PDF/DOCX 由 Docling 解析，所需版面、表格、OCR 模型与 BGE-M3 分词器在首次解析时自动下载。离线或网络不稳定时，预先执行：

```bash
uv run --directory backend python -m app.parsers.prefetch
```

模型默认保存在 `~/.cache/docling/models`，也可通过 `PK_DOCLING_ARTIFACTS_PATH` 指定目录。

## 5. 命令行工具

以下命令修改数据库，需在 Web 服务停止时运行。

| 命令 | 用途 |
| --- | --- |
| `uv run --directory backend python -m app.users list` | 列出用户 |
| `uv run --directory backend python -m app.users set-password <用户名>` | 设置密码（同时注销该用户全部会话）；用于没有密码的已有用户 |
| `uv run --directory backend python -m app.users delete <用户名> --yes` | 删除用户及其全部数据，不可恢复 |
| `uv run --directory backend python -m app.reindex --username <用户名>` | 为该用户全部成功文档补建或重建向量；切换到 `vector` / `hybrid`、更换 Embedding 服务、模型或维度后执行。逐文档提交，保留片段 ID |
| `PK_MCP_USERNAME=<用户名> uv run --directory backend python -m app.mcp_server` | 启动 stdio MCP Server（通常由 MCP 客户端拉起），可与 Web 服务同时运行 |

## 6. 接口调用示例

完整接口见[接口契约 §3](contracts.md#3-http-api)。以下示例在项目根目录执行，登录后用 `-b cookies.txt` 携带会话。

```bash
# 注册并保存会话 Cookie（已有账号改用 /api/auth/login）
curl -c cookies.txt -X POST http://127.0.0.1:8000/api/auth/register \
  -H 'Content-Type: application/json' -d '{"username":"alice","password":"secret123"}'

# 创建知识库，记下返回的 id
curl -b cookies.txt -X POST http://127.0.0.1:8000/api/knowledge-bases \
  -H 'Content-Type: application/json' -d '{"name":"演示知识库"}'

# 上传文档（202），轮询状态直到 success 或 failed
curl -b cookies.txt -X POST http://127.0.0.1:8000/api/knowledge-bases/KB_ID/documents \
  -F 'file=@examples/knowledge.txt'
curl -b cookies.txt http://127.0.0.1:8000/api/documents/DOCUMENT_ID

# 检索
curl -b cookies.txt -X POST http://127.0.0.1:8000/api/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"删除文档时如何处理索引","knowledge_base_ids":["KB_ID"],"top_k":3}'

# 创建会话并提问（SSE 流式返回）
curl -b cookies.txt -X POST http://127.0.0.1:8000/api/conversations \
  -H 'Content-Type: application/json' -d '{}'
curl -N -b cookies.txt -X POST http://127.0.0.1:8000/api/conversations/CONVERSATION_ID/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"删除文档时如何处理索引？","scope":{"mode":"selected","knowledge_base_ids":["KB_ID"]}}'
```

检索结果包含 `chunk_id`、`document_id`、`knowledge_base_id`、`filename`、`content`、结构化 `location`（含展示文本 `location.label`）和 `score`。不同检索策略的分数不可相互比较，也不表示答案置信度。使用其他用户的会话访问该知识库返回 404。

## 7. 测试与检查

```bash
uv run --directory backend pytest -q
uv run --directory backend ruff check app tests
uv run --directory backend ruff format --check app tests
```

- 测试使用临时数据目录，不读取 `.env`，不访问网络，不需要模型密钥。模型由 `tests/fakes/llm.py` 的 `ScriptedLLM` 替代；PDF/DOCX 默认由 `tests/conftest.py` 注入的离线替身解析器处理。
- 真实 Docling 解析测试需已下载模型，按需运行：

```bash
PK_RUN_DOCLING_TESTS=1 uv run --directory backend pytest tests/e2e/test_docling_real.py
```

- 真实模型冒烟（需先启动服务并配置 `backend/.env`）：

```bash
uv run --directory backend python ../scripts/smoke.py --base-url http://127.0.0.1:8000 --repo-root "$PWD"
```

## 8. 运行约束

| 约束 | 说明 |
| --- | --- |
| 单 worker | 解析队列、解析子进程与 Agent 运行状态在进程内，不支持多 worker |
| 启动恢复 | 启动时将排队或解析中的文档标记为失败（可重试），将进行中的运行标记为中断 |
| 解析串行 | PDF/DOCX 一次解析一份，其余保持排队 |
| 工具超时 | 超时的工具调用在后台线程中运行至结束，只持有自己的短事务 |
| 向量检索规模 | 精确扫描，耗时与内存随检索范围内片段数线性增长 |
| 文件移动 | 仅限同一知识库内；不支持旧版 `.doc` |
