"""ScriptedLLM: a deterministic LLMClient for tests (docs/briefs/D-testing.md §1.1).

Owner: B1; reused by D. It lives under tests/ and is injected through
`app.state.llm_factory`; product code never selects it.

Each "turn" is a list of LLM events (or `Pause` items) or an exception to raise. Every call
to `stream()` consumes one turn. When the script runs out, `ScriptExhausted` is raised. It
derives from BaseException on purpose, so the Agent's `except Exception` safety net cannot
turn a test-script mistake into an ordinary `internal_error` event: the request fails loudly.
"""

import asyncio
import copy
import json
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from itertools import count

from app.config import Settings
from app.llm.base import (
    LLMClient,
    LLMEvent,
    TextDelta,
    ToolCallRequest,
    ToolCallsRequested,
    UsageReport,
)


class ScriptExhausted(BaseException):
    """The Agent called the model more often than the test scripted."""


@dataclass(frozen=True)
class Pause:
    """Sleep inside a turn, e.g. to let heartbeats fire or a client disconnect."""

    seconds: float


TurnItem = LLMEvent | Pause
Turn = list[TurnItem] | BaseException

_ids = count(1)


class ScriptedLLM:
    def __init__(self, turns: Iterable[Turn], model: str = "scripted-model"):
        self.model = model
        self.turns: list[Turn] = list(turns)
        # One entry per stream() call: {"messages": [...], "tools": [...]} (deep copies).
        self.calls: list[dict] = []

    @property
    def remaining(self) -> int:
        return len(self.turns)

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]:
        self.calls.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        if not self.turns:
            raise ScriptExhausted(
                f"ScriptedLLM: 脚本只有 {len(self.calls) - 1} 轮，但模型被调用了第 {len(self.calls)} 次"
            )
        turn = self.turns.pop(0)
        if isinstance(turn, BaseException):
            raise turn
        for item in turn:
            if isinstance(item, Pause):
                await asyncio.sleep(item.seconds)
                continue
            # Yield control between events like a real network stream would.
            await asyncio.sleep(0)
            yield item

    # Convenience accessors for assertions.
    def messages(self, call: int) -> list[dict]:
        return self.calls[call]["messages"]

    def contents(self, call: int, role: str | None = None) -> list[str]:
        return [
            message.get("content") or ""
            for message in self.messages(call)
            if role is None or message.get("role") == role
        ]


def text(*pieces: str) -> list[TextDelta]:
    """`text("你好", "世界")` yields two deltas; `text("abc")` one."""
    return [TextDelta(piece) for piece in pieces]


def call(name: str, args: dict | str, id: str | None = None) -> ToolCallRequest:
    """One tool call request. `args` may be a dict or a raw (possibly invalid) JSON string."""
    arguments = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return ToolCallRequest(id=id or f"call_{next(_ids)}", name=name, arguments_json=arguments)


def tool_call(name: str, args: dict | str, id: str | None = None) -> ToolCallsRequested:
    """A turn-ending request for a single tool call."""
    return ToolCallsRequested(calls=(call(name, args, id),))


def tool_calls(*requests: ToolCallRequest) -> ToolCallsRequested:
    """A turn-ending request for several tool calls, built with `call(...)`."""
    return ToolCallsRequested(calls=tuple(requests))


def usage(prompt_tokens: int | None, completion_tokens: int | None) -> UsageReport:
    return UsageReport(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


def scripted_factory(llm: ScriptedLLM) -> Callable[[Settings], LLMClient]:
    """Assign the result to `app.state.llm_factory`."""

    def factory(settings: Settings) -> LLMClient:
        return llm

    return factory
