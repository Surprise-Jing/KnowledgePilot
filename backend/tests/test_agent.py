"""LLM client, default factory and context management (docs/contracts.md §5.3–§5.5). Owner: B.
Network access is replaced by httpx.MockTransport; no API key or model is needed."""

import asyncio
import json
import logging

import httpx
import pytest

from app.agent.context import TurnItem, build_messages, compact_tool_results, strip_markers
from app.agent.prompts import SYSTEM_PROMPT, focus_message
from app.errors import AppError
from app.llm.base import TextDelta, ToolCallRequest, ToolCallsRequested, UsageReport
from app.llm.factory import default_llm_factory
from app.llm.openai_compat import OpenAICompatClient
from app.services.conversations import SessionFocus
from tests.fakes.helpers import make_settings
from tests.fakes.llm import ScriptedLLM, ScriptExhausted, text, tool_call

SECRET = "sk-test-secret-value"
TOOLS = [{"type": "function", "function": {"name": "search_chunks", "parameters": {}}}]
MESSAGES = [{"role": "user", "content": "你好"}]


def collect(client, messages=MESSAGES, tools=TOOLS):
    async def run():
        return [event async for event in client.stream(messages, tools)]

    return asyncio.run(run())


def sse_body(*chunks, done=True) -> bytes:
    lines = [f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return ": keep-alive\n\n".join(lines).encode()


def make_client(tmp_path, handler, **overrides):
    settings = make_settings(
        tmp_path,
        llm_api_key=SECRET,
        llm_base_url="https://llm.example/v1/",
        **overrides,
    )
    return OpenAICompatClient(settings, transport=httpx.MockTransport(handler))


def delta(**fields):
    return {"choices": [{"index": 0, "delta": fields}]}


# ---- streaming ----


def test_stream_assembles_fragmented_tool_calls_text_and_usage(tmp_path):
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        body = sse_body(
            delta(role="assistant", content="我来"),
            delta(content="查一下"),
            delta(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "search_chunks", "arguments": ""},
                    }
                ]
            ),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '{"que'}}]),
            delta(
                tool_calls=[
                    {
                        "index": 1,
                        "id": "call_b",
                        "function": {"name": "list_files", "arguments": ""},
                    }
                ]
            ),
            delta(tool_calls=[{"index": 0, "function": {"arguments": 'ry": "删除'}}]),
            delta(
                tool_calls=[{"index": 1, "function": {"arguments": '{"knowledge_base_id":"k"}'}}]
            ),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '文档"}'}}]),
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 7}},
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    events = collect(make_client(tmp_path, handler))
    assert events == [
        TextDelta("我来"),
        TextDelta("查一下"),
        ToolCallsRequested(
            calls=(
                ToolCallRequest("call_a", "search_chunks", '{"query": "删除文档"}'),
                ToolCallRequest("call_b", "list_files", '{"knowledge_base_id":"k"}'),
            )
        ),
        UsageReport(prompt_tokens=12, completion_tokens=7),
    ]
    request = requests[0]
    assert str(request.url) == "https://llm.example/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    payload = json.loads(request.content)
    assert payload["model"] == "test"
    assert payload["stream"] is True
    assert payload["tools"] == TOOLS and payload["tool_choice"] == "auto"
    assert payload["messages"] == MESSAGES


def test_stream_text_only_and_stops_at_done(tmp_path):
    def handler(request):
        body = sse_body(delta(content="你好"), delta(content="！")) + b"data: not-json\n\n"
        return httpx.Response(200, content=body)

    assert collect(make_client(tmp_path, handler)) == [TextDelta("你好"), TextDelta("！")]


def test_stream_without_done_marker_or_usage(tmp_path):
    def handler(request):
        return httpx.Response(200, content=sse_body(delta(content="好"), done=False))

    assert collect(make_client(tmp_path, handler)) == [TextDelta("好")]


