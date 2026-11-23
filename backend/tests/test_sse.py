"""SSE protocol and Agent behaviour over HTTP with ScriptedLLM (docs/contracts.md §4, §5.4).
Owner: B."""

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from app.main import create_app
from tests.fakes.helpers import (
    ask,
    headers,
    install_llm,
    make_conversation,
    make_kb,
    make_settings,
    read_sse,
    upload,
)
from tests.fakes.llm import Pause, ScriptedLLM, call, text, tool_call, tool_calls, usage


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def kb(client):
    kb_id = make_kb(client, "产品库")
    upload(client, kb_id, "guide.txt", "删除文档时同步清理索引。")
    return kb_id


def names(events):
    return [name for name, _ in events]


def only(events, name):
    return [data for event, data in events if event == name]


def search_then_answer(answer_pieces=("删除文档时会清理索引", "[c1]", "。")):
    return ScriptedLLM(
        [
            [*text("我先检索。"), tool_call("search_chunks", {"query": "删除文档"}), usage(10, 2)],
            [*text(*answer_pieces), usage(20, 5)],
        ]
    )


def test_stream_shape_and_headers(client, kb):
    install_llm(client, search_then_answer())
    conversation = make_conversation(client)
    response = ask(client, conversation, "删除文档会怎样？", [kb])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    # Each data payload is a single line of JSON with non-ASCII kept as is.
    for line in response.text.splitlines():
        if line.startswith("data: "):
            json.loads(line[6:])
    assert "删除" in response.text
    events = read_sse(response)
    assert events[0][0] == "run_started"
    assert events[-1][0] == "done"
    assert names(events).count("done") == 1
    started = events[0][1]
    assert started["conversation_id"] == conversation
    done = events[-1][1]
    assert done["run_id"] == started["run_id"]
    assert done["stop_reason"] == "answered"
    assert done["step_count"] == 2
    assert done["usage"] == {"prompt_tokens": 30, "completion_tokens": 7}


