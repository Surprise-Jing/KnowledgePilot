"""Tool registry shared by the Agent, HTTP and the MCP server (docs/contracts.md §5.1). Owner: B.

`execute` never raises: every failure becomes a structured ToolResult so the model can read
it and decide what to do next.

Degradation (docs/contracts.md §10.2): `execute` / `execute_async` treat the call as final and
apply the tool's fallback when it fails, which is what MCP needs (it has no automatic retry).
The Agent retries retryable failures itself, so it runs single attempts with
`execute_attempt_async` and calls `apply_fallback` only on the final attempt."""

import asyncio
import copy
import json
import logging
from typing import Any, Protocol

from app.errors import AppError
from app.schemas import FallbackKind, ToolError, ToolResult
from app.tools.builtin import (
    ListFilesTool,
    ListKnowledgeBasesTool,
    ReadDocumentTool,
    SearchChunksTool,
)
from app.tools.context import ToolContext
from app.tools.schema import validate_object

logger = logging.getLogger(__name__)

# Upper bound for one tool result as sent to the model and stored in tool_calls.result_json.
MAX_RESULT_CHARS = 8000


class Tool(Protocol):
    name: str
    description: str
    parameters: dict

    def run(self, ctx: ToolContext, arguments: dict) -> dict: ...

    def summarize(self, data: dict) -> str: ...

    # Optional: `fallback(ctx, arguments, error) -> dict | None` returns degraded data for a
    # call that failed for good (see ReadDocumentTool.fallback).


_TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (ListKnowledgeBasesTool(), SearchChunksTool(), ListFilesTool(), ReadDocumentTool())
}


def list_tools() -> list[Tool]:
    return list(_TOOLS.values())


def openai_tool_specs() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": copy.deepcopy(tool.parameters),
            },
        }
        for tool in _TOOLS.values()
    ]


def _error(code: str, message: str, retryable: bool = False) -> ToolResult:
    return ToolResult(ok=False, error=ToolError(code=code, message=message, retryable=retryable))


def parse_arguments(raw_arguments: str | dict | None) -> dict:
    """Parse model-produced arguments. Raises AppError(invalid_argument)."""
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments)
    if raw_arguments is None or not raw_arguments.strip():
        # Some models send an empty string for tools without parameters.
        return {}
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise AppError(422, "invalid_argument", "工具参数不是合法的 JSON") from exc
    if not isinstance(arguments, dict):
        raise AppError(422, "invalid_argument", "工具参数必须是 JSON 对象")
    return arguments


def execute_attempt(ctx: ToolContext, name: str, raw_arguments: str | dict) -> ToolResult:
    """Run one attempt of a tool call, without the after-failure fallback."""
    tool = _TOOLS.get(name)
    if tool is None:
        return _error("invalid_argument", f"未知工具：{name}")
    try:
        arguments = validate_object(parse_arguments(raw_arguments), tool.parameters)
        return ToolResult(ok=True, data=tool.run(ctx, arguments))
    except AppError as exc:
        return _error(exc.code, exc.message, exc.retryable)
    except Exception:
        logger.exception("tool %s failed", name)
        return _error("internal_error", "工具执行出现内部错误，请稍后重试", retryable=True)


def execute(ctx: ToolContext, name: str, raw_arguments: str | dict) -> ToolResult:
    """Run a tool call as a final attempt: failures get the tool's fallback when it has one."""
    return apply_fallback(ctx, name, raw_arguments, execute_attempt(ctx, name, raw_arguments))


