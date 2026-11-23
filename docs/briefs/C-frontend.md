# 任务说明 C：前端（C1 主流程 + C2 完善）

> 交给前端 Agent 的完整任务说明。开始前先读 [AGENTS.md](../../AGENTS.md)、[docs/contracts.md](../contracts.md)（重点第 1、2、3、4、7 节）和 [backend/app/schemas.py](../../backend/app/schemas.py)。

## 1. 目标

实现一个可操作的单页应用，让用户**不调用后端接口**即可完成：管理知识库、目录和文件 → 上传并查看解析状态 → 选择知识库提问 → 看到流式回答 → 点击引用查看原文 → 查看本次工具调用轨迹 → 继续追问。

## 2. 工作方式

- 从 `dev` 分支创建独立 worktree 和分支 `c1-frontend`，只在该分支提交，不 push、不合并。
- **只修改 `frontend/**`**。需要后端或契约改动时，在报告中提出，不要改后端代码。
- 不允许在产品代码（`frontend/src`）里写假数据、mock 服务或硬编码演示数据。请求一律发给真实后端；测试中的 fetch mock 只能放在测试文件里。
- 部分接口后端尚未实现（见第 4 节）：按契约类型编码，接口返回 404/405 时界面显示后端错误即可，集成阶段由主 Agent 联调。

## 3. 技术栈与初始化

Vite + React + TypeScript（strict）+ Tailwind CSS v4 + shadcn/ui。本机没有 pnpm，使用 npm：

```bash
npm create vite@latest frontend -- --template react-ts
npm --prefix frontend install tailwindcss @tailwindcss/vite
npm --prefix frontend install -D @types/node
```

