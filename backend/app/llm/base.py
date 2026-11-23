"""LLM client contract shared by the Agent (B) and test doubles (D). Owner: Tech Lead.
See docs/contracts.md §5.3.

`messages` and `tools` use the OpenAI Chat Completions format. One call to `stream` is one
model turn: it yields text deltas, at most one ToolCallsRequested (at the end of the turn),
and optionally a UsageReport. Errors are raised as AppError (model_error / model_timeout)."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.config import Settings


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    # Raw JSON string exactly as the model produced it; the tool registry parses and validates.
    arguments_json: str


@dataclass(frozen=True)
class ToolCallsRequested:
    calls: tuple[ToolCallRequest, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class UsageReport:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


LLMEvent = TextDelta | ToolCallsRequested | UsageReport


class LLMClient(Protocol):
    model: str

    def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]: ...


# app.state.llm_factory: None means "use the default OpenAI-compatible factory" (implemented
# by B in app/llm). Tests assign a factory returning a scripted client.
LLMFactory = Callable[[Settings], LLMClient]
