"""Agent loop (docs/contracts.md §5.4). Owner: B.

One AgentRun answers one question: it calls the model, executes requested tools, feeds the
results (including structured errors) back, and finally validates citation markers and
persists the outcome. Events are handed to `emit` as schema models; the SSE layer frames
them and sends `done` from the returned DoneEvent.

Degradation (contracts §10): a retryable tool failure is retried once with the same arguments
as a new, linked tool call (at most MAX_TOOL_RETRIES per run); the tool's fallback is applied
to the final attempt only; a model failure before any text of the step is retried once. Any
of these marks the run as degraded."""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel

from app.agent.context import MARKER_PATTERN, TurnItem, build_messages
from app.agent.evidence import source_inventory
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
    ToolResult,
    ToolResultEvent,
    Usage,
)
from app.services import conversations
from app.services.conversations import CitationRecord, RunResult, StartedRun
from app.tools.context import MarkerAllocator, ToolContext
from app.tools.registry import (
    apply_fallback,
    execute_attempt_async,
    fallback_kind,
    openai_tool_specs,
    parse_arguments,
    serialize_result,
    summarize,
)

logger = logging.getLogger(__name__)

Emit = Callable[[BaseModel], Awaitable[None]]

MODEL_STOP_REASONS = {"model_timeout": "timeout"}
# Automatic tool retries allowed per run (contracts §10.1).
MAX_TOOL_RETRIES = 3
# Seconds to wait before retrying a document that is still being parsed. Read at call time,
# so tests can shorten it by patching this module attribute.
NOT_READY_RETRY_DELAY = 1.0
RETRYABLE_MODEL_ERRORS = {"model_error", "model_timeout"}
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
        self.source_documents: dict[str, dict] = {}
        self.tool_retries = 0
        self.model_retries = 0
        self.fallbacks = 0
        self.ctx = ToolContext(
            db=setup.db,
            settings=setup.settings,
            user_id=setup.user_id,
            allowed_kb_ids=list(setup.knowledge_base_ids),
            run_id=self.run_id,
            markers=MarkerAllocator(),
            allowed_document_ids=setup.started.scope.document_ids if setup.started.scope else None,
        )
        self.tool_specs = openai_tool_specs()

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    @property
    def usage(self) -> Usage:
        return Usage(prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens)

    @property
    def degraded(self) -> bool:
        return bool(self.tool_retries or self.model_retries or self.fallbacks)

    def done_event(self, stop_reason: str) -> DoneEvent:
        return DoneEvent(
            run_id=self.run_id,
            stop_reason=stop_reason,
            step_count=self.step_count,
            usage=self.usage,
            degraded=self.degraded,
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
            degraded=self.degraded,
            model_retries=self.model_retries,
        )
        await asyncio.to_thread(conversations.finish_run, self.setup.db, self.run_id, result)
        for citation in citations:
            await self.emit(CitationEvent(**citation.to_out().model_dump()))
        if error is not None:
            await self.emit(error)
        logger.info(
            "run finished",
            extra={
                "stop_reason": stop_reason,
                "step_count": self.step_count,
                "dropped": dropped,
                "degraded": self.degraded,
                "tool_retries": self.tool_retries,
                "model_retries": self.model_retries,
                "fallbacks": self.fallbacks,
            },
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
        if setup_scope := self.setup.started.scope:
            parts.append(
                "本轮资料范围："
                + setup_scope.model_dump_json(exclude={"document_ids", "knowledge_base_ids"})
                + "。自动模式按需检索；指定模式只能使用这些资料，历史回答不能作为范围外文档的新证据。"
            )
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
            inventory = source_inventory(
                [item.message for item in self.turns], previous=self.source_documents
            )
            current_system = ["\n\n".join([*system, inventory])] if inventory else system
            messages = build_messages(
                system=current_system,
                history=history,
                question=self.setup.question,
                turns=self.turns,
                budget=settings.context_budget_chars,
            )
            turn_text, requested = await self._call_model_with_retry(messages)
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

    async def _call_model_with_retry(
        self, messages: list[dict]
    ) -> tuple[str, ToolCallsRequested | None]:
        """A model failure before any text of this step is retried once (contracts §10.3):
        no event, no extra step. After text was streamed a retry would duplicate it."""
        emitted = len(self.text_parts)
        try:
            return await self._call_model(messages)
        except AppError as exc:
            if exc.code not in RETRYABLE_MODEL_ERRORS or len(self.text_parts) > emitted:
                raise
            self.model_retries += 1
            logger.warning("retrying model call", extra={"error_code": exc.code})
        return await self._call_model(messages)

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

    def _should_retry(self, result: ToolResult) -> bool:
        return (
            not result.ok
            and result.error is not None
            and result.error.retryable
            and self.tool_retries < MAX_TOOL_RETRIES
        )

    async def _run_tool(self, step: int, call: ToolCallRequest, arguments: dict) -> None:
        timeout = self.setup.settings.tool_timeout
        call_id = await self._start_call(step, call, arguments, attempt=1, retry_of=None)
        started = time.perf_counter()
        result = await execute_attempt_async(self.ctx, call.name, call.arguments_json, timeout)
        attempt = 1
        if self._should_retry(result):
            # The failed attempt stays in the trace but is not shown to the model: the model
            # gets one tool message per call, carrying the outcome of the retry.
            await self._finish_call(call_id, call.name, result, started, attempt=attempt)
            self.tool_retries += 1
            if result.error.code == "document_not_ready":
                # Parsing is in progress; give it a moment instead of failing again at once.
                await asyncio.sleep(NOT_READY_RETRY_DELAY)
            logger.info("retrying tool", extra={"tool": call.name, "error_code": result.error.code})
            attempt = 2
            call_id = await self._start_call(
                step, call, arguments, attempt=attempt, retry_of=call_id
            )
            started = time.perf_counter()
            result = await execute_attempt_async(self.ctx, call.name, call.arguments_json, timeout)
        # Only the final attempt degrades, so a transient failure is first retried for real.
        result = apply_fallback(self.ctx, call.name, call.arguments_json, result)
        summary, payload = await self._finish_call(
            call_id, call.name, result, started, attempt=attempt
        )
        self.turns.append(
            TurnItem(
                message={"role": "tool", "tool_call_id": call.id, "content": payload},
                summary=summary,
                ok=result.ok,
                step=step,
            )
        )

    async def _start_call(
        self,
        step: int,
        call: ToolCallRequest,
        arguments: dict,
        *,
        attempt: int,
        retry_of: str | None,
    ) -> str:
        call_id = await asyncio.to_thread(
            conversations.insert_tool_call,
            self.setup.db,
            self.run_id,
            step,
            call.name,
            arguments,
            attempt=attempt,
            retry_of=retry_of,
        )
        await self.emit(
            ToolCallEvent(
                call_id=call_id,
                step=step,
                name=call.name,
                arguments=arguments,
                attempt=attempt,
                retry_of=retry_of,
            )
        )
        return call_id

    async def _finish_call(
        self, call_id: str, name: str, result: ToolResult, started: float, *, attempt: int
    ) -> tuple[str, str]:
        """Persist and emit one attempt's outcome; returns (summary, payload for the model)."""
        duration_ms = round((time.perf_counter() - started) * 1000)
        summary = summarize(name, result)
        payload = serialize_result(result)
        fallback = fallback_kind(result)
        if fallback is not None:
            self.fallbacks += 1
        await asyncio.to_thread(
            conversations.finish_tool_call,
            self.setup.db,
            call_id,
            ok=result.ok,
            summary=summary,
            result_json=payload,
            error=result.error,
            duration_ms=duration_ms,
            fallback=fallback,
        )
        logger.info(
            "tool finished",
            extra={
                "tool": name,
                "ok": result.ok,
                "error_code": result.error.code if result.error else None,
                "duration_ms": duration_ms,
                "attempt": attempt,
                "fallback": fallback,
            },
        )
        await self.emit(
            ToolResultEvent(
                call_id=call_id,
                status="success" if result.ok else "error",
                summary=summary,
                error=result.error,
                duration_ms=duration_ms,
                fallback=fallback,
            )
        )
        return summary, payload

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
                degraded=self.degraded,
                model_retries=self.model_retries,
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
                    degraded=self.degraded,
                    model_retries=self.model_retries,
                ),
            )
        except Exception:
            logger.exception("cannot mark run as failed")
