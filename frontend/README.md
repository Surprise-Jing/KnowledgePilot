# KnowledgePilot 前端

Vite + React + TypeScript（strict）+ Tailwind CSS v4 + shadcn/ui（radix-nova）。设计见 [docs/design.md](../docs/design.md)，接口契约见 [docs/contracts.md](../docs/contracts.md)。

## 命令

```bash
npm --prefix frontend install
npm --prefix frontend run dev     # http://localhost:5173，/api 与 /health 代理到 127.0.0.1:8000
npm --prefix frontend run build   # tsc -b && vite build，输出 frontend/dist
npm --prefix frontend test        # vitest run（jsdom，不依赖后端）
npm --prefix frontend run lint    # oxlint
```

## 结构

```text
src/
  api/          types.ts（与 schemas.py 对应）、client.ts（会话 Cookie、ApiError）、sse.ts（SSE 解析）、各资源请求
  lib/          citations.ts、remarkCitations.ts（引用解析与聚合）、pdf.ts、pdfGeometry.ts（PDF 渲染与坐标换算）、
                tree.ts、errors.ts、format.ts、download.ts、session.ts
  hooks/        use-resource.ts（加载/重载/丢弃过期响应）、use-media-query.ts
  components/   跨功能组件（错误展示、命名/确认对话框、健康状态、账号菜单）；ui/ 为 shadcn 组件
  features/
    auth/       登录注册、账号管理
    knowledge/  知识库列表、目录树、移动对话框
    files/      文件表、上传、轮询
    chat/       会话、流式回答 reducer、引用标记渲染
    citations/  右栏（引用 / 工具轨迹）状态、引用面板、原文查看与 PDF 高亮
    trace/      工具轨迹面板
  test/         测试初始化与测试数据（仅测试使用）
```

## 约定

- 所有请求经 `api/client.ts`，以 `credentials: 'same-origin'` 携带会话 Cookie；返回 `unauthenticated` 时回到登录页；非 2xx 统一转为 `ApiError(code, message, retryable)`。
- 错误按 `lib/errors.ts` 分类：用户可修正 → 就地提示或 toast；系统错误 → 顶部错误条（`retryable` 时可重试）。
- 对话流用 `fetch` 读取 SSE，事件交给纯函数 `features/chat/chatReducer.ts`；结束或中断后重新拉取会话详情。
- 产品代码不含任何假数据或 mock；测试替身只在 `*.test.ts(x)` 与 `src/test/` 中。
