"""Context assembly under a character budget (docs/contracts.md §5.5). Owner: B.

Fixed part: system messages, the current question and this run's tool round trips. History
fills what is left, newest first. When this run's tool results exceed half of the budget,
older results are replaced by their one-line summary."""

import json
import re
from dataclasses import dataclass

from app.agent.evidence import compact_sources

MARKER_PATTERN = re.compile(r"\[(c\d+)\]")


def strip_markers(text: str) -> str:
    """Markers are only valid inside the run that issued them."""
    return MARKER_PATTERN.sub("", text)


@dataclass
class TurnItem:
    """One message of this run's tool round trips. `summary` is set on tool results."""

    message: dict
    summary: str | None = None
    ok: bool = True
    # Which model step produced it; results of the latest step are never compacted.
    step: int = 0
    compacted: bool = False


def _chars(message: dict) -> int:
    content = message.get("content") or ""
    size = len(content)
    for call in message.get("tool_calls") or []:
        size += len(call["function"]["name"]) + len(call["function"]["arguments"])
    return size


def compact_tool_results(turns: list[TurnItem], limit: int) -> None:
    """Replace the oldest tool results by their summary until the total fits `limit`.
    Results from the most recent step are kept intact: the model has not used them yet."""
    tool_items = [item for item in turns if item.message.get("role") == "tool"]
    total = sum(_chars(item.message) for item in tool_items)
    if total <= limit or not tool_items:
        return
    latest_step = max(item.step for item in tool_items)
    for item in tool_items:
        if total <= limit:
            break
        if item.compacted or item.step == latest_step:
            continue
        before = _chars(item.message)
        sources = compact_sources(json.loads(item.message["content"]), min(1200, limit // 4))
        compacted = {
            "ok": item.ok,
            "summary": item.summary,
            "note": "较早的工具结果已压缩；sources仅保留来源及短摘录，需要完整原文请重新读取",
        }
        if sources:
            compacted["sources"] = sources
        item.message = {
            **item.message,
            "content": json.dumps(
                compacted,
                ensure_ascii=False,
            ),
        }
        item.compacted = True
        total -= before - _chars(item.message)


def build_messages(
    *,
    system: list[str],
    history: list[dict],
    question: str,
    turns: list[TurnItem],
    budget: int,
) -> list[dict]:
    compact_tool_results(turns, budget // 2)
    fixed = sum(len(text) for text in system) + len(question)
    fixed += sum(_chars(item.message) for item in turns)
    remaining = budget - fixed
    kept: list[dict] = []
    for message in reversed(history):
        content = strip_markers(message["content"])
        if len(content) > remaining:
            break
        remaining -= len(content)
        kept.append({"role": message["role"], "content": content})
    kept.reverse()
    # Start the history at a user turn so it reads as complete question/answer pairs.
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return [
        *({"role": "system", "content": text} for text in system),
        *kept,
        {"role": "user", "content": question},
        *(item.message for item in turns),
    ]