def test_tool_events_precede_answer_and_tokens_match_persisted_answer(client, kb):
    llm = install_llm(client, search_then_answer())
    conversation = make_conversation(client)
    events = read_sse(ask(client, conversation, "删除文档会怎样？", [kb]))
    order = names(events)
    assert order == [
        "run_started",
        "token",
        "tool_call",
        "tool_result",
        "token",
        "token",
        "token",
        "citation",
        "done",
    ]
    tool_call_event, tool_result_event = (
        only(events, "tool_call")[0],
        only(events, "tool_result")[0],
    )
    assert tool_call_event["call_id"] == tool_result_event["call_id"]
    assert tool_call_event == {
        "call_id": tool_call_event["call_id"],
        "step": 1,
        "name": "search_chunks",
        "arguments": {"query": "删除文档"},
    }
    assert tool_result_event["status"] == "success"
    assert tool_result_event["summary"] == "命中 1 个片段（1 个文件）"
    assert tool_result_event["error"] is None
    assert tool_result_event["duration_ms"] >= 0
    answer = "".join(data["delta"] for data in only(events, "token"))
    detail = client.get(f"/api/conversations/{conversation}").json()
    assistant = detail["messages"][-1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == answer
    assert assistant["status"] == "completed"
    assert assistant["run_id"] == events[0][1]["run_id"]
    # The second model call received the tool result as a `tool` message.
    second = llm.messages(1)
    assistant_call = next(m for m in second if m["role"] == "assistant" and m.get("tool_calls"))
    tool_message = next(m for m in second if m["role"] == "tool")
    assert tool_message["tool_call_id"] == assistant_call["tool_calls"][0]["id"]
    payload = json.loads(tool_message["content"])
    assert payload["ok"] is True and payload["data"]["hits"][0]["marker"] == "c1"
    assert llm.calls[0]["tools"][0]["type"] == "function"


def test_citations_are_filtered_and_persisted(client, kb):
    install_llm(client, search_then_answer(("依据 [c1]，另见 [c9] 与 [c1]。",)))
    conversation = make_conversation(client)
    events = read_sse(ask(client, conversation, "删除文档会怎样？", [kb]))
    citations = only(events, "citation")
    assert [c["marker"] for c in citations] == ["c1"]
    citation = citations[0]
    assert citation["filename"] == "guide.txt"
    assert citation["location"]["kind"] == "txt_lines"
    assert citation["location_label"] == "第 1 行"
    assert citation["content"] == "删除文档时同步清理索引。"
    run_id = events[0][1]["run_id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["dropped_citations"] == ["c9"]
    assert run["status"] == "completed" and run["stop_reason"] == "answered"
    detail = client.get(f"/api/conversations/{conversation}").json()
    stored = detail["messages"][-1]
    # The answer text keeps the invalid marker; the UI renders it as plain text.
    assert "[c9]" in stored["content"]
    assert len(stored["citations"]) == 1
    assert stored["citations"][0]["location"] == {**citation["location"]}
    assert stored["citations"][0]["chunk_id"] == citation["chunk_id"]


def test_citations_follow_first_appearance_order(client, kb):
    upload(client, kb, "other.txt", "删除文档的其他说明。")
    llm = ScriptedLLM(
        [
            [tool_call("search_chunks", {"query": "删除文档"})],
            text("先说 [c2] 再说 [c1]，再提 [c2]。"),
        ]
    )
    install_llm(client, llm)
    events = read_sse(ask(client, make_conversation(client), "删除文档？", [kb]))
    assert [c["marker"] for c in only(events, "citation")] == ["c2", "c1"]


def test_tool_errors_are_reported_and_fed_back_to_the_model(client, kb):
    llm = install_llm(
        client,
        ScriptedLLM(
            [
                [tool_call("read_document", {"document_id": "does-not-exist"})],
                text("没有找到该文档。"),
            ]
        ),
    )
    events = read_sse(ask(client, make_conversation(client), "读一下文档", [kb]))
    result = only(events, "tool_result")[0]
    assert result["status"] == "error"
    assert result["error"]["code"] == "document_not_found"
    assert result["summary"].startswith("失败：")
    tool_messages = [m for m in llm.messages(1) if m["role"] == "tool"]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0]["content"])
    assert payload == {
        "ok": False,
        "error": {"code": "document_not_found", "message": "文档不存在", "retryable": False},
    }
    assert events[-1][1]["stop_reason"] == "answered"


def test_invalid_tool_arguments_and_unknown_tools_are_fed_back(client, kb):
    llm = install_llm(
        client,
        ScriptedLLM(
            [
                [tool_calls(call("search_chunks", "{bad json"), call("delete_everything", {}))],
                text("参数有误。"),
            ]
        ),
    )
    events = read_sse(ask(client, make_conversation(client), "问题", [kb]))
    results = only(events, "tool_result")
    assert [r["error"]["code"] for r in results] == ["invalid_argument", "invalid_argument"]
    assert only(events, "tool_call")[0]["arguments"] == {"_raw": "{bad json"}
    assert len([m for m in llm.messages(1) if m["role"] == "tool"]) == 2
    # The history sent back to the model only contains valid JSON arguments.
    echoed = next(m for m in llm.messages(1) if m.get("tool_calls"))["tool_calls"]
    assert json.loads(echoed[0]["function"]["arguments"]) == {"_raw": "{bad json"}
    assert json.loads(echoed[1]["function"]["arguments"]) == {}


def test_out_of_scope_tool_call(client, kb):
    other = make_kb(client, "未选择的库")
    install_llm(
        client,
        ScriptedLLM(
            [
                [tool_call("search_chunks", {"query": "x", "knowledge_base_ids": [other]})],
                text("无法访问。"),
            ]
        ),
    )
    events = read_sse(ask(client, make_conversation(client), "问题", [kb]))
    assert only(events, "tool_result")[0]["error"]["code"] == "out_of_scope"


