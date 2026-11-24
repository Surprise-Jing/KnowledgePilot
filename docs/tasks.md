# KnowledgePilot 开发任务与 Subagent 编排计划

> 维护人：主 Agent（Tech Lead）。本文件、`AGENTS.md`、`docs/contracts.md` 只由主 Agent 修改。
> 目标：2 个有效开发日内完成 P0，并完成 MCP＋Skill、20 条评测、工具失败降级、一项算法优化。

## 0. 现状评估（2026-11-22，基于当前工作区）

验证结果：`uv run pytest -q` 19 项通过；`ruff check`、`ruff format --check` 通过。注意：`backend/` 整体尚未提交到 Git（仓库只有 Initial commit），**必须先提交基线，否则 worktree 隔离拿不到后端代码**。

### 0.1 已完成且质量可用（保留，不重写）

| 能力 | 位置 | 评价 |
| --- | --- | --- |
| 用户隔离（`X-User-Id`）、越权返回 404 | `services/access.py` | 可直接被工具层复用 |
| 知识库创建 / 列表 | `main.py` | 缺重命名、删除 |
| 上传、SHA-256 同库去重、大小/格式/空文件校验 | `main.py` | 缺 `folder_id` 参数 |
| 异步解析 `queued→parsing→success/failed`、重试、删除后不写回、重启标失败 | `services/ingestion.py`、`db.py` | 满足 P0 |
| TXT(UTF-8/GBK)/PDF/DOCX 解析与结构化位置 | `services/ingestion.py` | 满足 P0，位置字段可支撑 read_document 按范围读 |
| 多库检索、去重、排序 | `services/retrieval.py` | 词频余弦全表扫描，作为算法优化基线 |
| v1 数据模型（含 folders/conversations/messages/runs/tool_calls/citations）与迁移框架 | `db.py` | 对话类表已建未用 |

### 0.2 P0 缺口

| # | 缺口 | 归属 |
| --- | --- | --- |
| G1 | 知识库重命名、删除（含磁盘原文件清理） | A |
| G2 | 目录 CRUD：任意层级、移动防环、非空目录删除规则 | A |
| G3 | 文件重命名、移动、上传到指定目录、按目录列出 | A |
| G4 | 工具层：注册表、JSON Schema、结构化错误、`search_chunks` / `list_files` / `read_document`（+`list_knowledge_bases`） | B |
| G5 | LLM Tool Calling（流式）、Agent 循环、最大步数、超时 | B |
| G6 | 会话与多轮：持久化消息、上下文预算、指代消解 | B |
| G7 | SSE 事件流、引用标记映射与过滤、调用轨迹持久化 | B |
| G8 | React 前端全部页面 | C |
| G9 | 统一错误结构（当前 422 仍是 FastAPI 默认 `detail`）、结构化日志与请求/提问 ID | 主 Agent（Phase 0） |
| G10 | 一条命令同时启动前后端 | 主 Agent（集成） |

### 0.3 代码审查发现（需在本轮处理）

1. 删除知识库 / 目录依赖 DB 级联，但磁盘原文件不会删除 → A 在服务层先收集 `path` 再删。
2. 重复删除返回 404，不满足幂等要求 → 契约改为：已不存在的资源删除返回 204（不泄露他人数据，因为同样返回 204）。
3. 同库去重与目录冲突：相同内容上传到另一目录时返回已有文档（位于原目录）→ 契约中写明，响应带 `duplicate: true`。
4. `answering.py` 的模型调用是同步非流式、无工具，且“模型未引用即 502”过严 → `/api/chat` 保留为 legacy 摘录接口，Agent 走新接口，不在旧代码上修补。
5. `tool_calls` 无结果载荷，`runs` 无处记录被丢弃的引用 → Phase 0 做 schema v2（只 `ADD COLUMN`，不重建表）。
6. 无日志配置，`logger.exception` 只有 ingestion 一处 → Phase 0 提供日志基础设施。
7. `.gitignore` 未忽略 Word 锁文件（`docs/.~任务书…docx` 已出现）、`node_modules`、`frontend/dist`。
8. 检索每次查询对全部片段重新分词，O(全库字符数)；演示规模可接受，留给算法优化轮。

## 1. 关键契约决策（详细定义写入 `docs/contracts.md`）

### 1.1 统一错误

```json
{"error": {"code": "invalid_argument", "message": "…", "retryable": false, "details": []}}
```

