# 任务说明 D：测试、评测与代码审查

> 交给测试 Agent 的完整任务说明。开始前先读 [AGENTS.md](../../AGENTS.md)、[docs/contracts.md](../contracts.md)（全部）、[backend/app/schemas.py](../../backend/app/schemas.py)、[backend/app/llm/base.py](../../backend/app/llm/base.py) 和现有测试 `backend/tests/`。

D 分三个阶段：

| 阶段 | 时间 | 内容 |
| --- | --- | --- |
| D1 | 与 A1/B1 并行 | 契约测试（预期失败）、现有后端审查 |
| D2 | Round 2 | 20 条评测集、评测脚本、基线结果 |
| D3 | 每次集成后 | 回归测试、真实模型冒烟脚本、合并代码审查 |

## 0. 工作方式

- 从 `dev` 分支创建独立 worktree，分支名按阶段：`d1-contract-tests`、`d2-eval`、`d3-regression`。只在该分支提交，不 push、不合并。
- **只修改**：`backend/tests/contract/**`、`backend/tests/e2e/**`、`scripts/smoke.py`、`docs/reviews/**`、`eval/**`、`docs/eval-report.md`。
- **不修改 `backend/app/**`**。发现产品代码缺陷时写入审查报告，由主 Agent 分派。
- 测试以契约为准，不要按某个实现去猜。契约不清楚的地方写进报告的“未解决问题”，不要自行假设。
- 除 `scripts/smoke.py` 和 `eval/` 外，测试不得访问网络或依赖模型密钥；每次测试使用 `tmp_path` 下的独立数据目录。

## 1. D1：契约测试先行

### 1.1 基础设施

**`backend/tests/fakes/llm.py`：`ScriptedLLM`**，由 B1 提供并维护（D 复用；需要新能力时在报告中提出）。它应满足：

- 构造参数为若干“轮”。每轮是 `LLMEvent` 列表或一个异常（如 `AppError(504, "model_timeout", …)`）。每次调用 `stream()` 消费一轮；轮数用完时抛出断言错误，提示脚本不足。
- 记录每次调用收到的 `messages` 和 `tools`（`calls` 属性），供断言上下文内容。
- 提供构造辅助：`text("…")`（可拆成多个 `TextDelta`）、`tool_call(name, args_dict, id=None)`、`usage(p, c)`。
- 提供工厂辅助 `scripted_factory(llm)`，返回可赋给 `app.state.llm_factory` 的函数。

**`backend/tests/contract/conftest.py`**：

- `settings`（`Settings(_env_file=None, data_dir=tmp_path)`）、`client`（`TestClient(create_app(settings))`）、按用户发请求的辅助（`as_user(client, "alice")` 或带头的辅助函数）。
- `make_kb()`、`make_folder()`、`upload(…, folder_id=None)`（TestClient 下后台任务在响应后同步执行，上传返回后即可断言状态）。
- `install_llm(client, ScriptedLLM(...))`：设置 `client.app.state.llm_factory`。
- `read_sse(response) -> list[tuple[str, dict]]`：按空行分帧，忽略注释行，校验 `id` 递增并解析 `data` 为 JSON。

### 1.2 标记规则

每个尚未实现的契约测试标记为预期失败，并写明负责人：

```python
@pytest.mark.xfail(strict=True, reason="A: folders API (contracts §3.2)")
```

`strict=True` 保证实现完成后测试意外通过会报错，提醒实现方删除该标记（这是 A/B 唯一可以改动 D 文件的地方）。**主干上 `pytest` 必须保持全绿。** 已经实现、本应通过的行为（例如统一错误结构）不加标记。

### 1.3 必须覆盖的契约测试

**`test_knowledge_bases_contract.py`（A）**
- 重命名成功并更新 `updated_at`；重命名其他用户的知识库返回 404 `knowledge_base_not_found`
- 删除后：列表中消失，其文档的磁盘原文件被删除，检索该库返回 404
- 重复删除返回 204；删除其他用户的知识库返回 204 且该库仍然存在、可被其所有者访问

**`test_folders_contract.py`（A）**
- 创建至少 3 层嵌套目录；列表为扁平结构且 `parent_id` 正确
- 同级重名 409 `name_conflict`；不同父目录下同名允许
- `parent_id` 指向其他知识库的目录 → 404 `folder_not_found`
- 重命名；`{"parent_id": null}` 移到根目录；`{}` → 422 `invalid_argument`
- 移动到自身、移动到子孙目录 → 409 `folder_cycle`
- 删除空目录 204；删除非空目录 409 `folder_not_empty`；`recursive=true` 删除整棵子树，其中文档的原文件被删除、检索不到
- 重复删除 204；修改其他用户的目录 → 404
- 名称包含 `/` → 422