def test_max_steps(tmp_path):
    settings = make_settings(tmp_path, agent_max_steps=2)
    with TestClient(create_app(settings)) as client:
        kb = make_kb(client)
        llm = install_llm(
            client,
            ScriptedLLM(
                [
                    [*text("先查 [c1]"), tool_call("search_chunks", {"query": "a"})],
                    [tool_call("search_chunks", {"query": "b"})],
                ]
            ),
        )
        conversation = make_conversation(client)
        events = read_sse(ask(client, conversation, "问题", [kb]))
        assert len(llm.calls) == 2
        assert names(events)[-2:] == ["error", "done"]
        error = only(events, "error")[0]
        assert error["code"] == "max_steps_reached" and error["retryable"] is False
        done = events[-1][1]
        assert done["stop_reason"] == "max_steps" and done["step_count"] == 2
        message = client.get(f"/api/conversations/{conversation}").json()["messages"][-1]
        # Text produced so far is kept and the message counts as completed.
        assert message["status"] == "completed" and message["content"] == "先查 [c1]"
        run = client.get(f"/api/runs/{done['run_id']}").json()
        assert run["stop_reason"] == "max_steps" and len(run["tool_calls"]) == 2
        assert run["dropped_citations"] == ["c1"]


@pytest.mark.parametrize(
    ("error", "stop_reason"),
    [
        (AppError(504, "model_timeout", "模型响应超时", retryable=True), "timeout"),
        (AppError(502, "model_error", "模型服务错误", retryable=True), "model_error"),
    ],
)
def test_model_failures_end_with_error_then_done(client, kb, error, stop_reason):
    install_llm(client, ScriptedLLM([[tool_call("search_chunks", {"query": "删除"})], error]))
    conversation = make_conversation(client)
    events = read_sse(ask(client, conversation, "问题", [kb]))
    assert names(events)[-2:] == ["error", "done"]
    assert only(events, "error")[0] == {
        "code": error.code,
        "message": error.message,
        "retryable": True,
    }
    assert events[-1][1]["stop_reason"] == stop_reason
    message = client.get(f"/api/conversations/{conversation}").json()["messages"][-1]
    assert message["status"] == "failed" and message["error"] == error.message
    run = client.get(f"/api/runs/{events[-1][1]['run_id']}").json()
    assert run["status"] == "failed" and run["stop_reason"] == stop_reason


def test_model_error_after_partial_answer_keeps_text(client, kb):
    class FailingMidway(ScriptedLLM):
        async def stream(self, messages, tools):
            self.calls.append({"messages": messages, "tools": tools})
            yield text("部分回答")[0]
            raise AppError(502, "model_error", "模型服务错误", retryable=True)

    install_llm(client, FailingMidway([]))
    conversation = make_conversation(client)
    events = read_sse(ask(client, conversation, "问题", [kb]))
    assert names(events) == ["run_started", "token", "error", "done"]
    message = client.get(f"/api/conversations/{conversation}").json()["messages"][-1]
    assert message["content"] == "部分回答" and message["status"] == "failed"


def test_unexpected_errors_become_internal_error_and_still_end_with_done(client, kb, caplog):
    install_llm(client, ScriptedLLM([RuntimeError("boom")]))
    with caplog.at_level(logging.ERROR):
        events = read_sse(ask(client, make_conversation(client), "问题", [kb]))
    assert names(events) == ["run_started", "error", "done"]
    assert only(events, "error")[0]["code"] == "internal_error"
    assert events[-1][1]["stop_reason"] == "interrupted"
    assert any("agent run failed" in record.message for record in caplog.records)


def test_tool_timeout_is_fed_back(tmp_path, monkeypatch):
    import time

    from app.tools import builtin

    def slow(*args, **kwargs):
        time.sleep(0.3)
        return []

    monkeypatch.setattr(builtin, "search", slow)
    with TestClient(create_app(make_settings(tmp_path, tool_timeout=0.05))) as client:
        kb = make_kb(client)
        llm = install_llm(
            client, ScriptedLLM([[tool_call("search_chunks", {"query": "x"})], text("超时了。")])
        )
        events = read_sse(ask(client, make_conversation(client), "问题", [kb]))
        result = only(events, "tool_result")[0]
        assert result["error"] == {
            "code": "tool_timeout",
            "message": result["error"]["message"],
            "retryable": True,
        }
        assert "tool_timeout" in next(m for m in llm.messages(1) if m["role"] == "tool")["content"]


