---
name: knowledgepilot
description: 通过 KnowledgePilot MCP Server 检索和阅读用户的个人知识库（上传到 KnowledgePilot 的 PDF、Word、TXT 文档），并带引用标记回答。当用户询问“我的笔记/资料/知识库/上传的文档里”有什么、要求依据自己的文档回答问题、查找某份文件或某段原文、或需要带出处的答案时使用。不适用于通用常识问答、网络搜索或修改知识库（本 Server 只读）。
---

# KnowledgePilot 知识库 Skill

KnowledgePilot 是个人知识管理助手：用户在 Web 界面建知识库、上传文档，系统解析成带位置信息的片段。本 Skill 通过 stdio MCP Server（服务名 `knowledgepilot`）把应用内 Agent 使用的同一组只读工具暴露给 MCP 客户端（Claude Code、Claude Desktop、MCP Inspector 等）。

## 适用场景

- 用户问“我的资料里关于 X 是怎么说的”“帮我在知识库里找…”“根据我上传的文档回答…”。
- 需要给出带出处（文件名 + 行/页/段落位置）的回答。
- 用户想知道有哪些知识库、某个知识库/目录下有哪些文件。
- 需要连续阅读某份文档的上下文（检索片段不足以回答时）。

不适用：通用知识问答（不需要用户资料）、联网搜索、上传/删除/重命名文档（请在 KnowledgePilot Web 界面操作）。

## 前置条件

1. 安装 Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)，克隆仓库后在仓库根目录执行一次 `uv sync --directory backend`。
2. 有数据：先用 Web 应用（`./start.sh`，打开 http://127.0.0.1:8000）建知识库并上传文档，等文档状态变为“解析成功”。MCP Server 与 Web 应用读同一个数据目录（默认 `backend/data`，可用 `PK_DATA_DIR` 指定），两者可以同时运行。Server 启动时只执行数据库迁移，不做中断恢复，不会影响 Web 端正在进行的解析或回答。
3. 用户身份：MCP 没有 HTTP 头，用环境变量 `PK_MCP_USER_ID` 指定以哪个用户的身份访问（默认 `demo`，与 Web 端默认用户一致）。只能访问该用户的知识库。
4. 检索策略：Server 启动目录为 `backend/`，会读取 `backend/.env`（与 Web 应用相同）。
   - `PK_RETRIEVER=cosine` 或 `bm25`：不需要 `backend/.env`，也不需要任何模型或密钥。
   - `PK_RETRIEVER=vector` 或 `hybrid`（`.env.example` 的默认值）：需要 `PK_EMBEDDING_BASE_URL`、`PK_EMBEDDING_MODEL`（以及服务要求的 `PK_EMBEDDING_API_KEY`），并且已建立向量索引（`uv run --directory backend python -m app.reindex --user-id <用户>`，需在 Web 服务停止时运行）。
   - 本 Server 不调用聊天模型，`PK_LLM_*` 不需要。
5. 在 MCP 客户端注册 Server，见 [mcp-config/](mcp-config/)。

## 工具说明

MCP 工具与应用内 Agent 工具**一一对应、完全相同**：Server 直接从后端共享注册表（`app.tools.registry`）读取工具名、描述和参数 JSON Schema，调用也走同一个 `execute_async`（参数校验、权限、超时、错误码、工具层降级都相同）。差别只有两点：

| 项目 | 应用内 Agent | MCP |
| --- | --- | --- |
| 可访问的知识库（`allowed_kb_ids`） | 本次提问选择的知识库 | `PK_MCP_USER_ID` 在**调用时**拥有的全部知识库 |
| 引用标记（`c1`、`c2`…）的有效范围 | 一次提问（run） | 一个 MCP 会话（Server 进程）；同一片段在会话内标记不变 |

Agent 层的自动重试与模型重试不适用于 MCP，由客户端自行决定是否重试（看 `retryable`）。

### `list_knowledge_bases`