HTTP 错误与工具错误共用一套 `code`：`invalid_argument`、`invalid_user_id`、`knowledge_base_not_found`、`folder_not_found`、`document_not_found`、`document_not_ready`、`folder_not_empty`、`folder_cycle`、`name_conflict`、`out_of_scope`、`unsupported_format`、`file_too_large`、`empty_file`、`model_not_configured`、`model_error`、`model_timeout`、`tool_timeout`、`internal_error`。422 校验错误也转为此结构（`code=invalid_argument`）。

### 1.2 新增 HTTP 接口（A）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| PATCH / DELETE | `/api/knowledge-bases/{id}` | 重命名；删除（级联＋清理文件，幂等 204） |
| GET / POST | `/api/knowledge-bases/{id}/folders` | 扁平列表（含 `parent_id`，前端组树）/ 创建 |
| PATCH / DELETE | `/api/folders/{id}` | `{name?, parent_id?}` 移动防环 409 `folder_cycle`；删除非空目录默认 409，`?recursive=true` 递归删除 |
| POST | `/api/knowledge-bases/{id}/documents` | 新增表单字段 `folder_id`（可空） |
| GET | `/api/knowledge-bases/{id}/documents?folder_id=&recursive=` | 按目录列出 |
| PATCH | `/api/documents/{id}` | `{filename?, folder_id?}`，**只允许同库移动**（跨库移动不在范围内） |

### 1.3 新增 HTTP 接口（B）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST / GET | `/api/conversations` | 创建 / 列出会话 |
| GET / DELETE | `/api/conversations/{id}` | 详情（消息＋引用）/ 删除 |
| POST | `/api/conversations/{id}/messages` | 请求 `{content, knowledge_base_ids}`，响应 `text/event-stream` |
| GET | `/api/runs/{id}` | 一次提问的完整轨迹（tool_calls、stop_reason、用量、被丢弃的引用） |

前端用 `fetch` + `ReadableStream` 解析 SSE（`EventSource` 不能发 POST 与 `X-User-Id`）。

### 1.4 SSE 事件协议

每条事件 `event: <type>` + `data: <json>`，附递增 `id:`；每 15 秒发送注释心跳 `: ping`。**任何情况都以 `done` 结束**。

| event | data |
| --- | --- |
| `run_started` | `{run_id, conversation_id, user_message_id, assistant_message_id}` |
| `tool_call` | `{call_id, step, name, arguments}`（工具开始执行时） |
| `tool_result` | `{call_id, status: "success"\|"error", summary, error?: {code,message,retryable}, duration_ms}` |
| `token` | `{delta}` |
| `citation` | `{marker, chunk_id, document_id, filename, location, content}`（回答结束、校验后逐个发送） |
| `error` | `{code, message, retryable}` |
| `done` | `{run_id, stop_reason: answered\|max_steps\|model_error\|timeout\|interrupted, step_count, usage}` |

### 1.5 工具契约

- 统一返回：`{"ok": true, "data": {...}}` 或 `{"ok": false, "error": {code, message, retryable}}`。
- 工具运行上下文 `ToolContext(user_id, allowed_kb_ids, run_id, marker_allocator)`：工具自己做权限与范围校验，越出本次所选知识库返回 `out_of_scope`。
- 片段在一次 run 内分配引用标记 `c1, c2…`；模型在回答中写 `[c1]`，后端只接受本 run 工具实际返回过的标记，其余丢弃并记入 `runs.dropped_citations`。

| 工具 | 参数（必填加粗） | 返回 |
| --- | --- | --- |
| `list_knowledge_bases` | — | 本次可用的库 `id/name/document_count` |
| `search_chunks` | **query**、knowledge_base_ids（默认本次全部所选库）、top_k 1–10 | `hits[{marker, chunk_id, document_id, filename, location, score, content}]` |
| `list_files` | **knowledge_base_id**、folder_id、recursive、offset、limit ≤ 50 | `files[{document_id, filename, folder_path, status, error}]`、`total` |
| `read_document` | **document_id**、start_ordinal、max_chars ≤ 4000 | `content`、`chunks[{marker, location}]`、`truncated`、`next_ordinal` |

### 1.6 LLM 与 Agent

- `LLMClient` 协议：`stream(messages, tools) -> AsyncIterator[TextDelta | ToolCalls | Usage]`；实现 `OpenAICompatClient`（httpx 流式调用 `{PK_LLM_BASE_URL}/chat/completions` 的 `tools`），配置沿用 `PK_LLM_BASE_URL` / `PK_LLM_MODEL` / `PK_LLM_API_KEY`。不新增 SDK 依赖。
- 测试通过 `app.state.llm_factory` 注入 `ScriptedLLM`（只存在于 `tests/`，不进入产品代码路径）。未配置模型时对话接口返回 `model_not_configured`，**不降级成摘录冒充 Agent**。
- 新配置：`PK_AGENT_MAX_STEPS=6`、`PK_TOOL_TIMEOUT=15`、`PK_CONTEXT_BUDGET_CHARS=24000`（按字符估算，不引入 tokenizer 依赖）。
- 多轮：历史消息按预算从新到旧保留；系统提示附“会话焦点”（最近被引用的文档 id/文件名），支持“这份文档”类指代。

