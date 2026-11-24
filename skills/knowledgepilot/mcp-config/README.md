# MCP 客户端配置示例

所有示例都用占位符，不含任何密钥：

| 占位符 | 含义 |
| --- | --- |
| `<ABS_REPO>` | 本仓库的绝对路径 |
| `<ABS_PATH_TO_UV>` | `uv` 的绝对路径（`which uv` 查看）；图形界面应用通常拿不到 shell 的 `PATH` |
| `demo` | `PK_MCP_USER_ID`，以哪个用户身份访问知识库 |

Server 以 `backend/` 为工作目录启动，会读取 `backend/.env`（与 Web 应用相同，检索策略与 Embedding 配置都从那里来），不要把密钥写进这些客户端配置。只想用关键词检索、不配置任何服务时，在 `env` 中加 `"PK_RETRIEVER": "bm25"`。

| 文件 | 客户端 |
| --- | --- |
| [claude-code.md](claude-code.md) | Claude Code：`claude mcp add` 命令与 Skill 安装 |
| [claude-code.mcp.json](claude-code.mcp.json) | Claude Code 项目级 `.mcp.json` |
| [claude-desktop.json](claude-desktop.json) | Claude Desktop `claude_desktop_config.json`（macOS：`~/Library/Application Support/Claude/`；Windows：`%APPDATA%\Claude\`），也是大多数客户端通用的 `mcpServers` 格式 |

## MCP Inspector

用官方调试工具手动调用工具（需要 Node.js）：

```bash
npx @modelcontextprotocol/inspector \
  -e PK_MCP_USER_ID=demo \
  uv run --directory <ABS_REPO>/backend python -m app.mcp_server
```

在打开的页面中 Connect → Tools → List Tools，选择 `search_chunks` 填入 `query` 后运行。

## 排错

- Server 的日志（JSON 行）只写到 stderr，stdout 只承载 MCP 协议。客户端通常把 stderr 记到自己的日志里（Claude Code：`claude --debug`；Claude Desktop：`~/Library/Logs/Claude/mcp-server-knowledgepilot.log`）。
- 启动失败（用户标识不合法、数据库版本高于程序等）会在 stderr 打印“KnowledgePilot MCP 启动失败：…”并以非零码退出。
