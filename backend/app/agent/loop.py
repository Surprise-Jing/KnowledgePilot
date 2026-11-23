"""Agent loop (docs/contracts.md §5.4). Owner: B.

One AgentRun answers one question: it calls the model, executes requested tools, feeds the
results (including structured errors) back, and finally validates citation markers and
persists the outcome. Events are handed to `emit` as schema models; the SSE layer frames
them and sends `done` from the returned DoneEvent."""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel

from app.agent.context import MARKER_PATTERN, TurnItem, build_messages
from app.agent.prompts import SYSTEM_PROMPT, focus_message, scope_message
from app.config import Settings
from app.db import Database
from app.errors import AppError
from app.llm.base import LLMClient, TextDelta, ToolCallRequest, ToolCallsRequested, UsageReport
from app.logging_setup import run_id_var
from app.schemas import (
    CitationEvent,
    DoneEvent,
    ErrorEvent,
    RunStartedEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    Usage,
)
from app.services import conversations
from app.services.conversations import CitationRecord, RunResult, StartedRun
from app.tools.context import MarkerAllocator, ToolContext
from app.tools.registry import (
    execute_async,
    openai_tool_specs,
    parse_arguments,
    serialize_result,
    summarize,
)

logger = logging.getLogger(__name__)

Emit = Callable[[BaseModel], Awaitable[None]]

MODEL_STOP_REASONS = {"model_timeout": "timeout"}
INTERNAL_ERROR = ErrorEvent(
    code="internal_error", message="服务内部错误，回答未完成，请稍后重试", retryable=True
)


@dataclass
class RunSetup:
    db: Database
    settings: Settings
    llm: LLMClient
    user_id: str
    question: str
    knowledge_base_ids: list[str]
    started: StartedRun


def _recorded_arguments(call: ToolCallRequest) -> tuple[dict, str]:
    """Arguments for the trace and the JSON string echoed back to the model. Unparsable
    arguments are recorded under `_raw` and echoed as valid JSON, because some providers
    reject conversation history with malformed tool-call arguments; the registry reports the
    parse error to the model in the tool result."""
    try:
        return parse_arguments(call.arguments_json), call.arguments_json or "{}"
    except AppError:
        arguments = {"_raw": call.arguments_json[:1000]}
        return arguments, json.dumps(arguments, ensure_ascii=False)


