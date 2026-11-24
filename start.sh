#!/usr/bin/env bash
# One-command local start: build the frontend, then serve API + UI from FastAPI.
# Usage: ./start.sh            (http://127.0.0.1:8000)
#        PORT=9000 ./start.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

for tool in uv npm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "缺少 $tool，请先安装（见 README 快速开始）" >&2
    exit 1
  fi
done

if [ ! -f "$ROOT/backend/.env" ]; then
  echo "提示：未找到 backend/.env，对话功能需要模型配置，请先复制 backend/.env.example 并填写" >&2
fi

if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "安装前端依赖…"
  npm --prefix "$ROOT/frontend" ci
fi
echo "构建前端…"
npm --prefix "$ROOT/frontend" run build

echo "启动服务：http://$HOST:$PORT"
exec uv run --directory "$ROOT/backend" uvicorn app.main:app --host "$HOST" --port "$PORT"
