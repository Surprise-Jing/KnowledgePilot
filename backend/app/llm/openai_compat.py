"""OpenAI-compatible Chat Completions client with tool calling (docs/contracts.md §5.3).
Owner: B.

Streaming mode assembles `delta.tool_calls` fragments by index; non-streaming mode makes a
single request and yields the same events. Never log the API key or response bodies: they
can echo document text sent as tool results."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

import httpx

from app.config import Settings
from app.errors import AppError
from app.llm.base import LLMEvent, TextDelta, ToolCallRequest, ToolCallsRequested, UsageReport

logger = logging.getLogger(__name__)


def _model_error(message: str = "模型服务返回错误或无效响应，请稍后重试") -> AppError:
    return AppError(502, "model_error", message, retryable=True)


def _model_timeout() -> AppError:
    return AppError(504, "model_timeout", "模型响应超时，请稍后重试", retryable=True)


def _usage(payload: Any) -> UsageReport | None:
    if not isinstance(payload, dict):
        return None
    prompt, completion = payload.get("prompt_tokens"), payload.get("completion_tokens")
    if not isinstance(prompt, int) and not isinstance(completion, int):
        return None
    return UsageReport(
        prompt_tokens=prompt if isinstance(prompt, int) else None,
        completion_tokens=completion if isinstance(completion, int) else None,
    )


def _arguments_text(arguments: Any) -> str:
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return arguments
    # Some providers return parsed objects instead of the JSON string.
    return json.dumps(arguments, ensure_ascii=False)


class _ToolCallAssembler:
    """Accumulates streamed tool-call fragments. Fragments of one call share an `index`;
    `id` and `name` usually arrive with the first fragment, `arguments` in pieces."""

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, Any]] = {}

    def add(self, fragment: dict) -> None:
        index = fragment.get("index", len(self._calls))
        if not isinstance(index, int):
            raise _model_error()
        call = self._calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
        if fragment.get("id"):
            call["id"] = fragment["id"]
        function = fragment.get("function") or {}
        if not isinstance(function, dict):
            raise _model_error()
        if function.get("name"):
            call["name"] += function["name"]
        call["arguments"] += _arguments_text(function.get("arguments"))

    def result(self) -> ToolCallsRequested | None:
        if not self._calls:
            return None
        calls = []
        for index in sorted(self._calls):
            call = self._calls[index]
            if not call["name"]:
                raise _model_error("模型返回的工具调用缺少名称")
            calls.append(
                ToolCallRequest(
                    id=call["id"] or f"call_{index}",
                    name=call["name"],
                    arguments_json=call["arguments"],
                )
            )
        return ToolCallsRequested(calls=tuple(calls))


class OpenAICompatClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.model = settings.llm_model
        self._settings = settings
        # Injected by tests (httpx.MockTransport); None uses the real network transport.
        self._transport = transport

    def _request(self, messages: list[dict], tools: list[dict], stream: bool) -> dict:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": stream}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._settings.llm_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {self._settings.llm_api_key.get_secret_value()}"},
            timeout=httpx.Timeout(self._settings.llm_timeout),
            transport=self._transport,
        )

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]:
        try:
            if self._settings.llm_stream:
                async with aclosing(self._stream(messages, tools)) as events:
                    async for event in events:
                        yield event
            else:
                for event in await self._complete(messages, tools):
                    yield event
        except httpx.TimeoutException as exc:
            logger.warning("model request timed out", extra={"model": self.model})
            raise _model_timeout() from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "model request failed: %s", type(exc).__name__, extra={"model": self.model}
            )
            raise _model_error() from exc

    @staticmethod
    def _check_status(response: httpx.Response) -> None:
        if response.status_code >= 400:
            # Status only: the body may echo the prompt, which contains document text.
            logger.warning("model service returned HTTP %s", response.status_code)
            raise _model_error(f"模型服务返回错误（HTTP {response.status_code}），请稍后重试")

    async def _stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]:
        assembler = _ToolCallAssembler()
        usage: UsageReport | None = None
        request = self._request(messages, tools, True)
        async with (
            self._client() as client,
            client.stream("POST", "/chat/completions", json=request) as response,
        ):
            self._check_status(response)
            async for line in response.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    # Blank separators, SSE comments (": keep-alive") and other fields.
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                chunk = self._parse_chunk(data)
                usage = _usage(chunk.get("usage")) or usage
                for event_text in self._apply_chunk(chunk, assembler):
                    yield TextDelta(event_text)
        tool_calls = assembler.result()
        if tool_calls is not None:
            yield tool_calls
        if usage is not None:
            yield usage

    @staticmethod
    def _parse_chunk(data: str) -> dict:
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as exc:
            logger.warning("model stream sent a non-JSON data line")
            raise _model_error() from exc
        if not isinstance(chunk, dict):
            raise _model_error()
        if chunk.get("error"):
            logger.warning("model stream reported an error")
            raise _model_error()
        return chunk

    @staticmethod
    def _apply_chunk(chunk: dict, assembler: _ToolCallAssembler) -> list[str]:
        texts = []
        choices = chunk.get("choices") or []
        if not isinstance(choices, list):
            raise _model_error()
        for choice in choices[:1]:
            delta = choice.get("delta") if isinstance(choice, dict) else None
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                texts.append(content)
            fragments = delta.get("tool_calls") or []
            if not isinstance(fragments, list):
                raise _model_error()
            for fragment in fragments:
                if not isinstance(fragment, dict):
                    raise _model_error()
                assembler.add(fragment)
        return texts

    async def _complete(self, messages: list[dict], tools: list[dict]) -> list[LLMEvent]:
        async with self._client() as client:
            response = await client.post(
                "/chat/completions", json=self._request(messages, tools, False)
            )
        self._check_status(response)
        try:
            body = response.json()
            message = body["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError("message is not an object")
            events: list[LLMEvent] = []
            content = message.get("content")
            if isinstance(content, str) and content:
                events.append(TextDelta(content))
            assembler = _ToolCallAssembler()
            for index, call in enumerate(message.get("tool_calls") or []):
                assembler.add({**call, "index": index})
            tool_calls = assembler.result()
            if tool_calls is not None:
                events.append(tool_calls)
            usage = _usage(body.get("usage"))
            if usage is not None:
                events.append(usage)
            return events
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("model returned an invalid response: %s", type(exc).__name__)
            raise _model_error() from exc