按 [shadcn Vite 指引](https://ui.shadcn.com/docs/installation/vite) 配置 `src/index.css`（`@import "tailwindcss";`）、`tsconfig.json` / `tsconfig.app.json` 的 `@/*` 别名、`vite.config.ts`（`react()`、`tailwindcss()`、`@` 别名），然后在 `frontend/` 下执行：

```bash
npx shadcn@latest init
npx skills add shadcn/ui
```

之后用 shadcn Skill、`npx shadcn@latest search` / `docs` 查找组件，用 `npx shadcn@latest add <组件>` 添加，遵循其组合规则（表单用 `FieldGroup`、选项组用 `ToggleGroup`、语义色）。

`vite.config.ts` 增加开发代理：`/api` 和 `/health` → `http://127.0.0.1:8000`。

**依赖白名单**（可直接安装）：`react`、`react-dom`、`vite`、`@vitejs/plugin-react`、`typescript`、Vite 模板自带的 ESLint 相关包、`tailwindcss`、`@tailwindcss/vite`、`@types/node`、shadcn CLI 添加组件时自动安装的依赖（`radix-ui` / `@radix-ui/*`、`lucide-react`、`class-variance-authority`、`clsx`、`tailwind-merge`、`tw-animate-css`、`sonner` 等）、测试用的 `vitest`、`jsdom`、`@testing-library/react`、`@testing-library/user-event`、`@testing-library/jest-dom`。
**白名单之外**（状态管理库、路由、请求库、Markdown 渲染等）需在报告中提出，不要安装。

`package.json` 脚本：`dev`、`build`（`tsc -b && vite build`）、`test`（`vitest run`）、`lint`。

## 4. 接口现状

| 状态 | 接口 |
| --- | --- |
| 已可用 | `GET /health`；`POST/GET /api/knowledge-bases`；`POST/GET /api/knowledge-bases/{id}/documents`；`GET /api/documents/{id}`、`/chunks`；`POST /api/documents/{id}/retry`；`DELETE /api/documents/{id}`；`POST /api/search` |
| A 实现中 | 知识库 `PATCH/DELETE`；目录 `GET/POST /api/knowledge-bases/{id}/folders`、`PATCH/DELETE /api/folders/{id}`；文档 `PATCH`；上传和列表的 `folder_id` / `recursive` 参数 |
| B 实现中 | `/api/conversations` 全部接口；`POST /api/conversations/{id}/messages`（SSE）；`GET /api/runs/{id}` |

字段、错误码、事件以 `contracts.md` 和 `schemas.py` 为准。

## 5. 代码结构（建议）

```text
frontend/src/
  api/
    types.ts          与 schemas.py 一一对应的类型（KnowledgeBaseOut、FolderOut、DocumentOut、Location、
                      SearchHit、ConversationOut/Detail、MessageOut、CitationOut、RunOut、ToolCallOut、
                      各 SSE 事件、ApiErrorBody）
    client.ts         fetch 封装：自动带 X-User-Id；非 2xx 解析 {error:{code,message,retryable}} 抛 ApiError
    sse.ts            SSE 流解析器（纯函数 + 异步迭代器）
    knowledgeBases.ts / folders.ts / documents.ts / conversations.ts
  lib/
    user.ts           当前用户（localStorage，读写包 try/catch）
    tree.ts           扁平目录 → 树
    citations.ts      把回答文本切分为 文本段 / [cN] 标记段
    errors.ts         错误码 → “用户可修正” / “系统错误” 分类
  features/
    knowledge/  files/  chat/  trace/  citations/
  components/ui/      shadcn 组件
  App.tsx
```

## 6. 功能清单（C1，Round 1）

### 6.1 全局

- 顶栏：应用名、**用户切换器**（预置 `demo`、`alice`、`bob`，可输入新用户 ID；切换后重新加载全部数据，用于演示隔离）、后端健康状态。
- 布局：左栏知识库与目录树；中间区域 “文件” / “对话” 两个视图；右栏 “引用” / “工具轨迹” 面板。窄屏下右栏折叠为抽屉。
- 错误展示：所有错误显示后端返回的 `message`。按 `code` 分两类：
  - 用户可修正（`invalid_argument`、`name_conflict`、`folder_cycle`、`folder_not_empty`、`unsupported_format`、`file_too_large`、`empty_file`、`document_not_failed`、`conversation_busy`、`*_not_found`）：就地提示或 toast。
  - 系统错误（`model_not_configured`、`model_error`、`model_timeout`、`internal_error`、`tool_timeout`）：醒目的错误条，`retryable` 为真时提供“重试”。
- 网络断开、后端不可达要有明确提示，不能无限 loading。

### 6.2 知识库与目录

- 知识库列表：创建、选择、重命名、删除（确认对话框说明会删除全部目录和文件）。
- 目录树：任意层级展开/折叠；在任一目录下新建子目录；重命名；移动（选择目标目录或根目录，后端返回 `folder_cycle` 时提示）；删除：先普通删除，收到 `folder_not_empty` 时二次确认“连同子目录和文件一起删除”，再以 `recursive=true` 调用。
- 选中目录决定文件视图的范围和上传目标。

### 6.3 文件

- 文件表：文件名、类型、所在目录、大小、解析状态徽标（排队 / 解析中 / 成功 / 失败）、更新时间、操作。
- 失败行显示失败原因并提供“重试”。
- 存在 `queued` / `parsing` 时每 2 秒轮询列表，全部结束后停止；切换知识库或卸载组件时清理定时器。
- 上传：支持多选文件，上传到当前目录（`folder_id`）；前端先校验扩展名（.txt/.pdf/.docx），仍以后端错误为准；响应 `duplicate: true` 时提示“已存在相同内容的文件”。
- 重命名、移动到其他目录（同一知识库内）、删除（确认）。
- “查看原文”：打开侧边抽屉，调用 `/api/documents/{id}/chunks` 按顺序展示片段及 `location.label`。

### 6.4 对话

- 会话列表：新建、选择、删除；标题使用后端返回值。
- 知识库多选（默认选中当前知识库），未选择时禁止发送。
- 输入框：Enter 发送、Shift+Enter 换行；运行中禁用发送，显示“停止”按钮（`AbortController` 中断请求）。
- 发送后用 `fetch` 读取 SSE 流（不用 `EventSource`），把事件交给一个**纯函数 reducer** 更新状态：
  - `run_started`：记录 run_id 和消息 ID。
  - `token`：追加到当前回答，实时渲染。
  - `tool_call` / `tool_result`：实时更新工具轨迹面板。
  - `citation`：登记引用。
  - `error`：在该条回答下显示错误（按 6.1 分类）。
  - `done`：结束运行状态，显示结束原因（`max_steps` 显示“已达到最大步数”等）；随后重新拉取会话详情，与持久化结果对齐。
- 流异常中断（未收到 `done`）：显示“连接中断”，并重新拉取会话详情。
- 回答中的 `[cN]` 渲染为可点击的引用徽标；在本条消息的引用中找不到的标记按普通文本显示。点击后在右栏高亮对应引用。
- 回答按纯文本渲染（保留换行），不引入 Markdown 库。
- 加载历史会话时，assistant 消息的引用来自 `MessageOut.citations`。

### 6.5 引用面板

- 列出当前选中回答的引用：标记、文件名、`location.label`、片段内容。
- “查看原文”：打开原文抽屉并滚动到、高亮对应 `chunk_id`。`document_id` 为空（源文档已删除）时只展示引用快照，并注明“来源文档已删除”。

### 6.6 工具轨迹面板

- 当前回答：按步骤列出每次工具调用——工具名、参数（JSON 折叠展示）、状态（运行中 / 成功 / 失败）、结果摘要、错误码和消息、耗时。
- 底部显示结束原因、步数、token 用量。
- 历史回答：通过 `GET /api/runs/{run_id}` 加载。

## 7. 测试要求（vitest，不依赖后端）

| 测试对象 | 必测场景 |
| --- | --- |
| `api/sse.ts` | 一个数据块含多个事件；一个事件跨多个数据块；`: ping` 注释行被忽略；`\r\n` 换行；流结束时残留的不完整事件不产生事件 |
| 聊天 reducer | 完整事件序列（run_started → tool_call → tool_result → token… → citation → done）后的状态；`error` 后接 `done`；未收到 `done` 时的中断状态 |
| `lib/citations.ts` | 有效标记、无效标记、相邻标记、`[c12]` 两位数、无标记文本 |
| `lib/tree.ts` | 多层目录；父目录缺失时作为根节点处理 |
| `api/client.ts` | 带 `X-User-Id`；把后端错误体解析成 `ApiError(code, message, retryable)`；非 JSON 错误响应 |
| 组件 | 文件表的状态徽标与失败重试按钮；对话面板在模拟流下逐步显示文本和工具轨迹 |

门禁：`npm --prefix frontend run build`、`npm --prefix frontend test`、`npm --prefix frontend run lint` 全部通过。

## 8. 验收清单（集成阶段对真实后端执行）

- [ ] 切换用户后看不到其他用户的知识库
- [ ] 创建知识库 → 建三层目录 → 在子目录上传 TXT、PDF、DOCX，状态从排队变为成功
- [ ] 上传损坏文件显示失败原因，点击重试后状态正确
- [ ] 文件重命名、移动、删除；目录移动成环被拒绝；非空目录删除需二次确认
- [ ] 选择多个知识库提问，回答逐字出现，工具轨迹在回答过程中实时出现
- [ ] 点击引用看到来源文件、位置和原文
- [ ] 追问“这份文档还讲了什么”，能得到连贯回答
- [ ] 后端未配置模型或模型超时时显示明确错误，界面不会一直等待
- [ ] 中途点击停止，界面恢复可用

## 9. C2（Round 2，集成 1 之后）

- 降级展示：工具轨迹中标出重试、替代工具、部分回答等降级动作（字段以届时更新的契约为准）
- 空状态、加载骨架、删除确认文案、键盘可达性
- 引用聚合展示：同一文档的多个引用分组
- 修复集成 1 中发现的问题

## 10. 交付报告

按 AGENTS.md 第 5 节返回：已实现功能、修改文件、测试命令及结果、新增依赖、未解决问题、是否需要修改公共接口。另外附上实际安装的依赖列表（`package.json` 的 dependencies / devDependencies）。
