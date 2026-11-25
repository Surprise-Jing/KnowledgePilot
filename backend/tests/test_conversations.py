"""Conversation CRUD, pre-stream errors and persistence rules (docs/contracts.md §3.3).
Owner: B."""

import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from app.main import create_app
from app.schemas import Usage
from app.services import conversations
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
from tests.fakes.llm import ScriptedLLM, text, tool_call


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def error_of(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    return error


def test_create_list_detail_delete(client):
    response = client.post("/api/conversations", json={})
    assert response.status_code == 201
    first = response.json()
    assert first["title"] == "新对话"
    assert set(first) == {"id", "title", "created_at", "updated_at"}
    named = client.post("/api/conversations", json={"title": "  我的会话 "}).json()
    assert named["title"] == "我的会话"
    detail = client.get(f"/api/conversations/{first['id']}").json()
    assert detail["messages"] == [] and detail["id"] == first["id"]
    assert client.delete(f"/api/conversations/{first['id']}").status_code == 204
    assert client.delete(f"/api/conversations/{first['id']}").status_code == 204
    error_of(client.get(f"/api/conversations/{first['id']}"), 404, "conversation_not_found")
    assert [c["id"] for c in client.get("/api/conversations").json()] == [named["id"]]


def test_list_orders_by_recent_activity(client):
    kb = make_kb(client)
    install_llm(client, ScriptedLLM([text("好的。")]))
    older, newer = make_conversation(client), make_conversation(client)
    assert [c["id"] for c in client.get("/api/conversations").json()] == [newer, older]
    read_sse(ask(client, older, "问题", [kb]))
    assert [c["id"] for c in client.get("/api/conversations").json()] == [older, newer]


def test_conversations_are_private(client):
    mine = make_conversation(client, user="alice")
    assert client.get("/api/conversations", headers=headers("bob")).json() == []
    error_of(
        client.get(f"/api/conversations/{mine}", headers=headers("bob")),
        404,
        "conversation_not_found",
    )
    # Deleting someone else's conversation is a no-op that still answers 204.
    assert client.delete(f"/api/conversations/{mine}", headers=headers("bob")).status_code == 204
    assert client.get(f"/api/conversations/{mine}", headers=headers("alice")).status_code == 200
    kb = make_kb(client, user="bob")
    install_llm(client, ScriptedLLM([]))
    error_of(ask(client, mine, "问题", [kb], user="bob"), 404, "conversation_not_found")


def test_pre_stream_errors_are_json(client):
    install_llm(client, ScriptedLLM([]))
    kb = make_kb(client)
    foreign_kb = make_kb(client, user="bob")
    conversation = make_conversation(client)
    error_of(ask(client, "missing", "问题", [kb]), 404, "conversation_not_found")
    error_of(ask(client, conversation, "问题", [foreign_kb]), 404, "knowledge_base_not_found")
    error_of(ask(client, conversation, "问题", ["missing"]), 404, "knowledge_base_not_found")
    error_of(ask(client, conversation, "   ", [kb]), 422, "invalid_argument")
    error_of(ask(client, conversation, "问题", []), 422, "invalid_argument")
    error_of(ask(client, conversation, "x" * 4001, [kb]), 422, "invalid_argument")
    response = client.post(
        f"/api/conversations/{conversation}/messages",
        json={"content": "q", "scope": {"mode": "selected"}},
    )
    error_of(response, 422, "invalid_argument")
    # Nothing was written for rejected questions.
    assert client.get(f"/api/conversations/{conversation}").json()["messages"] == []


def test_model_not_configured_is_a_json_error(tmp_path):
    settings = make_settings(tmp_path, llm_model="")
    with TestClient(create_app(settings)) as client:
        assert client.app.state.llm_factory is None
        kb = make_kb(client)
        conversation = make_conversation(client)
        error = error_of(ask(client, conversation, "问题", [kb]), 503, "model_not_configured")
        assert error["retryable"] is False
        detail = client.get(f"/api/conversations/{conversation}").json()
        assert detail["messages"] == [] and detail["title"] == "新对话"


def test_first_question_names_the_conversation(client):
    kb = make_kb(client)
    install_llm(client, ScriptedLLM([text("一"), text("二"), text("三")]))
    conversation = make_conversation(client)
    question = "请总结一下知识库中关于删除文档时索引如何同步清理的全部规则和注意事项"
    assert len(question) > 30
    read_sse(ask(client, conversation, question, [kb]))
    assert client.get(f"/api/conversations/{conversation}").json()["title"] == question[:30]
    read_sse(ask(client, conversation, "第二个问题", [kb]))
    assert client.get(f"/api/conversations/{conversation}").json()["title"] == question[:30]
    titled = make_conversation(client, title="自定义标题")
    read_sse(ask(client, titled, question, [kb]))
    assert client.get(f"/api/conversations/{titled}").json()["title"] == "自定义标题"


def test_detail_lists_messages_with_runs_and_citations(client):
    kb = make_kb(client)
    upload(client, kb, "guide.txt", "删除文档时同步清理索引。")
    install_llm(
        client,
        ScriptedLLM([[tool_call("search_chunks", {"query": "删除文档"})], text("会清理 [c1]。")]),
    )
    conversation = make_conversation(client)
    events = read_sse(ask(client, conversation, "删除文档会怎样？", [kb]))
    messages = client.get(f"/api/conversations/{conversation}").json()["messages"]
    assert [(m["role"], m["status"]) for m in messages] == [
        ("user", "completed"),
        ("assistant", "completed"),
    ]
    assert messages[0]["run_id"] is None and messages[0]["citations"] == []
    assert messages[1]["run_id"] == events[0][1]["run_id"]
    assert messages[0]["id"] == events[0][1]["user_message_id"]
    assert messages[1]["id"] == events[0][1]["assistant_message_id"]
    citation = messages[1]["citations"][0]
    assert citation["marker"] == "c1" and citation["filename"] == "guide.txt"
    assert citation["location"]["start"] == 1 and citation["location_label"] == "第 1 行"


def test_citation_snapshot_survives_document_deletion(client):
    kb = make_kb(client)
    document = upload(client, kb, "guide.txt", "删除文档时同步清理索引。")
    install_llm(
        client,
        ScriptedLLM([[tool_call("search_chunks", {"query": "删除文档"})], text("会清理 [c1]。")]),
    )
    conversation = make_conversation(client)
    read_sse(ask(client, conversation, "问题", [kb]))
    assert client.delete(f"/api/documents/{document}").status_code == 204
    citation = client.get(f"/api/conversations/{conversation}").json()["messages"][1]["citations"][
        0
    ]
    assert citation["chunk_id"] is None and citation["document_id"] is None
    assert citation["content"] == "删除文档时同步清理索引。"


def test_conversation_busy(client, settings):
    kb = make_kb(client)
    conversation = make_conversation(client)
    db = client.app.state.db
    started = conversations.start_run(db, "demo", conversation, "第一个问题", [kb], "m")
    install_llm(client, ScriptedLLM([]))
    error = error_of(ask(client, conversation, "第二个问题", [kb]), 409, "conversation_busy")
    assert error["retryable"] is True
    with pytest.raises(AppError) as raised:
        conversations.start_run(db, "demo", conversation, "第三个问题", [kb], "m")
    assert raised.value.code == "conversation_busy"
    # Rejected questions leave no messages behind; another conversation is unaffected.
    messages = client.get(f"/api/conversations/{conversation}").json()["messages"]
    assert [m["content"] for m in messages] == ["第一个问题", ""]
    install_llm(client, ScriptedLLM([text("好的。")]))
    other = make_conversation(client)
    assert read_sse(ask(client, other, "问题", [kb]))[-1][0] == "done"
    # Once the first run is finished the conversation accepts questions again.
    conversations.interrupt_run(db, started.run_id, content=None, step_count=0, usage=Usage())
    install_llm(client, ScriptedLLM([text("好的。")]))
    assert read_sse(ask(client, conversation, "第二个问题", [kb]))[-1][1]["stop_reason"] == (
        "answered"
    )


def test_finish_run_is_applied_once(client):
    kb = make_kb(client)
    conversation = make_conversation(client)
    db = client.app.state.db
    started = conversations.start_run(db, "demo", conversation, "问题", [kb], "m")
    usage = Usage(prompt_tokens=1, completion_tokens=2)
    assert conversations.interrupt_run(
        db, started.run_id, content="部分", step_count=1, usage=usage
    )
    assert not conversations.interrupt_run(
        db, started.run_id, content="x", step_count=9, usage=usage
    )
    run = conversations.get_run(db, "demo", started.run_id)
    assert (run.status, run.stop_reason, run.step_count) == ("failed", "interrupted", 1)
    assert (run.prompt_tokens, run.completion_tokens) == (1, 2)
    message = client.get(f"/api/conversations/{conversation}").json()["messages"][1]
    assert message["content"] == "部分" and message["status"] == "failed"


def test_run_degradation_and_retry_links_are_persisted(client):
    kb = make_kb(client)
    conversation = make_conversation(client)
    db = client.app.state.db
    started = conversations.start_run(db, "demo", conversation, "问题", [kb], "m")
    first = conversations.insert_tool_call(db, started.run_id, 1, "read_document", {"a": 1})
    retry = conversations.insert_tool_call(
        db, started.run_id, 1, "read_document", {"a": 1}, attempt=2, retry_of=first
    )
    conversations.finish_tool_call(
        db,
        retry,
        ok=True,
        summary="读取原文失败，已改用本次已检索到的片段",
        result_json="{}",
        error=None,
        duration_ms=1,
        fallback="retrieved_snippets",
    )
    assert conversations.interrupt_run(
        db,
        started.run_id,
        content="部分",
        step_count=1,
        usage=Usage(),
        degraded=True,
        model_retries=1,
    )
    run = client.get(f"/api/runs/{started.run_id}").json()
    assert run["degraded"] is True and run["model_retries"] == 1
    calls = run["tool_calls"]
    assert [(c["attempt"], c["retry_of"], c["fallback"]) for c in calls] == [
        (1, None, None),
        (2, first, "retrieved_snippets"),
    ]
    # The first attempt never finished: it is closed like any abandoned call.
    assert calls[0]["status"] == "error"


def test_delete_conversation_cascades_runs(client):
    kb = make_kb(client)
    install_llm(client, ScriptedLLM([[tool_call("list_knowledge_bases", {})], text("好")]))
    conversation = make_conversation(client)
    run_id = read_sse(ask(client, conversation, "问题", [kb]))[0][1]["run_id"]
    assert client.get(f"/api/runs/{run_id}").status_code == 200
    assert client.delete(f"/api/conversations/{conversation}").status_code == 204
    error_of(client.get(f"/api/runs/{run_id}"), 404, "run_not_found")
    with client.app.state.db.connect() as connection:
        for table in ("messages", "runs", "tool_calls", "citations"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_rename_conversation_preserves_messages_and_checks_owner(client):
    kb = make_kb(client)
    install_llm(client, ScriptedLLM([text("好的")]))
    conversation = make_conversation(client)
    read_sse(ask(client, conversation, "公开测试问题", [kb]))
    before = client.get(f"/api/conversations/{conversation}").json()
    response = client.patch(f"/api/conversations/{conversation}", json={"title": "  财报讨论  "})
    assert response.status_code == 200
    assert response.json()["title"] == "财报讨论"
    after = client.get(f"/api/conversations/{conversation}").json()
    assert after["messages"] == before["messages"]
    denied = client.patch(
        f"/api/conversations/{conversation}", json={"title": "越权"}, headers=headers("bob")
    )
    error_of(denied, 404, "conversation_not_found")
    for title in ["   ", "x" * 101]:
        error_of(
            client.patch(f"/api/conversations/{conversation}", json={"title": title}),
            422,
            "invalid_argument",
        )
    assert client.get(f"/api/conversations/{conversation}").json()["title"] == "财报讨论"
