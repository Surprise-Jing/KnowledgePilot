# 首日后端开发说明

## 本次实现范围

后端已实现知识库创建和列表、文档上传、异步解析、片段持久化、按库检索、单轮回答及来源返回，供后续前端和 Agent 接入。在首日最小闭环的基础上，数据模型已升级到 v1：按用户隔离数据，位置改为结构化字段，并为目录、会话和调用轨迹预留了表。这不代表全部 P0/P1 已完成。表结构见[数据模型设计](./data-model.md)。

- 请求通过 `X-User-Id` 请求头标识用户，缺省时使用 `PK_DEFAULT_USER_ID`（默认 `demo`）。所有知识库、文档和检索操作只能访问自己的数据，访问他人数据返回 404。这是本地演示用的身份传递方式，不做身份认证。
- 文件支持 TXT（UTF-8 或 GBK）、文本型 PDF、DOCX（段落和表格）。暂不支持旧版 `.doc`、OCR、图片和嵌套表格内容提取。
- 解析状态为 `queued → parsing → success / failed`，失败可重试。
- 同一知识库内按 SHA-256 去重，重复上传返回已有记录；失败记录需要显式重试。
- 片段按格式合并切分：TXT 连续行合并，PDF 按页，DOCX 按标题小节合并段落，表格按表合并行；每段最多约 800 字符，超长单元按 200 字符重叠拆分。
- 每个片段的 `location` 是结构化对象，包含类型、起止行/页/段/表格行、DOCX 标题路径和拆分时的字符范围，以及展示文本 `label`。
- 检索使用中文单字及双字词项、英文词项的词频余弦相似度，返回前 K 项并按内容去重。分数是词项相似度，不是答案置信度。
- 默认回答是检索片段的原文摘录；配置模型后通过兼容 Chat Completions 的接口生成回答。
- SQLite 持久保存知识库、文档和片段；原文件保存在本地。删除文档同步删除片段，并清理原文件。

## 目录结构

```text
backend/
  app/
    main.py                 应用工厂：错误处理、中间件、路由装配
    deps.py                 共享依赖：配置、数据库、当前用户
    logging_setup.py        JSON 日志、X-Request-Id 与 run_id 关联
    api/                    HTTP 路由（knowledge_bases、folders、documents、search、conversations）
    config.py               环境变量配置
    db.py                   SQLite 表结构、版本迁移、连接和启动恢复
    schemas.py              输入输出模型
    errors.py               业务错误
    services/
      ingestion.py          文档解析、切片和入库
      access.py             按用户校验知识库和文档归属
      retrieval.py          按库过滤、打分和去重
      answering.py          摘录回答和真实模型适配
  tests/test_api.py         闭环与异常场景测试
  tests/test_data_model.py  用户隔离、切分定位、编码和迁移测试
  .env.example              配置模板
  pyproject.toml            依赖及测试配置
  uv.lock                   依赖锁定文件
examples/knowledge.txt      演示资料
```

路由层组织请求，服务层实现解析、检索和回答，数据层负责事务。后续可在服务层上增加工具契约和 Agent 循环，无需重写上传链路。

## 启动与配置

前置条件：安装 uv。首次运行需要联网安装 Python 3.11 或更高版本及依赖。

在项目根目录执行：

```bash
uv run --directory backend uvicorn app.main:app --host 127.0.0.1 --port 8000
```

也可以先进入 `backend`，再执行 `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`。

- 健康检查：<http://127.0.0.1:8000/health>
- Swagger 接口文档：<http://127.0.0.1:8000/docs>
- OpenAPI：<http://127.0.0.1:8000/openapi.json>

将 `backend/.env.example` 复制为 `backend/.env`，只需填写三项模型配置。启动命令将工作目录设置为 `backend`，因此默认数据保存在 `backend/data`。

| 必填环境变量 | 用途 |
| --- | --- |
| `PK_LLM_BASE_URL` | OpenAI 兼容服务地址，程序追加 `/chat/completions` |
| `PK_LLM_MODEL` | 模型名称，需支持工具调用 |
| `PK_LLM_API_KEY` | 模型密钥，只用于后端请求 |