async def execute_attempt_async(
    ctx: ToolContext, name: str, raw_arguments: str | dict, timeout: float
) -> ToolResult:
    """Run `execute_attempt` in a worker thread with a timeout. On timeout the result is
    `tool_timeout`; the worker thread cannot be killed and finishes in the background (its
    database work uses its own short transactions, so it does not block the run)."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(execute_attempt, ctx, name, raw_arguments), timeout=timeout
        )
    except TimeoutError:
        logger.warning("tool %s timed out after %ss", name, timeout)
        return _error("tool_timeout", f"工具执行超过 {timeout:g} 秒未完成", retryable=True)


async def execute_async(
    ctx: ToolContext, name: str, raw_arguments: str | dict, timeout: float
) -> ToolResult:
    """`execute` with a timeout: one attempt in a worker thread, then the fallback (which only
    reads in-memory state, so it also works after a timeout)."""
    result = await execute_attempt_async(ctx, name, raw_arguments, timeout)
    return apply_fallback(ctx, name, raw_arguments, result)


def is_degradable(error: ToolError) -> bool:
    """Permission, scope and argument errors are never degraded (contracts §10.2): the answer
    to "you may not / this does not exist / wrong arguments" must not be replaced."""
    code = error.code
    return not (code.endswith("_not_found") or code in {"out_of_scope", "invalid_argument"})


def apply_fallback(
    ctx: ToolContext, name: str, raw_arguments: str | dict, result: ToolResult
) -> ToolResult:
    """Replace the final failure of a call with the tool's degraded result, if it has one."""
    if result.ok or result.error is None or not is_degradable(result.error):
        return result
    tool = _TOOLS.get(name)
    fallback = getattr(tool, "fallback", None)
    if fallback is None:
        return result
    try:
        arguments = validate_object(parse_arguments(raw_arguments), tool.parameters)
        data = fallback(ctx, arguments, result.error)
    except Exception:
        # Invalid arguments never get here (invalid_argument is not degradable). Anything else
        # is a bug in the fallback: log it and report the original failure.
        logger.exception("fallback of tool %s failed", name)
        return result
    if data is None:
        return result
    logger.warning("tool %s degraded", name, extra={"reason": result.error.code})
    return ToolResult(ok=True, data=data)


def fallback_kind(result: ToolResult) -> FallbackKind | None:
    """The degradation recorded in a successful result's `data.degraded`, if any."""
    if not result.ok or not isinstance(result.data, dict):
        return None
    degraded = result.data.get("degraded")
    return degraded.get("kind") if isinstance(degraded, dict) else None


def summarize(name: str, result: ToolResult) -> str:
    """One-line Chinese summary for the trace and for compacted context."""
    if not result.ok:
        message = result.error.message if result.error else "未知错误"
        return f"失败：{message}"
    tool = _TOOLS.get(name)
    if tool is None or result.data is None:
        return "完成"
    try:
        return tool.summarize(result.data)
    except (KeyError, TypeError):
        logger.exception("cannot summarize result of tool %s", name)
        return "完成"


def _longest_string(node: Any, parent: Any = None, key: Any = None):
    best = (parent, key, node) if isinstance(node, str) else (None, None, "")
    children = (
        node.items()
        if isinstance(node, dict)
        else enumerate(node)
        if isinstance(node, list)
        else ()
    )
    for child_key, child in children:
        candidate = _longest_string(child, node, child_key)
        if len(candidate[2]) > len(best[2]):
            best = candidate
    return best


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def serialize_result(result: ToolResult, limit: int = MAX_RESULT_CHARS) -> str:
    """Serialize for the model, keeping the JSON valid within `limit` characters by shortening
    the longest strings (chunk contents) rather than cutting the document mid-structure."""
    payload = result.model_dump(exclude_none=True)
    text = _dumps(payload)
    if len(text) <= limit:
        return text
    payload["note"] = "结果过长，部分文本已截断"
    for _ in range(500):
        text = _dumps(payload)
        excess = len(text) - limit
        if excess <= 0:
            return text
        parent, key, value = _longest_string(payload)
        if parent is None or len(value) <= 1:
            break
        parent[key] = value[: max(len(value) - excess - 1, 0)] + "…"
    logger.warning("tool result could not be shortened below %s chars", limit)
    return _dumps({"ok": result.ok, "note": "结果过长，已省略"})