class AgentRun:
    def __init__(self, setup: RunSetup, emit: Emit):
        self.setup = setup
        self.emit = emit
        self.run_id = setup.started.run_id
        self.text_parts: list[str] = []
        self.step_count = 0
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.turns: list[TurnItem] = []
        self.ctx = ToolContext(
            db=setup.db,
            settings=setup.settings,
            user_id=setup.user_id,
            allowed_kb_ids=list(setup.knowledge_base_ids),
            run_id=self.run_id,
            markers=MarkerAllocator(),
        )
        self.tool_specs = openai_tool_specs()

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    @property
    def usage(self) -> Usage:
        return Usage(prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens)

    def done_event(self, stop_reason: str) -> DoneEvent:
        return DoneEvent(
            run_id=self.run_id,
            stop_reason=stop_reason,
            step_count=self.step_count,
            usage=self.usage,
        )

    # ---- entry point ----

    async def run(self) -> DoneEvent:
        token = run_id_var.set(self.run_id)
        try:
            return await self._run()
        except asyncio.CancelledError:
            # Client disconnected: persist before propagating, even though we are cancelled.
            self.persist_interrupted()
            raise
        finally:
            run_id_var.reset(token)

    async def _run(self) -> DoneEvent:
        started = self.setup.started
        await self.emit(
            RunStartedEvent(
                run_id=started.run_id,
                conversation_id=started.conversation_id,
                user_message_id=started.user_message_id,
                assistant_message_id=started.assistant_message_id,
            )
        )
        logger.info("run started", extra={"steps_max": self.setup.settings.agent_max_steps})
        error: ErrorEvent | None
        try:
            stop_reason, error = await self._loop()
        except AppError as exc:
            # Model failures (model_error / model_timeout) end the run with a visible error.
            stop_reason = MODEL_STOP_REASONS.get(exc.code, "model_error")
            error = ErrorEvent(code=exc.code, message=exc.message, retryable=exc.retryable)
            logger.warning("run stopped by %s", exc.code)
        except Exception:
            logger.exception("agent run failed")
            # The contract has no dedicated stop reason for internal failures.
            stop_reason, error = "interrupted", INTERNAL_ERROR
        citations, dropped = self._resolve_citations()
        completed = stop_reason in ("answered", "max_steps")
        result = RunResult(
            run_status="completed" if completed else "failed",
            stop_reason=stop_reason,
            message_status="completed" if completed else "failed",
            content=self.text,
            step_count=self.step_count,
            usage=self.usage,
            error=error.message if error else None,
            dropped_citations=dropped,
            citations=citations,
        )
        await asyncio.to_thread(conversations.finish_run, self.setup.db, self.run_id, result)
        for citation in citations:
            await self.emit(CitationEvent(**citation.to_out().model_dump()))
        if error is not None:
            await self.emit(error)
        logger.info(
            "run finished",
            extra={"stop_reason": stop_reason, "step_count": self.step_count, "dropped": dropped},
        )
        return self.done_event(stop_reason)

    # ---- loop ----

    def _system_messages(self) -> list[str]:
        setup = self.setup
        started = setup.started
        names = conversations.knowledge_base_names(setup.db, setup.user_id, self.ctx.allowed_kb_ids)
        focus = conversations.session_focus(
            setup.db, setup.user_id, started.conversation_id, started.run_id
        )
        parts = [SYSTEM_PROMPT, scope_message(names)]
        focus_text = focus_message(focus)
        if focus_text:
            parts.append(focus_text)
        # One system message: some OpenAI-compatible providers reject several.
        return ["\n\n".join(parts)]

    def _load_context(self) -> tuple[list[str], list[dict]]:
        started = self.setup.started
        history = conversations.load_history(
            self.setup.db, started.conversation_id, started.user_message_id
        )
        return self._system_messages(), history

    async def _loop(self) -> tuple[str, ErrorEvent | None]:
        settings = self.setup.settings
        system, history = await asyncio.to_thread(self._load_context)
        step = 1
        while True:
            if step > settings.agent_max_steps:
                return "max_steps", ErrorEvent(
                    code="max_steps_reached",
                    message=f"已达到最大执行步数（{settings.agent_max_steps}），回答可能不完整",
                    retryable=False,
                )
            self.step_count = step
            messages = build_messages(
                system=system,
                history=history,
                question=self.setup.question,
                turns=self.turns,
                budget=settings.context_budget_chars,
            )
            turn_text, requested = await self._call_model(messages)
            if requested is None:
                return "answered", None
            parsed = [_recorded_arguments(call) for call in requested.calls]
            self.turns.append(
                TurnItem(
                    message={
                        "role": "assistant",
                        "content": turn_text or None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {"name": call.name, "arguments": echoed},
                            }
                            for call, (_, echoed) in zip(requested.calls, parsed, strict=True)
                        ],
                    },
                    step=step,
                )
            )
            for call, (arguments, _) in zip(requested.calls, parsed, strict=True):
                await self._run_tool(step, call, arguments)
            step += 1

    async def _call_model(self, messages: list[dict]) -> tuple[str, ToolCallsRequested | None]:
        turn_text: list[str] = []
        requested: ToolCallsRequested | None = None
        events = self.setup.llm.stream(messages, self.tool_specs)
        try:
            async for event in events:
                if isinstance(event, TextDelta):
                    if event.text:
                        turn_text.append(event.text)
                        self.text_parts.append(event.text)
                        await self.emit(TokenEvent(delta=event.text))
                elif isinstance(event, ToolCallsRequested):
                    requested = event if event.calls else None
                elif isinstance(event, UsageReport):
                    self._add_usage(event)
        finally:
            # Close the model stream (and its HTTP connection) now, also when cancelled.
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                await aclose()
        return "".join(turn_text), requested

    def _add_usage(self, event: UsageReport) -> None:
        if event.prompt_tokens is not None:
            self.prompt_tokens = (self.prompt_tokens or 0) + event.prompt_tokens
        if event.completion_tokens is not None:
            self.completion_tokens = (self.completion_tokens or 0) + event.completion_tokens

    async def _run_tool(self, step: int, call: ToolCallRequest, arguments: dict) -> None:
        setup = self.setup
        call_id = await asyncio.to_thread(
            conversations.insert_tool_call, setup.db, self.run_id, step, call.name, arguments
        )
        await self.emit(
            ToolCallEvent(call_id=call_id, step=step, name=call.name, arguments=arguments)
        )
        started = time.perf_counter()
        result = await execute_async(
            self.ctx, call.name, call.arguments_json, setup.settings.tool_timeout
        )
        duration_ms = round((time.perf_counter() - started) * 1000)
        summary = summarize(call.name, result)
        payload = serialize_result(result)
        await asyncio.to_thread(
            conversations.finish_tool_call,
            setup.db,
            call_id,
            ok=result.ok,
            summary=summary,
            result_json=payload,
            error=result.error,
            duration_ms=duration_ms,
        )
        logger.info(
            "tool finished",
            extra={
                "tool": call.name,
                "ok": result.ok,
                "error_code": result.error.code if result.error else None,
                "duration_ms": duration_ms,
            },
        )
        await self.emit(
            ToolResultEvent(
                call_id=call_id,
                status="success" if result.ok else "error",
                summary=summary,
                error=result.error,
                duration_ms=duration_ms,
            )
        )
        self.turns.append(
            TurnItem(
                message={"role": "tool", "tool_call_id": call.id, "content": payload},
                summary=summary,
                ok=result.ok,
                step=step,
            )
        )

    # ---- citations and persistence ----

    def _resolve_citations(self) -> tuple[list[CitationRecord], list[str]]:
        """Accept only markers that a tool returned in this run, in order of first use."""
        citations: list[CitationRecord] = []
        dropped: list[str] = []
        for marker in dict.fromkeys(MARKER_PATTERN.findall(self.text)):
            source = self.ctx.markers.source(marker)
            if source is None:
                dropped.append(marker)
                continue
            citations.append(
                CitationRecord(
                    marker=marker,
                    chunk_id=source.chunk_id,
                    document_id=source.document_id,
                    filename=source.filename,
                    location=source.location,
                    content=source.content,
                )
            )
        return citations, dropped

    def persist_interrupted(self) -> None:
        """Synchronous on purpose: it runs while the task or stream is being cancelled, when
        awaiting is not possible. Idempotent: a finalized run is left untouched."""
        try:
            conversations.interrupt_run(
                self.setup.db,
                self.run_id,
                content=self.text,
                step_count=self.step_count,
                usage=self.usage,
            )
        except Exception:
            logger.exception("cannot mark run as interrupted")

    def persist_failed(self) -> None:
        try:
            conversations.finish_run(
                self.setup.db,
                self.run_id,
                RunResult(
                    run_status="failed",
                    stop_reason="interrupted",
                    message_status="failed",
                    content=self.text,
                    step_count=self.step_count,
                    usage=self.usage,
                    error=INTERNAL_ERROR.message,
                ),
            )
        except Exception:
            logger.exception("cannot mark run as failed")
