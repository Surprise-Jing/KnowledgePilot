# Claude Code

把 `<ABS_REPO>` 换成本仓库的绝对路径（例如 `/Users/me/code/pk-assistant`），`demo` 换成要访问的用户标识。

## 命令行注册

```bash
claude mcp add --transport stdio --env PK_MCP_USERNAME=demo knowledgepilot \
  -- uv run --directory <ABS_REPO>/backend python -m app.mcp_server
```

- 默认注册到当前项目的本地配置；加 `--scope user` 对所有项目生效，加 `--scope project` 写入项目根目录的 `.mcp.json`（与团队共享）。
- 需要指定数据目录或检索策略时再加 `--env`，例如 `--env PK_DATA_DIR=<ABS_REPO>/backend/data --env PK_RETRIEVER=bm25`。
- 验证：`claude mcp list` 应显示 `knowledgepilot` 已连接；在会话中输入 `/mcp` 可查看 4 个工具。

## 项目级 `.mcp.json`

内容见同目录 [claude-code.mcp.json](claude-code.mcp.json)，复制到项目根目录并命名为 `.mcp.json`。

## 安装 Skill

把 `skills/knowledgepilot/` 复制或软链接到 Claude Code 的 Skill 目录：

```bash
# 仅当前项目
mkdir -p .claude/skills && ln -s <ABS_REPO>/skills/knowledgepilot .claude/skills/knowledgepilot
# 或对所有项目
mkdir -p ~/.claude/skills && ln -s <ABS_REPO>/skills/knowledgepilot ~/.claude/skills/knowledgepilot
```