### 1.7 Schema v2（主 Agent 在 Phase 0 完成）

只做 `ALTER TABLE ADD COLUMN`：`tool_calls.result_json TEXT`（截断后的结构化结果，供轨迹和追问复用）、`runs.dropped_citations TEXT NOT NULL DEFAULT '[]'`。

## 2. 文件所有权

| 所有者 | 文件 |
| --- | --- |
| 主 Agent | `AGENTS.md`、`CLAUDE.md`、`docs/contracts.md`、`docs/tasks.md`、`docs/data-model.md`、`README.md`、`.gitignore`、`start.sh`、`backend/pyproject.toml`、`backend/uv.lock`、`backend/.env.example`、`backend/app/{main,config,db,errors,schemas,deps,logging_setup}.py`、`backend/app/llm/base.py`、`backend/app/api/__init__.py`、`backend/tests/test_platform.py`、`frontend/package.json` 的依赖增减 |
| A 数据与文件 | `backend/app/api/{knowledge_bases,folders,documents}.py`、`backend/app/services/{access,knowledge_bases,folders,documents,ingestion}.py`、`backend/app/services/{retrieval,embeddings}.py` 与 `backend/app/reindex.py`（向量检索已完成，Round 1 冻结，只修缺陷）、`backend/tests/test_{folders,documents_manage,knowledge_bases,documents_service,data_model,retrieval}.py`；`backend/tests/test_api.py` 中因契约变更（删除幂等、`duplicate`）需要调整的断言 |
| B 工具与 Agent | `backend/app/api/{search,conversations}.py`、`backend/app/services/{answering,conversations}.py`、`backend/app/tools/**`、`backend/app/llm/**`（`base.py` 除外）、`backend/app/agent/**`、`backend/tests/test_{tools,agent,sse,conversations}.py`、`backend/tests/fakes/**`（`ScriptedLLM` 由 B1 提供，D 复用） |
| C 前端 | `frontend/**`（初始依赖按 [docs/briefs/C-frontend.md](briefs/C-frontend.md) 白名单，之外的依赖需报告主 Agent） |
| D 测试与审查 | `backend/tests/contract/**`、`backend/tests/e2e/**`、`scripts/smoke.py`、`docs/reviews/**`、第二轮起 `eval/**`、`docs/eval-report.md` |
| E MCP（第二轮） | `backend/app/mcp_server.py`、`skills/knowledgepilot/**`、`backend/tests/test_mcp.py` |

规则：
- 需要改别人的文件或公共接口：停止该部分，返回“需要的契约变更”，由主 Agent 决定。
- 唯一例外：A/B 让 D 的契约测试通过后，可以删除该测试上的 `@pytest.mark.xfail(strict=True, …)` 一行，不得改测试内容。
- 新增依赖一律由主 Agent 写入 `pyproject.toml` / `package.json`。

## 3. 执行阶段

### Phase 0 — 主 Agent 串行（D1 上午，约 2 小时）

> 状态（2026-11-23）：Phase 0 已完成并提交；A1（44d3f46）、C1（9558f72）、B1（0d76b2c）已合入 `dev`；Integration 1 已完成（见下文“集成 1 结果”）。下一步 Round 2。
> 原状态（2026-11-22）：第 2–5 步已完成，26 项测试与 Ruff 通过，实测服务启动、统一错误与 `X-Request-Id` 正常；待第 1、6 步（提交）。

1. 经用户确认后提交当前基线（`backend/`、`docs/`、`examples/`、`README.md`），修正 `.gitignore`。
2. 纯重构：把 `main.py` 拆成 `app/api/*` 路由模块，`main.py` 只做装配；19 项测试保持全绿。
3. 统一错误处理（含 422）、`logging_setup.py`（JSON 日志、`X-Request-Id` 中间件、`run_id` contextvar）。
4. Schema v2 迁移及测试；`config.py` 增加 Agent 配置；`schemas.py` 写入全部新接口的请求/响应模型。
5. 写 `AGENTS.md`（规则、命令、所有权、返回格式）和 `docs/contracts.md`（第 1 节完整版）。
6. 提交 `phase0`。验收：`pytest`、`ruff check`、`ruff format --check` 全绿。