def test_tools_are_omitted_when_empty(tmp_path):
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, content=sse_body(delta(content="好")))

    collect(make_client(tmp_path, handler), tools=[])
    assert "tools" not in payloads[0] and "tool_choice" not in payloads[0]


@pytest.mark.parametrize(
    "body",
    [
        b"data: {not json}\n\n",
        b'data: {"error": {"message": "overloaded"}}\n\n',
        (
            b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {}}]}}]}\n\n'
            b"data: [DONE]\n\n"
        ),
        b'data: {"choices": "oops"}\n\n',
    ],
)
def test_stream_format_errors_are_model_errors(tmp_path, body):
    client = make_client(tmp_path, lambda request: httpx.Response(200, content=body))
    with pytest.raises(AppError) as raised:
        collect(client)
    assert raised.value.code == "model_error" and raised.value.retryable is True
    assert raised.value.status == 502


# ---- non-streaming ----


def test_non_stream_mode_yields_equivalent_events(tmp_path):
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "先检索",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "search_chunks",
                                        "arguments": '{"query":"x"}',
                                    },
                                },
                                {
                                    "id": "call_2",
                                    "type": "function",
                                    # Some providers return parsed objects.
                                    "function": {"name": "list_files", "arguments": {"a": 1}},
                                },
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            },
        )

    events = collect(make_client(tmp_path, handler, llm_stream=False))
    assert events == [
        TextDelta("先检索"),
        ToolCallsRequested(
            calls=(
                ToolCallRequest("call_1", "search_chunks", '{"query":"x"}'),
                ToolCallRequest("call_2", "list_files", '{"a": 1}'),
            )
        ),
        UsageReport(3, 4),
    ]
    assert payloads[0]["stream"] is False and "stream_options" not in payloads[0]


def test_non_stream_plain_answer(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "答案"}}]})

    assert collect(make_client(tmp_path, handler, llm_stream=False)) == [TextDelta("答案")]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, content=b"<html>"),
    ],
)
def test_non_stream_invalid_responses_are_model_errors(tmp_path, response):
    client = make_client(tmp_path, lambda request: response, llm_stream=False)
    with pytest.raises(AppError) as raised:
        collect(client)
    assert raised.value.code == "model_error"


# ---- error mapping ----


@pytest.mark.parametrize("stream", [True, False])
def test_timeouts_map_to_model_timeout(tmp_path, stream):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(AppError) as raised:
        collect(make_client(tmp_path, handler, llm_stream=stream))
    assert (raised.value.status, raised.value.code, raised.value.retryable) == (
        504,
        "model_timeout",
        True,
    )


@pytest.mark.parametrize("stream", [True, False])
def test_http_errors_map_to_model_error_without_leaking(tmp_path, stream, caplog):
    def handler(request):
        return httpx.Response(401, json={"error": f"bad key {SECRET} 文档原文"})

    with caplog.at_level(logging.DEBUG), pytest.raises(AppError) as raised:
        collect(make_client(tmp_path, handler, llm_stream=stream))
    assert raised.value.code == "model_error" and raised.value.status == 502
    assert "401" in raised.value.message
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert SECRET not in logged and "文档原文" not in logged
    assert SECRET not in raised.value.message


def test_connection_errors_map_to_model_error(tmp_path):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AppError) as raised:
        collect(make_client(tmp_path, handler))
    assert raised.value.code == "model_error"


# ---- factory ----


@pytest.mark.parametrize("overrides", [{"llm_model": ""}, {"llm_api_key": ""}])
def test_default_factory_requires_model_and_key(tmp_path, overrides):
    with pytest.raises(AppError) as raised:
        default_llm_factory(make_settings(tmp_path, **overrides))
    assert (raised.value.status, raised.value.code) == (503, "model_not_configured")


def test_default_factory_builds_the_openai_client(tmp_path):
    client = default_llm_factory(make_settings(tmp_path))
    assert isinstance(client, OpenAICompatClient) and client.model == "test"