**`test_documents_manage_contract.py`（A）**
- 上传时指定 `folder_id`，文档 `folder_id` 正确；`folder_id` 属于其他知识库 → 404 `folder_not_found`
- 同库重复内容返回已有记录且 `duplicate: true`；首次上传 `duplicate: false`
- 列表 `folder_id` + `recursive` 语义：省略 `folder_id` 默认返回全部；`recursive=false` 只返回直接子项
- 重命名：`file_type` 不变，片段不变，检索结果中的 `filename` 为新名称
- 移动到同库其他目录；移动到其他知识库的目录 → 404 `folder_not_found`；`{}` → 422
- 删除幂等：第二次删除 204（现有实现返回 404，此用例标记 xfail 给 A）

**`test_services_contract.py`（A 的 M1，B 依赖）**
- `list_files`：分页（`offset`、`limit`、`total`）、排序（`folder_path`, `filename`, `id`）、`folder_path` 为从根开始的名称数组、`recursive` 语义、其他用户的知识库 → `knowledge_base_not_found`、`limit` 越界 → `invalid_argument`
- `read_document_range`：超过 `max_chars` 时 `truncated=True` 且 `next_ordinal` 正确、用 `next_ordinal` 继续读能读完全文且不重复、至少返回 1 个片段、`status` 为 `failed` 时 `document_not_ready`（`retryable=False`）、`queued` 时 `retryable=True`（可直接改数据库状态构造）、`start_ordinal` 越界 → `invalid_argument`、其他用户 → `document_not_found`

**`test_tools_contract.py`（B）**
- `list_tools()` 恰好包含 `list_knowledge_bases`、`search_chunks`、`list_files`、`read_document`；`openai_tool_specs()` 中每个工具都有中文 `description`、`parameters.additionalProperties == false`、必填项与契约一致
- `execute` 结构化错误：非法 JSON、缺必填参数、类型或范围错误 → `invalid_argument`；未知工具 → `invalid_argument`
- 范围：`search_chunks` 指定未选择的知识库 → `out_of_scope`；`read_document` 读取未选择知识库中的文档 → `out_of_scope`；其他用户的文档 → `document_not_found`
- 无结果时 `ok: true` 且 `hits: []`
- 标记：同一 `MarkerAllocator` 下，`search_chunks` 与 `read_document` 返回的同一片段标记相同，新片段依次为 `c1`、`c2`…

**`test_conversations_contract.py`（B）**
- 会话创建、列表（按 `updated_at` 倒序）、详情、删除幂等；其他用户的会话 → 404
- 流开始前的错误以 JSON 返回：会话不存在 404、知识库不属于当前用户 404、参数错误 422、未配置模型（`llm_factory` 为 `None` 且 `llm_model` 为空）503 `model_not_configured`
- 首次提问后会话标题变为问题前 30 字

**`test_sse_contract.py`（B，使用 `ScriptedLLM`）**
- 响应头 `text/event-stream`；第一条为 `run_started`，最后一条为 `done` 且只出现一次；`id` 从 1 递增
- 一次检索再回答：`tool_call` 先于对应 `tool_result`，二者 `call_id` 相同；所有 `token` 拼接结果等于会话详情中持久化的回答
- 引用过滤：脚本回答同时写 `[c1]`（工具返回过）和 `[c9]`（未返回）→ 只有 `c1` 的 `citation` 事件；`GET /api/runs/{id}` 的 `dropped_citations == ["c9"]`；会话详情中该消息的引用带结构化 `location`
- 工具错误回传：脚本调用 `read_document` 传入不存在的 ID → `tool_result.status == "error"` 且 `error.code == "document_not_found"`；第二次模型调用收到的 `messages` 中包含该错误的 `tool` 消息
- 最大步数：`agent_max_steps=2`，脚本每轮都请求工具 → `error` 事件 `max_steps_reached`，`done.stop_reason == "max_steps"`
- 模型异常：脚本抛出 `model_timeout` → `error` 事件后接 `done`，`stop_reason == "timeout"`，消息状态 `failed`；`model_error` 同理对应 `stop_reason == "model_error"`
- 多轮：第二次提问时模型收到的 `messages` 包含上一轮的问题和回答（其中 `[cN]` 已删除），并包含列出上一轮被引用文档文件名的会话焦点
- 轨迹持久化：`GET /api/runs/{id}` 返回每次工具调用的参数、状态、摘要和耗时；其他用户访问 → 404 `run_not_found`

### 1.4 审查现有后端