以下配置都有默认值，一般无需设置，仅在调试或评测时通过环境变量覆盖：

| 可选环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `PK_DATA_DIR` | `data` | 数据库和原文件目录，相对于进程工作目录 |
| `PK_DEFAULT_USER_ID` | `demo` | 请求未携带 `X-User-Id` 时使用的用户 |
| `PK_LLM_TIMEOUT` | `45` | 模型请求超时秒数 |
| `PK_LLM_STREAM` | `true` | 模型服务不支持流式工具调用时设为 `false` |
| `PK_AGENT_MAX_STEPS` | `6` | Agent 最大执行步数 |
| `PK_TOOL_TIMEOUT` | `15` | 单次工具调用超时秒数 |
| `PK_CONTEXT_BUDGET_CHARS` | `24000` | 对话上下文预算（字符） |
| `PK_RETRIEVER` | `cosine` | 检索策略，评测对照用 |
| `PK_MCP_USER_ID` | `demo` | MCP Server 访问知识库时的用户 |
| `PK_MAX_UPLOAD_BYTES` | `10485760` | 上传大小限制，默认 10 MiB |
| `PK_LOG_LEVEL` | `INFO` | 日志级别 |
| `PK_ANSWER_MODE` | `extractive` | 仅影响调试接口 `/api/chat`：`extractive` 为摘录，`openai` 为调用模型 |

本地兼容服务若不校验密钥，可填其允许的占位值。模型异常会明确返回错误，不会悄悄改成摘录回答。调用外部模型会发送问题和检索片段，请只上传公开或脱敏资料。

## 最小闭环演示

推荐先使用 Swagger 页面按顺序操作，也可以使用以下命令。将响应中的实际 ID 替换到后续命令中。

### 1. 创建知识库

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge-bases \
  -H 'Content-Type: application/json' \
  -d '{"name":"演示知识库"}'
```

保存响应中的 `id` 为知识库 ID。

### 2. 上传并查看解析状态

在项目根目录执行，替换 `KB_ID`：

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge-bases/KB_ID/documents \
  -F 'file=@examples/knowledge.txt'
```

响应为 HTTP 202，包含文档 ID 和提交时状态。后台任务随后进行解析。替换 `DOCUMENT_ID`，轮询直到 `success` 或 `failed`：

```bash
curl http://127.0.0.1:8000/api/documents/DOCUMENT_ID
```

### 3. 检索

```bash
curl -X POST http://127.0.0.1:8000/api/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"删除文档时如何处理索引","knowledge_base_ids":["KB_ID"],"top_k":3}'
```

每个结果包含 `chunk_id`、`document_id`、`knowledge_base_id`、`filename`、`content`、`location` 和 `score`。`location` 是结构化对象，`location.label` 是可直接展示的位置文本，例如 `第 1–3 行`。

以上命令未携带 `X-User-Id`，均以默认用户 `demo` 操作。加上 `-H 'X-User-Id: alice'` 即以 alice 身份操作；用 bob 的身份检索 alice 的知识库会返回 404。

### 4. 获取回答

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"删除文档时如何处理索引","knowledge_base_ids":["KB_ID"],"top_k":3}'
```

响应包含 `answer`、`mode` 和 `citations`。回答的 `[1]` 对应 `citations[0]`，依此类推。`citations` 是提供给回答阶段的来源列表，可能包含模型没有引用的候选来源。真实模型模式检查引用编号存在且不越界，但尚不验证每个结论是否被原文充分支持。无结果时明确告知未检索到相关内容。

## 接口清单

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/health` | 健康检查和回答模式 |
| POST / GET | `/api/knowledge-bases` | 创建 / 列出知识库 |
| POST / GET | `/api/knowledge-bases/{id}/documents` | 上传 / 列出文档 |
| GET | `/api/documents/{id}` | 查询文档与解析状态 |
| GET | `/api/documents/{id}/chunks` | 按顺序读取文档片段与来源位置 |
| POST | `/api/documents/{id}/retry` | 重试失败的解析任务 |
| DELETE | `/api/documents/{id}` | 删除原文件、文档和片段 |
| POST | `/api/search` | 按一个或多个知识库检索 |
| POST | `/api/chat` | 单轮检索问答 |