- 何时用：不清楚有哪些知识库，或需要知识库 ID 再调用 `list_files` / 限定 `search_chunks` 范围。
- 参数：无（`{}`）。
- 返回：`{knowledge_bases: [{id, name, document_count}]}`。
- 错误码：`tool_timeout`、`internal_error`。

### `search_chunks`

- 何时用：回答任何需要依据资料的问题时**首先**使用；结果不理想时换关键词或同义改写再查。
- 参数：`query`（必填，1–500 字）；`knowledge_base_ids`（可选，1–20 个；省略时检索全部可用知识库）；`top_k`（1–10，默认 5）。
- 返回：`{hits: [{marker, chunk_id, document_id, knowledge_base_id, filename, location, score, content}]}`；无结果时 `hits: []`（不是错误）。`location.label` 是人类可读位置，如“第 1–3 行”“第 2 页”。降级时额外带 `degraded`（见“错误处理”）。
- 错误码：`invalid_argument`、`out_of_scope`（`knowledge_base_ids` 含不属于该用户的库）、`embedding_not_configured` / `embedding_error` / `embedding_timeout` / `embedding_index_not_ready`（vector/hybrid 策略，且未降级时）、`tool_timeout`、`internal_error`。

### `list_files`

- 何时用：用户问有哪些文件、某个目录下有什么，或需要先找到文档 ID 再用 `read_document`。
- 参数：`knowledge_base_id`（必填）；`folder_id`（可选，省略为根目录）；`recursive`（默认 `true`）；`offset`（≥0，默认 0）；`limit`（1–50，默认 20）。
- 返回：`{files: [{document_id, knowledge_base_id, folder_id, folder_path, filename, file_type, status, error}], total, offset, limit}`；`status` 为 `success` 的文档才能检索和阅读。
- 错误码：`invalid_argument`、`out_of_scope`、`folder_not_found`、`tool_timeout`、`internal_error`。

### `read_document`

- 何时用：需要某份文档的完整上下文、用户点名某份文档，或检索片段不足以回答。
- 参数：`document_id`（必填）；`start_ordinal`（从第几个片段开始，默认 0）；`max_chars`（500–4000，默认 3000）。
- 返回：`{document_id, filename, content, chunks: [{marker, ordinal, location}], truncated, next_ordinal, total_chunks}`；`content` 中每段前缀 `[cN]`。`truncated=true` 时用 `next_ordinal` 作为 `start_ordinal` 继续读。
- 错误码：`invalid_argument`、`document_not_found`（不存在或属于其他用户）、`document_not_ready`（仍在解析时 `retryable=true`；解析失败时 `false`）、`tool_timeout`、`internal_error`。

## 推荐调用顺序

1. `list_knowledge_bases` —— 确认有哪些库、是否有文档（`document_count`）。用户只问内容时可跳过，直接第 2 步。
2. `search_chunks` —— 用问题中的关键词检索；必要时换 1–2 次关键词。
3. `read_document` —— 片段不够时，对最相关的 `document_id` 连续阅读上下文。
4. 作答：只依据工具返回的内容，在句末用方括号标注引用标记，如“验收日期定在三月十五日 [c1]”，并在末尾列出 `marker → filename（location.label）`。资料中没有的内容明确说“知识库中未找到”，不要编造；不要虚构标记。

## 调用示例

可直接运行的示例（不需要任何 AI 客户端）：[scripts/demo_client.py](scripts/demo_client.py)，用法见 [README.md](README.md)。

`search_chunks` 请求（MCP `tools/call`）：

```json
{"name": "search_chunks", "arguments": {"query": "删除文档时如何清理索引", "top_k": 3}}
```

成功响应（`isError: false`，`content[0].text` 为下面的 JSON，`structuredContent` 与之相同）：

