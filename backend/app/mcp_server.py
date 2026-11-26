"""stdio MCP server exposing the shared tool registry (docs/contracts.md §6). Owner: E.

Start: uv run --directory backend python -m app.mcp_server

Tool names, descriptions and input schemas are taken verbatim from `app.tools.registry`, and
every call goes through `execute_async`, so argument validation, permission checks, error codes
and tool-level degradation are exactly those of the in-app Agent.

stdout carries MCP protocol messages only. Logging goes to stderr, and as a safety net file
descriptor 1 is pointed at stderr once the protocol has taken a private copy of it, so a stray
print() from any library cannot corrupt the protocol stream."""

import json
import logging
import os
import sqlite3
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import TextIOWrapper
from typing import Any

import anyio
import anyio.to_thread
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import ValidationError

from app.config import Settings
from app.db import Database
from app.errors import AppError
from app.logging_setup import configure_logging
from app.schemas import ToolError, ToolResult
from app.services.auth import require_user_by_username
from app.services.knowledge_bases import list_knowledge_bases
from app.tools.context import MarkerAllocator, ToolContext
from app.tools.registry import execute_async, openai_tool_specs

# Explicit name: run as `python -m`, __name__ would be "__main__".
logger = logging.getLogger("app.mcp_server")

SERVER_NAME = "knowledgepilot"
INSTRUCTIONS = (
    "KnowledgePilot 个人知识库检索工具。建议顺序：list_knowledge_bases 了解可用知识库 → "
    "search_chunks 检索相关片段 → 需要上下文时 read_document 连续阅读。"
    "回答时用结果中的引用标记（如 [c1]）注明出处，不要编造资料中没有的内容。"
)


@dataclass(frozen=True)
class McpRuntime:
    """Process-wide state: one database and one MCP user for the lifetime of the server."""

    db: Database
    settings: Settings
    user_id: str


def mcp_tools() -> list[types.Tool]:
    """The registry's tools in MCP form. Schemas are copied, never re-declared, so the MCP
    surface cannot drift from what the in-app Agent sees."""
    return [
        types.Tool(
            name=spec["function"]["name"],
            description=spec["function"]["description"],
            inputSchema=spec["function"]["parameters"],
        )
        for spec in openai_tool_specs()
    ]


def _allowed_kb_ids(runtime: McpRuntime) -> list[str]:
    # Read at call time so knowledge bases created or deleted in the web app are seen at once.
    with runtime.db.connect() as connection:
        return [kb["id"] for kb in list_knowledge_bases(connection, runtime.user_id)]


def _error_result(code: str, message: str, retryable: bool) -> ToolResult:
    return ToolResult(ok=False, error=ToolError(code=code, message=message, retryable=retryable))


def to_call_tool_result(result: ToolResult) -> types.CallToolResult:
    if result.ok:
        data = result.data or {}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
            structuredContent=data,
            isError=False,
        )
    error = result.error or ToolError(code="internal_error", message="未知错误", retryable=True)
    return types.CallToolResult(
        content=[
            types.TextContent(type="text", text=json.dumps(error.model_dump(), ensure_ascii=False))
        ],
        isError=True,
    )


async def run_tool(
    runtime: McpRuntime, markers: MarkerAllocator, name: str, arguments: dict[str, Any]
) -> ToolResult:
    """Run one tool call with a fresh ToolContext (docs/contracts.md §6)."""
    try:
        allowed = await anyio.to_thread.run_sync(_allowed_kb_ids, runtime)
    except AppError as exc:
        return _error_result(exc.code, exc.message, exc.retryable)
    except Exception:
        logger.exception("cannot resolve knowledge bases for mcp user")
        return _error_result("internal_error", "工具执行出现内部错误，请稍后重试", True)
    ctx = ToolContext(
        db=runtime.db,
        settings=runtime.settings,
        user_id=runtime.user_id,
        allowed_kb_ids=allowed,
        run_id=None,
        markers=markers,
    )
    return await execute_async(ctx, name, arguments, runtime.settings.tool_timeout)


def build_server(runtime: McpRuntime) -> Server:
    @asynccontextmanager
    async def session_lifespan(_server: Server) -> AsyncIterator[dict[str, Any]]:
        # Server.run enters the lifespan once per MCP session, so citation markers stay
        # stable across all calls of one session and are not shared between sessions.
        yield {"markers": MarkerAllocator()}

    server: Server = Server(SERVER_NAME, instructions=INSTRUCTIONS, lifespan=session_lifespan)

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return mcp_tools()

    # validate_input=False: the registry validates arguments itself and reports failures as
    # `invalid_argument` with a Chinese message, the same as for the in-app Agent.
    @server.call_tool(validate_input=False)
    async def handle_call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        markers: MarkerAllocator = server.request_context.lifespan_context["markers"]
        started = time.perf_counter()
        result = await run_tool(runtime, markers, name, arguments)
        logger.info(
            "mcp tool call",
            extra={
                "tool": name,
                "ok": result.ok,
                "error_code": result.error.code if result.error else None,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )
        return to_call_tool_result(result)

    return server


def _protocol_stdout() -> anyio.AsyncFile[str]:
    """Keep a private copy of stdout for the protocol and redirect fd 1 to stderr."""
    sys.stdout.flush()
    protocol_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    return anyio.wrap_file(TextIOWrapper(os.fdopen(protocol_fd, "wb"), encoding="utf-8"))


def _startup(settings: Settings) -> McpRuntime:
    db = Database(settings.data_dir / "knowledge.db")
    # migrate(), not initialize(): recovery would fail parsing/answers in a running web app.
    db.migrate()
    # The MCP server has no login: it acts as a configured, already registered user and never
    # creates accounts.
    with db.connect() as connection:
        user_id = require_user_by_username(connection, settings.mcp_username)["id"]
    return McpRuntime(db=db, settings=settings, user_id=user_id)


async def _serve(runtime: McpRuntime, stdout: anyio.AsyncFile[str]) -> None:
    server = build_server(runtime)
    async with stdio_server(stdout=stdout) as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> int:
    stdout = _protocol_stdout()
    try:
        settings = Settings()
        configure_logging(settings.log_level)
        runtime = _startup(settings)
    except AppError as exc:
        print(f"KnowledgePilot MCP 启动失败：{exc.message}", file=sys.stderr)
        return 1
    except (ValidationError, RuntimeError, sqlite3.Error, OSError) as exc:
        print(f"KnowledgePilot MCP 启动失败：{exc}", file=sys.stderr)
        return 1
    logger.info(
        "mcp server started",
        extra={"user_id": runtime.user_id, "retriever": runtime.settings.retriever},
    )
    try:
        anyio.run(_serve, runtime, stdout)
    except KeyboardInterrupt:
        # Ctrl-C when started by hand: a normal way to stop, not an error worth a traceback.
        logger.info("mcp server stopped by keyboard interrupt")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