业务错误结构为 `{"error":{"code":"...","message":"..."}}`。参数校验错误使用 FastAPI 默认的 HTTP 422 `detail` 结构。常见状态码：400 空文件或用户标识不合法、404 资源不存在、409 不可重试、413 文件过大、415 格式不支持、502 模型调用或引用失败、503 模型未配置、504 模型超时。

## 测试

在项目根目录执行：

```bash
uv run --directory backend pytest -q
uv run --directory backend ruff check app tests
```

测试覆盖用户隔离、TXT 上传及 GBK 编码、按行/标题小节合并切分与超长拆分、DOCX 段落和表格、PDF 文本解析、失败重试、同库去重、多库过滤、来源定位、摘录回答、删除清理、重启持久化、任务与运行中断恢复、首日数据库迁移、高版本数据库拒绝启动、级联删除、上传大小限制，以及使用模拟响应的模型适配和超时。测试使用临时数据库，不依赖真实模型密钥。真实模型联网效果需配置密钥后另行验证。

本次验证结果：Python 3.12 环境下 19 项测试通过，Ruff 检查及格式检查通过；实际启动 Uvicorn 后，以 alice 身份上传示例文档并检索成功，bob 检索同一知识库返回 404。测试依赖存在一条 Starlette 关于未来切换 HTTP 测试客户端的弃用提示，不影响当前测试结果。

## 当前边界与后续接入

1. 用户身份由请求头直接声明，不做认证，任何人都可以冒充其他用户。这满足本地演示的隔离验收，但默认仅监听 `127.0.0.1`，不能直接作为多人服务部署。
2. 后台任务使用进程内任务，启动时将中断任务标为失败，需手动重试。当前只支持单进程启动，不使用多个 workers；后续可迁移到持久任务队列。
3. 检索逐次扫描指定库的片段，适合小规模演示；语义匹配、专用索引、重排和评测优化待后续实现。
4. 当前是固定的检索后回答流程，无模型自主选工具、多轮上下文或流式输出。工具层、Agent 循环和 MCP＋Skill 尚未实现。
5. 目录管理接口（表已建好）、知识库重命名与删除、文件移动与重命名、前端、P1 查询分解与多文档对比等尚未实现；需求仍以功能 README 为准。
6. 上传限制在应用读取时检查，解析器暂无独立进程资源隔离；当前面向受信任的本地演示文件。
7. 数据库使用 `PRAGMA user_version` 做版本迁移。从首日原型升级时，旧片段会被清除，文档需要点击重试重新解析，详见[数据模型设计](./data-model.md#5-版本迁移)。

## 选型理由与替代方案

| 当前选择 | 理由 | 后续替代方向 |
| --- | --- | --- |
| FastAPI | 输入校验、接口文档和 Python 解析库便于快速联调 | 团队熟悉其他框架时可替换 HTTP 层 |
| SQLite＋本地文件 | 无需额外服务，易于本地持久化和演示 | 多用户或并发增长后使用 PostgreSQL 与对象存储 |
| 词项余弦检索 | 无需下载嵌入模型或配置外部密钥，方便先验证闭环 | 引入嵌入模型、向量库或混合检索 |
| 摘录＋模型适配 | 无密钥也能验证来源链路，模型接入独立于解析和检索 | 使用模型 SDK 或增加其他提供商适配 |
| 进程内后台任务 | 首日无需维护队列服务 | 使用持久队列与独立 worker |

实现参考：[FastAPI 文件上传](https://fastapi.tiangolo.com/tutorial/request-files/)、[FastAPI 后台任务](https://fastapi.tiangolo.com/tutorial/background-tasks/)、[Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。