```json
{
  "hits": [
    {
      "marker": "c1",
      "chunk_id": "4b605bc3-…",
      "document_id": "53b5ae95-…",
      "knowledge_base_id": "9b59c785-…",
      "filename": "knowledge.txt",
      "location": {"kind": "txt_lines", "start": 1, "end": 3, "table": null,
                   "heading_path": [], "char_start": null, "char_end": null,
                   "label": "第 1–3 行"},
      "score": 0.465267,
      "content": "KnowledgePilot 支持 PDF、Word 和 TXT 文档解析。\n…删除文档时应同步清理索引…"
    }
  ]
}
```

`read_document` 请求与响应：

```json
{"name": "read_document", "arguments": {"document_id": "53b5ae95-…", "max_chars": 1000}}
```

```json
{
  "document_id": "53b5ae95-…",
  "filename": "knowledge.txt",
  "content": "[c1] KnowledgePilot 支持 PDF、Word 和 TXT 文档解析。\n…",
  "chunks": [{"marker": "c1", "ordinal": 0, "location": {"kind": "txt_lines", "start": 1, "end": 3, "label": "第 1–3 行", "…": "…"}}],
  "truncated": false,
  "next_ordinal": null,
  "total_chunks": 1
}
```

同一会话内同一片段的标记保持不变（上例中检索和阅读都是 `c1`）。

错误响应（`isError: true`，`content[0].text`）：

```json
{"code": "document_not_found", "message": "文档不存在", "retryable": false}
```

## 错误处理

- 所有错误都是 `isError: true` + JSON `{"code", "message", "retryable"}`，错误码与 Web API / 应用内 Agent 相同（见 `docs/contracts.md` §2）。`message` 是中文、可直接转述给用户。
- `invalid_argument`：参数不符合 Schema（缺必填项、超范围、多余参数、未知工具）。按 `message` 修正参数后重试，不要原样重试。
- `*_not_found`、`out_of_scope`：资源不存在或不属于当前用户（为防探测，两者不区分）。不要重试；先用 `list_knowledge_bases` / `list_files` 拿正确的 ID。
- `document_not_ready`：`retryable=true` 表示仍在解析，稍后再读；`false` 表示解析失败，请用户在 Web 界面重试解析。
- `tool_timeout`、`internal_error`、`embedding_error`、`embedding_timeout`：`retryable=true`，可稍后重试一次；仍失败就如实告知用户。
- `embedding_not_configured`、`embedding_index_not_ready`：配置问题，告知用户检查 `backend/.env` 或先重建向量索引；也可以让用户把 `PK_RETRIEVER` 设为 `bm25`。
- **降级（`data.degraded`）**：结果 `isError: false` 但 `data` 中带 `degraded` 时，表示工具层已自动改用替代方式完成：
  - `search_chunks` 带 `{"kind": "bm25", "reason": "<原错误码>"}`：向量检索服务异常，已改用关键词检索，结果可用但语义召回可能变差。
  - `read_document` 带 `{"kind": "retrieved_snippets", "reason": "<原错误码>"}`，且 `truncated=true`：文档无法完整读取，只返回本会话已检索到的该文档片段。
  - 回答时照常引用这些片段的标记，并向用户简要说明发生了降级。权限、范围、参数类错误不会降级。

## 限制

- 只读：不能创建知识库、上传、删除或重新解析文档。
- 用户身份由 `PK_MCP_USER_ID` 在启动时固定，一个 Server 进程只服务一个用户；可访问范围是该用户的全部知识库，无法像 Web 端那样只选部分（可用 `knowledge_base_ids` 自行限定检索范围）。
- 引用标记只在一个 MCP 会话内有效，重启 Server 后从 `c1` 重新编号；不要跨会话复用标记。
- 单次结果不做 8000 字符截断（应用内 Agent 会截断），`read_document` 用 `max_chars` 控制长度。
- 只支持 stdio 传输；不提供 HTTP/SSE 传输与 MCP resources/prompts。
- 检索质量取决于 `PK_RETRIEVER`：`cosine` / `bm25` 是关键词匹配，同义改写召回有限。