### Round 1a — D1 契约测试先行（约 1 小时，可与 C1 同时开始）

D1 依据 `contracts.md` 编写 `tests/contract/`：目录树与防环、重命名/移动、删除幂等与文件清理、工具参数校验与结构化错误、越库 `out_of_scope`、SSE 事件顺序与 `done` 必达（用 `ScriptedLLM`）、引用过滤、最大步数、模型超时。全部标记 `xfail(strict=True, reason="A:…"/"B:…")`，保证主干绿。同时输出对现有后端的审查报告 `docs/reviews/round0.md`。主 Agent 审核后合入主干。

### Round 1b — A1、B1、C1 并行（各自独立 worktree）

| 任务 | 内容 | 里程碑 |
| --- | --- | --- |
| A1 | G1–G3。M1（`services/documents.py` 的 `list_files`、`read_document_range` 及 `tests/test_documents_service.py`）已由主 Agent 先行交付到 `dev`，之后归 A 维护 | 契约测试中 A 部分全部转绿 |
| B1 | G4–G7。M1 已在 `dev` 上，四个工具可直接实现 | 契约测试中 B 部分全部转绿 |
| C1 | Vite + React + TS + Tailwind v4 + shadcn/ui；用户切换、知识库/目录树、文件表（状态轮询、失败原因、重试）、对话（库多选、流式、异常提示）、引用抽屉、轨迹面板；SSE 解析器单元测试（vitest）。先接已有接口，新接口按 contracts 编码 | `npm run build`、`npm test` 通过 |

强依赖处理：B 对 A 的依赖仅限两个只读服务函数，通过 M1 先行交付解耦；C 只依赖契约，不使用假数据，联调在集成阶段对真实后端完成。

### Integration 1（D1 晚，约 2 小时）

1. 合并顺序 A → B → C，逐个跑门禁。
2. 门禁：`uv run --directory backend pytest -q`、`ruff check`、`ruff format --check`、`npm --prefix frontend run build`、`npm --prefix frontend test`，且主干不存在 `xfail` 契约测试。
3. 主 Agent 在 `main.py` 挂载 `frontend/dist`，写 `start.sh`（构建前端 → 启动 uvicorn），实现一条命令启动。
4. D 运行 `scripts/smoke.py`：真实模型下走完“建库 → 建目录 → 上传三种格式 → 提问 → 追问‘这份文档’ → 校验引用与轨迹 → 删除文档 → 检索不到 → 重启 → 数据仍在”。
5. D 对合并后的差异做代码审查，结果写入 `docs/reviews/round1.md`，主 Agent 分派修复。

### 集成 1 结果（2026-11-23）

- `start.sh` 一条命令启动；FastAPI 在 `/` 托管 `frontend/dist`，API 路由优先。
- 真实模型（OpenAI 兼容，流式工具调用）＋ BGE-M3 `hybrid` 冒烟 18/18 通过：两库、两层目录、TXT/DOCX/PDF 解析成功；bob 越权检索 404；首字约 5 秒；模型自主调用 `search_chunks`，追问“这份文档”时调用 `read_document` 并引用同一文档；轨迹与 token 用量持久化；删除后检索不到；重启后知识库、目录、文档、会话和引用快照都在。
- 服务商接受 `stream_options.include_usage`，无需关闭流式。
- 浏览器验收：用户切换、文件表、历史会话、实时流式回答、多文档引用（DOCX 标题路径、PDF 页码）、工具轨迹（含并行调用、步数、token、模型）正常。

**交给 C2 的问题：**
1. 时间显示错误：后端时间是 UTC（无时区后缀），前端按本地时间解析，显示早/晚 8 小时。解析时按 UTC 处理。
2. 回答中的 Markdown（`**加粗**`、列表）原样显示星号。主 Agent 批准引入 `react-markdown`（不启用原始 HTML），或自行实现加粗/列表/段落的轻量渲染；`[cN]` 引用徽标需保留。
3. 回答结束重新拉取会话后，消息列表滚回顶部，应保持在底部。

### Round 2 — D2 上午到下午（并行）