# ---- ScriptedLLM ----


def test_scripted_llm_records_calls_and_fails_loudly_when_exhausted():
    llm = ScriptedLLM([[*text("a", "b"), tool_call("search_chunks", {"query": "q"}, id="x")]])

    async def run(messages):
        return [event async for event in llm.stream(messages, TOOLS)]

    events = asyncio.run(run(MESSAGES))
    assert events[:2] == [TextDelta("a"), TextDelta("b")]
    assert events[2].calls[0] == ToolCallRequest("x", "search_chunks", '{"query": "q"}')
    assert llm.calls == [{"messages": MESSAGES, "tools": TOOLS}]
    with pytest.raises(ScriptExhausted):
        asyncio.run(run(MESSAGES))


# ---- context management ----


def test_strip_markers():
    assert strip_markers("结论[c1]，另见[c12][c3]。[x1]") == "结论，另见。[x1]"


def test_history_fills_remaining_budget_newest_first():
    history = [
        {"role": "user", "content": "旧问题" * 50},
        {"role": "assistant", "content": "旧回答" * 50},
        {"role": "user", "content": "新问题[c1]"},
        {"role": "assistant", "content": "新回答[c2]"},
    ]
    system = ["系统" * 10]
    messages = build_messages(
        system=system, history=history, question="当前问题", turns=[], budget=200
    )
    assert messages == [
        {"role": "system", "content": "系统" * 10},
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": "新回答"},
        {"role": "user", "content": "当前问题"},
    ]
    everything = build_messages(
        system=system, history=history, question="当前问题", turns=[], budget=10_000
    )
    assert len(everything) == 6


def test_history_never_starts_with_an_assistant_message():
    history = [
        {"role": "user", "content": "很长的问题" * 40},
        {"role": "assistant", "content": "回答"},
    ]
    messages = build_messages(system=["s"], history=history, question="q", turns=[], budget=100)
    assert [m["role"] for m in messages] == ["system", "user"]


def tool_item(step, size, summary):
    content = json.dumps({"ok": True, "data": {"text": "字" * size}}, ensure_ascii=False)
    return TurnItem(
        message={"role": "tool", "tool_call_id": f"t{step}", "content": content},
        summary=summary,
        step=step,
    )


def test_older_tool_results_are_compacted_to_summaries():
    turns = [tool_item(1, 600, "第一次"), tool_item(2, 600, "第二次"), tool_item(3, 600, "第三次")]
    compact_tool_results(turns, limit=1000)
    first, second, third = (json.loads(item.message["content"]) for item in turns)
    assert first["summary"] == "第一次" and "data" not in first
    assert second["summary"] == "第二次"
    assert third["data"]["text"] == "字" * 600
    # Within the limit nothing changes.
    small = [tool_item(1, 10, "a"), tool_item(2, 10, "b")]
    compact_tool_results(small, limit=1000)
    assert all("data" in json.loads(item.message["content"]) for item in small)


def test_compaction_stops_once_under_limit():
    turns = [tool_item(1, 400, "一"), tool_item(2, 400, "二"), tool_item(3, 400, "三")]
    compact_tool_results(turns, limit=1000)
    assert [item.compacted for item in turns] == [True, False, False]


def test_session_focus_message():
    assert focus_message(SessionFocus()) is None
    text_ = focus_message(
        SessionFocus(documents=[("d1", "方案.docx"), ("d2", "说明.txt")], last_scope=[("k", "库")])
    )
    assert "《方案.docx》 document_id=d1" in text_ and "「库」(id=k)" in text_
    assert text_.index("方案.docx") < text_.index("说明.txt")


def test_system_prompt_covers_contract_points():
    for phrase in ("只依据", "不可信", "[c1]", "不得编造", "没有找到依据", "寒暄"):
        assert phrase in SYSTEM_PROMPT
