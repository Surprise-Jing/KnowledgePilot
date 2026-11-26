# AGENTS.md — KnowledgePilot 协作规则

所有参与开发的 Agent（主 Agent 与 Subagent）开始工作前必须阅读本文件、[docs/contracts.md](docs/contracts.md) 和 [docs/design.md](docs/design.md)。

## 1. 项目

个人知识管理智能助手：多知识库 → 上传解析 → 检索 → 模型自主调用工具 → 流式回答 → 引用与调用轨迹。需求见 [docs/README.md](docs/README.md)。

- 后端：Python 3.11+、FastAPI、SQLite、uv（`backend/`）
- 前端：Vite + React + TypeScript + Tailwind CSS v4 + shadcn/ui（`frontend/`）
- 模型：OpenAI 兼容 Chat Completions（tools），配置在 `backend/.env`
- MCP：官方 Python SDK `mcp`，stdio

## 2. 常用命令

```bash
uv run --directory backend pytest -q
uv run --directory backend ruff check app tests
uv run --directory backend ruff format --check app tests
uv run --directory backend uvicorn app.main:app --host 127.0.0.1 --port 8000
npm --prefix frontend run build
npm --prefix frontend test
```

## 3. 不可违反的规则

1. **共享契约只能由主 Agent 修改**：`docs/contracts.md`、`AGENTS.md`、`backend/app/{main,config,db,errors,schemas,deps,logging_setup}.py`、`backend/app/llm/base.py`、`backend/.env.example`、`backend/pyproject.toml`、`backend/uv.lock`、`frontend/package.json` 中白名单之外的依赖、`.gitignore`、`README.md`。需要改动时停下来，在报告中提出。
2. **只修改主 Agent 分配任务时指定的文件**。唯一例外：让契约测试通过后，可删除该测试上的 `@pytest.mark.xfail(strict=True, …)` 那一行。
3. **不提交假数据模拟的最终功能**。测试替身（如 `ScriptedLLM`）只能放在 `tests/` 下并通过依赖注入使用；产品代码不得有假模型、假检索结果或硬编码演示数据。
4. **不静默吞异常**。捕获后要么转换为契约中的结构化错误，要么 `logger.exception` 记录并转为 `internal_error`；不得 `except: pass`。
5. **不新增大型依赖**（Redis、Celery、消息队列、向量数据库服务、微服务等）。任何新依赖都在报告中提出，由主 Agent 写入依赖文件。
6. **分层**：路由（`app/api`）→ 服务（`app/services`）→ 数据库。工具层（`app/tools`）、Agent（`app/agent`）、MCP 只调用服务层，不直接拼 SQL。
7. **每项功能都有自动化测试**，测试不依赖网络和模型密钥。交付前门禁命令全部通过。
8. **安全**：不把密钥写入代码、日志、测试或提交；日志不输出文档全文；只用公开或脱敏数据。
9. 数据库连接用短事务，不跨 `await` 持有。
10. 不 push，不改 Git 配置；在自己的 worktree 分支提交，由主 Agent 合并。

## 4. 代码风格

- Python：Ruff（行宽 100），类型注解，面向用户的错误消息用中文，代码注释用英文，说明“为什么”。
- 前端：TypeScript strict；组件使用 shadcn/ui，先用 shadcn Skill / `npx shadcn@latest docs|search` 查找组件；语义色，不硬编码颜色。
- 不做与任务无关的重构和格式化。

## 5. Subagent 交付报告格式

完成后必须按以下格式返回：

1. **已实现功能**
2. **修改文件**（新增 / 修改 / 删除）
3. **测试命令及结果**（贴关键输出，包括失败项）
4. **新增依赖**（名称、版本、理由；未安装进锁文件）
5. **未解决问题**（含已知限制和风险）
6. **是否修改公共接口**（否 / 是：列出与 contracts.md 的差异及理由）