| 任务 | 内容 | 依赖 |
| --- | --- | --- |
| B2 工具失败降级 | 工具超时/失败分类；有限重试（同参数最多 1 次）；`read_document` 失败转用已检索片段；部分回答并说明缺失；全部计入步数上限；轨迹中标出降级动作 | Round 1 |
| E MCP＋Skill | stdio MCP Server 复用同一工具注册表与权限（`PK_MCP_USER_ID`）；`skills/knowledgepilot/SKILL.md`、Claude Desktop/Inspector 调用示例 | Round 1 工具层；依赖待确认 |
| D2 评测 | `eval/corpus/`（公开资料）、`eval/cases.jsonl` 20 条（单库、多库、多轮指代、工具选择、引用定位、无结果、异常各若干）；`eval/run.py` 输出检索 Hit@5/MRR（无需模型）与回答/引用/工具成功率/耗时（真实模型）；产出基线结果 | Round 1 |
| A2 算法优化 | 已提前完成：BM25、BGE-M3 向量、BM25＋向量 RRF 混合检索（`PK_RETRIEVER`）。Round 2 只做 D2 评测对照与调参 | D2 检索评测 |
| C2 前端完善 | 引用点击跳转原文范围、轨迹中的错误/降级展示、空状态与异常提示、删除确认、解析状态自动刷新 | Round 1 |

### Integration 2 + 收尾（D2 下午到晚上）

1. 合并 A2 → B2 → E → C2 → D2，门禁同上，另加 `uv run --directory backend pytest tests/test_mcp.py`。
2. D3：全量回归、真实模型 smoke、20 条评测在 `cosine` 与最佳策略下各跑一次，写 `docs/eval-report.md`（含失败分析）。
3. 主 Agent：更新 README（一条命令启动、选型理由与替代方案）、`docs/contracts.md` 作为 API 文档终版、`docs/design.md`（分层、Agent 循环、上下文、降级）；从干净 clone 按 README 复现一次。

## 4. Subagent 通用要求

派发时每个 brief 都包含：目标、拥有的文件、只读参考文件、契约章节、验收命令。每个 Subagent 结束时必须返回：

1. 已实现功能
2. 修改文件
3. 测试命令及结果（贴关键输出）
4. 新增依赖（只列需求，不自行安装进锁文件）
5. 未解决问题
6. 是否修改公共接口（是则列出差异，等待主 Agent 决定）

禁止：用假数据实现最终功能；静默吞异常（捕获后必须记录日志并转为结构化错误）；未经确认引入 Redis、Celery、向量数据库服务、消息队列、微服务；修改不属于自己的文件。

## 5. 风险与应对

| 风险 | 应对 |
| --- | --- |
| 所选模型的流式 tool calling 不稳定 | 集成 1 前用真实模型跑 smoke；`OpenAICompatClient` 同时支持 `stream=false` 回退开关 |
| B 工作量最大，成为关键路径 | Phase 0 先把路由、schemas、配置、错误、日志做完；降级和 MCP 拆到第二轮并交给 B2 / E |
| SQLite 在流式请求中长时间持有连接 | 约定每次写入短连接短事务，不跨 `await` 持有连接 |
| 前端联调晚 | C1 先接已存在的接口；新接口严格按契约类型编码，集成时只改适配层 |

## 6. 已确认决策（2026-11-22）

1. 模型：OpenAI 兼容服务，URL 与 API Key 由用户提供，只写入 `backend/.env`（已被 Git 忽略），不进入代码、日志或提交。
2. MCP：使用官方 Python SDK `mcp`（主 Agent 在 Round 2 前加入 `pyproject.toml`）。
3. 前端：Vite + React + TypeScript + Tailwind CSS v4 + shadcn/ui。初始化按官方 Vite 指引（本机无 pnpm，使用 npm）：`npm create vite@latest frontend -- --template react-ts` → `npm i tailwindcss @tailwindcss/vite` → 配置 `@/*` 路径别名 → `npx shadcn@latest init` → `npx shadcn@latest add <组件>`。C 在 `frontend/` 下安装 shadcn 官方 Agent Skill（`npx skills add shadcn/ui`，依赖 `components.json`），遵循其组合规则（表单用 `FieldGroup`、语义色等）。

## 7. 待确认

1. 提交当前基线（worktree 隔离的前提）。

## 8. 当前增量：BGE-M3 与混合检索（2026-11-23）

用户已确认 OpenAI 兼容 `/embeddings`、模型 ID `bge-m3`。本增量由主 Agent 串行实现，
临时接管 `services/{ingestion,retrieval,embeddings}.py`、`api/{documents,search}.py`、
`app/reindex.py` 与 `tests/test_retrieval.py`；共享配置、迁移与文档仍由主 Agent 维护。
范围：SQLite 持久化真实稠密向量、BM25、vector、BM25+vector 的 RRF hybrid、离线测试及重建命令。
覆盖 Round 2 原 hybrid（词频余弦与 BM25 融合）定义；不引入向量数据库服务或新依赖。
