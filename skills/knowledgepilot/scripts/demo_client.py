"""Reproducible KnowledgePilot MCP demo, no AI client needed.

Starts the stdio MCP server with the official `mcp` SDK client and runs the recommended call
order: list tools → list_knowledge_bases → search_chunks → read_document. Every request and
response is printed as JSON on stdout; the server's own logs go to stderr.

Run from the repository root (the backend environment provides the `mcp` package):

    uv run --directory backend python ../skills/knowledgepilot/scripts/demo_client.py \
        --query "检索关键词" --user demo
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DEFAULT_BACKEND = Path(__file__).resolve().parents[3] / "backend"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KnowledgePilot MCP 调用示例")
    parser.add_argument("--query", required=True, help="search_chunks 的检索语句")
    parser.add_argument("--user", default=None, help="已注册的用户名（PK_MCP_USERNAME），默认 demo")
    parser.add_argument("--top-k", type=int, default=3, help="返回片段数，1–10，默认 3")
    parser.add_argument(
        "--document-id", default=None, help="要阅读的文档 ID；默认取第一个检索结果所在文档"
    )
    parser.add_argument("--backend-dir", type=Path, default=DEFAULT_BACKEND, help="backend 目录")
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="数据目录（PK_DATA_DIR），默认 backend/data"
    )
    parser.add_argument(
        "--server-cwd",
        type=Path,
        default=None,
        help="服务端工作目录，默认 backend 目录（会读取其中的 .env）",
    )
    return parser.parse_args(argv)


def server_parameters(args: argparse.Namespace) -> StdioServerParameters:
    backend = args.backend_dir.resolve()
    # The SDK only passes a few safe variables (PATH, HOME…) to the server; forward the
    # KnowledgePilot settings explicitly so PK_* in the caller's environment still apply.
    env = {key: value for key, value in os.environ.items() if key.startswith("PK_")}
    env["PYTHONPATH"] = str(backend)
    if args.user:
        env["PK_MCP_USERNAME"] = args.user
    if args.data_dir:
        env["PK_DATA_DIR"] = str(args.data_dir.resolve())
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        cwd=str((args.server_cwd or backend).resolve()),
        env=env,
    )


def show(title: str, payload: Any) -> None:
    print(f"### {title}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.flush()


async def call(session: ClientSession, name: str, arguments: dict) -> tuple[bool, Any]:
    show(f"call_tool {name} 请求", {"name": name, "arguments": arguments})
    result = await session.call_tool(name, arguments)
    payload = json.loads(result.content[0].text)
    show(f"call_tool {name} 响应", {"isError": result.isError, "result": payload})
    return not result.isError, payload


async def run(args: argparse.Namespace) -> int:
    async with (
        stdio_client(server_parameters(args)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        show(
            "list_tools",
            [{"name": tool.name, "description": tool.description} for tool in tools.tools],
        )

        ok, data = await call(session, "list_knowledge_bases", {})
        if not ok:
            return 1
        ok, data = await call(session, "search_chunks", {"query": args.query, "top_k": args.top_k})
        if not ok:
            return 1
        document_id = args.document_id or (data["hits"][0]["document_id"] if data["hits"] else None)
        if document_id is None:
            show("read_document", {"skipped": "没有检索结果，跳过 read_document"})
            return 0
        ok, _ = await call(
            session, "read_document", {"document_id": document_id, "max_chars": 1000}
        )
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
