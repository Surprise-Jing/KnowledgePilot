# KnowledgePilot Skill

让 MCP 客户端（Claude Code、Claude Desktop 等）检索和阅读 KnowledgePilot 中的个人知识库，并带引用标记回答。

```
skills/knowledgepilot/
├── SKILL.md               # Skill 本体：适用场景、工具说明、调用顺序、示例、错误处理
├── mcp-config/            # 客户端配置示例（Claude Code、Claude Desktop、MCP Inspector）
└── scripts/demo_client.py # 不依赖 AI 客户端的可复现调用示例
```

## 1. 准备

```bash
uv sync --directory backend          # 安装后端依赖（含官方 mcp SDK）
./start.sh                           # 启动 Web 应用，建知识库并上传文档（可选：已有数据可跳过）
```

只用 `PK_RETRIEVER=cosine` 或 `bm25` 时不需要 `backend/.env`；`vector` / `hybrid` 需要在 `backend/.env` 配置 Embedding 并重建索引，详见 [SKILL.md](SKILL.md#前置条件)。

## 2. 启动 MCP Server（stdio）

```bash
PK_MCP_USERNAME=demo uv run --directory backend python -m app.mcp_server
```

通常不需要手动启动：MCP 客户端会按配置自动拉起。手动启动后它会等待 stdin 上的 JSON-RPC 消息，`Ctrl-C` 退出。

## 3. 注册到客户端并安装 Skill

见 [mcp-config/README.md](mcp-config/README.md)。Claude Code 最简方式：

```bash
claude mcp add --transport stdio --env PK_MCP_USERNAME=demo knowledgepilot \
  -- uv run --directory <ABS_REPO>/backend python -m app.mcp_server
mkdir -p ~/.claude/skills && ln -s <ABS_REPO>/skills/knowledgepilot ~/.claude/skills/knowledgepilot
```

之后直接提问，例如“在我的知识库里查一下删除文档时怎么清理索引”。

## 4. 运行调用示例（不需要 AI 客户端）

在仓库根目录执行：

```bash
uv run --directory backend python ../skills/knowledgepilot/scripts/demo_client.py \
  --query "删除文档时如何清理索引" --user demo
```

脚本用官方 SDK 的 stdio 客户端启动 Server，依次执行 `list_tools` → `list_knowledge_bases` → `search_chunks` → `read_document`（第一个命中的文档），把每次请求与响应以 JSON 打印到 stdout；Server 日志在 stderr。全部成功时退出码为 0。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--query` | 必填，检索语句 |
| `--user` | 用户标识，覆盖 `PK_MCP_USERNAME`（默认 `demo`） |
| `--top-k` | 检索片段数，默认 3 |
| `--document-id` | 指定要阅读的文档，默认取第一个命中 |
| `--data-dir` | 数据目录，覆盖 `PK_DATA_DIR`（默认 `backend/data`） |
| `--server-cwd` | Server 工作目录，默认 `backend/`（会读取其中的 `.env`）；指定空目录可完全不读 `.env` |

当前 shell 中的 `PK_*` 环境变量会传给 Server，例如 `PK_RETRIEVER=bm25 uv run …`。