输出 `docs/reviews/round0.md`。每条问题一行，按严重程度排序：

| 严重程度 | 位置（文件:行） | 问题 | 复现或依据 | 建议 | 负责人（A / B / 主 Agent） |
| --- | --- | --- | --- | --- | --- |

重点检查：解析与删除的竞态、重试并发、幂等性、异常是否被吞、上传文件名与路径安全、用户 ID 校验、事务边界、错误码是否符合契约、检索性能。只报告有依据的问题，不确定的标注“待确认”。

### 1.5 D1 验收

- `uv run --directory backend pytest -q` 全绿（未实现部分全部为 xfail）
- `uv run --directory backend ruff check app tests`、`ruff format --check app tests` 通过
- 报告中列出每个测试文件的用例数量、xfail 数量及对应负责人

## 2. D2：评测集与评测脚本（Round 2）

### 2.1 数据

- `eval/corpus/`：3～5 份公开或脱敏资料，覆盖 TXT、PDF、DOCX，分属至少 2 个知识库。DOCX / PDF 可用脚本 `eval/build_corpus.py` 从公开文本生成，并在 `eval/corpus/README.md` 注明来源和许可。
- 内容需要支撑多库、指代、对比和“资料中没有答案”的问题。

### 2.2 用例：`eval/cases.jsonl`，共 20 条

| 类别 | 数量 | 考察点 |
| --- | --- | --- |
| 单库事实 | 4 | 检索命中、回答正确、引用位置正确 |
| 多库范围 | 3 | 只在所选库检索，不串库 |
| 多轮指代 | 3 | “这份文档”“刚才提到的方法” |
| 工具选择 | 3 | 需要 `list_files` / `read_document` 的问题 |
| 引用定位 | 3 | 引用指向正确的页、行或段落 |
| 无结果 | 2 | 明确说明资料中没有，不编造 |
| 异常 | 2 | 例如文档被删除后提问、极小的模型超时 |

每条字段：`id`、`category`、`knowledge_bases`（使用的语料）、`turns`（一个或多个问题）、`expect`：`relevant`（期望命中的文件和位置范围）、`answer_keywords`（关键词或要点）、`expect_tools` / `forbidden_tools`（可选）、`expect_no_answer`（可选）、`expect_stop_reason` 或 `expect_error_code`（可选），以及 `judge`（判定规则说明）。

### 2.3 脚本：`eval/run.py`

- 每次在全新的临时数据目录中建库并导入语料，等待解析完成。
- `--mode retrieval`：只调用检索，不需要模型；对 `PK_RETRIEVER=cosine|bm25|vector|hybrid` 分别计算 Hit@1、Hit@3、Hit@5、MRR。这是算法优化（A2）的对照基准。
- `--mode agent`：从 `backend/.env` 读取真实模型配置运行完整对话；统计回答要点命中率、引用正确率（引用位置与 `relevant` 重叠）、工具调用成功率、平均工具调用次数、首字延迟与总耗时（P50 / P95）、token 用量。
- 结果写入 `eval/results/<时间>-<模式>-<策略>.json`（已被 Git 忽略），并输出 Markdown 汇总表。
- 不在日志或结果中写入 API Key。

### 2.4 报告：`docs/eval-report.md`

评测集说明、指标定义、基线结果、各检索策略对照表、失败用例逐条分析（失败类型：检索未命中 / 工具选择错误 / 引用错误 / 回答错误 / 异常处理）、改进方向。

## 3. D3：回归与冒烟（每次集成后）

- 全量 `pytest`、Ruff；前端 `build`、`test`。
- `scripts/smoke.py`：对已启动的服务（默认 `http://127.0.0.1:8000`）用真实模型走完整验收流程，任何一步失败即以非零状态退出，并打印失败步骤：
  1. alice 建库 → 建两层目录 → 上传 `examples/` 中的 TXT 以及生成的 PDF、DOCX → 轮询直到全部成功
  2. bob 检索 alice 的知识库返回 404
  3. 新建会话提问，SSE 中出现 `tool_call`、`token`、`citation`，以 `done`（`answered`）结束
  4. 追问“这份文档还提到了什么”，回答引用的文档与上一轮一致
  5. `GET /api/runs/{id}` 有工具调用记录
  6. 删除文档后检索不到其内容
- 对合并后的差异做代码审查，写入 `docs/reviews/round<N>.md`，格式同 1.4。

## 4. 交付报告

按 AGENTS.md 第 5 节返回：已实现内容、修改文件、测试命令及结果、新增依赖、未解决问题（含契约中不明确的地方）、是否需要修改公共接口。D1 另附各测试文件的用例数和 xfail 负责人统计。
