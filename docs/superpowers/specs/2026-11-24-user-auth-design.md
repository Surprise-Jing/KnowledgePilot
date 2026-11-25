# 用户注册与登录（契约 v1.11）设计

日期：2026-11-24

## 背景

数据隔离已经在服务层按 `user_id` 完成（`services/access.py`、工具层、Agent、MCP），但身份来自请求头 `X-User-Id`：任何人改请求头就能冒充他人，首次出现的 ID 还会自动建用户。本次把“身份从哪来”换成真正的认证，下游的 `user_id: str` 接口保持不变。

## 决策

| 项 | 决策 |
| --- | --- |
| `X-User-Id` | 删除，不保留开发模式；`PK_DEFAULT_USER_ID` 删除 |
| 会话载体 | HttpOnly Cookie `kp_session`，`SameSite=Lax`，`Path=/`；`Secure` 由 `PK_COOKIE_SECURE` 控制（本地 http 默认关） |
| 会话存储 | `sessions` 表只存令牌的 SHA-256；令牌 `secrets.token_urlsafe(32)`；有效期 `PK_SESSION_TTL_DAYS`（默认 30 天，固定到期，不滑动） |
| 密码哈希 | 标准库 `hashlib.scrypt`（N=2^14, r=8, p=1, 16 字节盐, 64 字节输出），`hmac.compare_digest` 比对；不新增依赖 |
| 用户名 | `[A-Za-z0-9_-]{3,32}`，按原样保存，大小写不敏感唯一 |
| 密码 | 8–128 位，至少包含一个字母和一个数字 |
| 用户 ID | 新用户 UUID；已有用户保持原 ID（如 `demo`），用户名 = 原 ID |
| 注册开关 | `PK_ALLOW_REGISTRATION`（默认开）；关闭后注册返回 403 |
| 登录限流 | 进程内计数：同一用户名 15 分钟内失败 5 次后返回 429，成功登录清零（不引入 Redis） |
| 防枚举 | 用户名不存在与密码错误同一错误；不存在时也执行一次哈希，耗时一致 |
| 老用户 | 迁移后没有密码、无法登录；用 `python -m app.users set-password` 设置 |
| MCP / 重建索引 | 走本地进程，不经 HTTP：改为按用户名指定（`PK_MCP_USERNAME`、`--username`），用户必须已存在，不再自动创建 |

## 数据模型（schema v7）

- `users` 新增 `username TEXT`（大小写不敏感唯一索引）、`password_hash TEXT`（可空：老用户未设密码）。迁移把 `username` 设为原 `id`；若老 ID 仅大小写不同，只有最小的那个得到用户名，其余留空（可用 CLI 补设）。
- 新表 `sessions(id, user_id → users ON DELETE CASCADE, token_hash UNIQUE, created_at, expires_at)`。登录时顺带清理该用户的过期会话。

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/register` | `{username, password}` → 201 `UserOut`，并设置会话 Cookie（注册即登录） |
| POST | `/api/auth/login` | `{username, password}` → 200 `UserOut`，设置 Cookie |
| POST | `/api/auth/logout` | 删除当前会话并清 Cookie；幂等 204 |
| GET | `/api/auth/me` | 200 `UserOut`；未登录 401 |

`UserOut = {id, username, created_at}`。除 `/health`、`/api/auth/register|login|logout` 和前端静态文件外，所有 `/api/*` 需要有效会话，否则 401 `unauthenticated`。

新错误码：`unauthenticated` 401、`invalid_credentials` 401、`username_taken` 409、`invalid_username` 400、`weak_password` 400、`registration_closed` 403、`too_many_attempts` 429（retryable）。删除 `invalid_user_id`。

## 后端结构

- `services/auth.py`：密码哈希/校验、注册、登录（含限流）、会话创建/解析/删除、按用户名查用户、设置密码、删除用户（先收集文档文件路径，提交后删除文件，与删除知识库一致）。
- `api/auth.py`：四个路由，负责 Cookie 读写。
- `deps.current_user`：读 Cookie → `auth.resolve_session` → `user_id`。
- `app/users.py`：管理命令 `list` / `set-password <username>`（交互输入或 `--password-stdin`）/ `delete <username>`。需在服务停止时运行。
- `services/access.ensure_user` 删除。

## 前端

- `api/client.ts`：去掉 `X-User-Id`，`credentials: 'same-origin'`；任何响应 `unauthenticated` 时触发“会话失效”事件。
- `api/auth.ts`、`features/auth/AuthProvider`（启动时 `/me`）、`features/auth/AuthPage`（登录/注册两个标签，shadcn Card/Tabs/Field/Input/Button）。
- `App.tsx`：加载中 → 骨架；未登录 → AuthPage；已登录 → 工作区（仍按用户 ID 作 key，切换账号时清空状态）。
- `UserSwitcher` 改为 `UserMenu`：显示用户名与“退出登录”；删除 `lib/user.ts`。

## 测试

- `tests/fakes/helpers.py`：`login(client, user)` 通过服务层创建用户（无密码）和真实会话，并设为客户端默认 Cookie；`headers(client, user)` 返回指定用户的 `Cookie` 头。所有请求都走真实的会话校验，不再有绕过认证的路径。
- 新增 `test_auth.py`：注册/重名（含大小写）/非法用户名/弱密码/注册关闭、登录成功与失败、限流、注销后 401、会话过期、未登录 401、伪造 `X-User-Id` 无效、库里不存明文密码、Cookie 属性、两个注册用户相互隔离、v6→v7 迁移保留老数据、CLI 设置密码与删除用户。
- 前端：client 不再发送 `X-User-Id`、401 触发会话失效；登录表单测试。

## 本地数据处理

保留 `demo`（设置一个随机密码告知用户），删除 `alice`、`bob`（alice 无数据，bob 有 1 个会话）。

## 账号管理（2026-11-25 追加，契约 v1.12）

账号菜单新增：修改用户名、修改密码、注销账号（“退出其他设备”按需求去掉）。

- 修改用户名 / 修改密码 / 注销账号都再次校验当前密码；错误为 403 `wrong_password`（不用 401，避免前端误判为会话失效），失败次数与登录共用限流，防止拿到会话的人借此暴力猜密码。
- 修改用户名不改 `users.id`，数据归属不受影响。
- 修改密码后保留当前会话、撤销其他会话；新密码不得与旧密码相同。
- 注销账号与 CLI 删除用户共用同一删除逻辑（级联删除 + 提交后删文件），前端要求输入用户名并填写密码确认。