def test_greeting_without_tools(client, kb):
    install_llm(client, ScriptedLLM([text("你好！有什么可以帮你？")]))
    events = read_sse(ask(client, make_conversation(client), "你好", [kb]))
    assert names(events) == ["run_started", "token", "done"]
    assert events[-1][1]["step_count"] == 1
    assert events[-1][1]["usage"] == {"prompt_tokens": None, "completion_tokens": None}


def test_multi_turn_history_and_session_focus(client, kb):
    llm = install_llm(
        client,
        ScriptedLLM(
            [
                [tool_call("search_chunks", {"query": "删除文档"})],
                text("删除时会清理索引 [c1]。"),
                text("这份文档还提到了同步。"),
            ]
        ),
    )
    conversation = make_conversation(client)
    read_sse(ask(client, conversation, "删除文档会怎样？", [kb]))
    read_sse(ask(client, conversation, "这份文档还提到了什么？", [kb]))
    messages = llm.messages(2)
    system = messages[0]
    assert system["role"] == "system"
    assert "guide.txt" in system["content"] and "会话焦点" in system["content"]
    assert "产品库" in system["content"]
    history = [(m["role"], m["content"]) for m in messages[1:]]
    assert history == [
        ("user", "删除文档会怎样？"),
        ("assistant", "删除时会清理索引 。"),
        ("user", "这份文档还提到了什么？"),
    ]
    # Tool round trips of the previous run are not replayed.
    assert all(m["role"] != "tool" for m in messages)


def test_failed_answers_are_not_in_history(client, kb):
    llm = install_llm(
        client,
        ScriptedLLM([AppError(502, "model_error", "错误", retryable=True), text("好的。")]),
    )
    conversation = make_conversation(client)
    read_sse(ask(client, conversation, "第一个问题", [kb]))
    read_sse(ask(client, conversation, "第二个问题", [kb]))
    roles = [(m["role"], m["content"]) for m in llm.messages(1)[1:]]
    assert roles == [("user", "第一个问题"), ("user", "第二个问题")]


