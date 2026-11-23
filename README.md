# KnowledgePilot

Agentic Personal Knowledge Workspace（个人知识管理智能助手）。

当前提供首日后端原型，跑通 **上传 → 解析 → 检索 → 回答**。完整 P0/P1 需求见 [功能需求](docs/README.md)，启动、接口和实现边界见 [后端开发说明](docs/backend.md)。

安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 后，在项目根目录执行：

```bash
uv run --directory backend uvicorn app.main:app --host 127.0.0.1 --port 8000
```

首次启动自动安装 Python 和依赖，需要网络。接口文档：<http://127.0.0.1:8000/docs>。

默认无需模型密钥，回答为带来源的原文摘录。真实模型模式需要按后端说明配置环境变量。