def test_trace_is_persisted_and_private(client, kb):
    install_llm(
        client,
        ScriptedLLM(
            [
                [
                    tool_calls(
                        call("search_chunks", {"query": "删除"}), call("list_knowledge_bases", {})
                    )
                ],
                [tool_call("read_document", {"document_id": "missing"})],
                text("完成。"),
            ]
        ),
    )
    events = read_sse(ask(client, make_conversation(client), "问题", [kb]))
    run_id = events[0][1]["run_id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["knowledge_base_ids"] == [kb]
    assert run["model"] == "scripted-model"
    assert run["step_count"] == 3 and run["finished_at"]
    calls = run["tool_calls"]
    assert [(c["step"], c["tool_name"], c["status"]) for c in calls] == [
        (1, "search_chunks", "success"),
        (1, "list_knowledge_bases", "success"),
        (2, "read_document", "error"),
    ]
    assert calls[0]["arguments"] == {"query": "删除"}
    assert calls[0]["result_summary"] == "命中 1 个片段（1 个文件）"
    assert calls[0]["error"] is None and calls[0]["duration_ms"] >= 0
    assert calls[2]["error"]["code"] == "document_not_found"
    assert [c["id"] for c in calls] == [e["call_id"] for e in only(events, "tool_call")]
    with client.app.state.db.connect() as connection:
        stored = connection.execute(
            "SELECT result_json FROM tool_calls WHERE id=?", (calls[0]["id"],)
        ).fetchone()["result_json"]
    assert json.loads(stored)["data"]["hits"][0]["marker"] == "c1"
    response = client.get(f"/api/runs/{run_id}", headers=headers("bob"))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"
    assert client.get("/api/runs/missing").status_code == 404


def test_old_tool_results_are_compacted_when_over_half_budget(tmp_path):
    settings = make_settings(tmp_path, context_budget_chars=2000)
    with TestClient(create_app(settings)) as client:
        kb = make_kb(client)
        upload(
            client,
            kb,
            "big.txt",
            "\n".join(f"北极星资料第{i}行" + "很长" * 30 for i in range(40)),
        )
        llm = install_llm(
            client,
            ScriptedLLM(
                [
                    [tool_call("read_document_missing_tool", {})],
                    [tool_call("search_chunks", {"query": "北极星资料", "top_k": 3})],
                    [tool_call("search_chunks", {"query": "北极星", "top_k": 3})],
                    text("完成。"),
                ]
            ),
        )
        read_sse(ask(client, make_conversation(client), "问题", [kb]))
        last = [m for m in llm.messages(3) if m["role"] == "tool"]
        compacted = [json.loads(m["content"]) for m in last[:-1]]
        assert all("summary" in item and "data" not in item for item in compacted)
        assert compacted[1]["summary"].startswith("命中")
        assert "data" in json.loads(last[-1]["content"])


def test_heartbeat_is_sent_while_idle(client, kb):
    client.app.state.sse_heartbeat_seconds = 0.02
    install_llm(client, ScriptedLLM([[Pause(0.2), *text("好的。")]]))
    response = ask(client, make_conversation(client), "问题", [kb])
    assert ": ping\n\n" in response.text
    events = read_sse(response)
    assert names(events) == ["run_started", "token", "done"]


def test_client_disconnect_marks_run_interrupted(client, kb):
    """Drive the ASGI app directly: TestClient buffers the whole body, so it cannot
    disconnect mid-stream."""
    install_llm(
        client,
        ScriptedLLM([[*text("正在回答"), Pause(5), *text("不会发出")]]),
    )
    conversation = make_conversation(client)
    body = json.dumps({"content": "问题", "knowledge_base_ids": [kb]}).encode()
    chunks: list[bytes] = []

    async def scenario():
        got_token = asyncio.Event()
        request_sent = False

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await got_token.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if b"event: token" in b"".join(chunks):
                    got_token.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": f"/api/conversations/{conversation}/messages",
            "raw_path": f"/api/conversations/{conversation}/messages".encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json"), (b"x-user-id", b"demo")],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "state": {},
        }
        await asyncio.wait_for(client.app(scope, receive, send), timeout=3)

    asyncio.run(scenario())
    streamed = b"".join(chunks).decode()
    assert "event: run_started" in streamed and "event: done" not in streamed
    detail = client.get(f"/api/conversations/{conversation}").json()
    message = detail["messages"][-1]
    assert message["status"] == "failed" and message["content"] == "正在回答"
    run = client.get(f"/api/runs/{message['run_id']}").json()
    assert run["status"] == "failed" and run["stop_reason"] == "interrupted"
    assert run["finished_at"]
    # The conversation is free again.
    install_llm(client, ScriptedLLM([text("好的。")]))
    assert read_sse(ask(client, conversation, "再问一次", [kb]))[-1][1]["stop_reason"] == "answered"


def test_closing_the_stream_early_interrupts_the_run(client, kb):
    """ASGI servers with spec >= 2.4 report a disconnect as a failed send; the response then
    closes the body iterator, which must cancel and persist the run."""
    install_llm(client, ScriptedLLM([[*text("片段"), Pause(5)]]))
    conversation = make_conversation(client)
    body = json.dumps({"content": "问题", "knowledge_base_ids": [kb]}).encode()

    async def scenario():
        sent_body = False

        async def receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.sleep(10)
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and b"event: token" in message.get(
                "body", b""
            ):
                raise OSError("client went away")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": f"/api/conversations/{conversation}/messages",
            "raw_path": f"/api/conversations/{conversation}/messages".encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "state": {},
        }
        with pytest.raises(Exception):  # noqa: B017 - Starlette's ClientDisconnect
            await asyncio.wait_for(client.app(scope, receive, send), timeout=3)
        # Already persisted when the app returns, not only when the loop finalizes generators.
        with client.app.state.db.connect() as connection:
            status = connection.execute(
                "SELECT stop_reason FROM runs WHERE conversation_id=?", (conversation,)
            ).fetchone()["stop_reason"]
        assert status == "interrupted"

    asyncio.run(scenario())
    message = client.get(f"/api/conversations/{conversation}").json()["messages"][-1]
    assert message["status"] == "failed"
    run = client.get(f"/api/runs/{message['run_id']}").json()
    assert run["stop_reason"] == "interrupted"
