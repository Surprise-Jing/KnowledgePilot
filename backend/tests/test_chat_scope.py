"""Automatic chat and enforced per-question resource boundaries, entirely offline."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import ChatScope
from app.services.chat_scope import resolve_scope
from app.tools.context import ToolContext
from app.tools.registry import execute
from tests.fakes.helpers import (
    install_llm,
    make_conversation,
    make_kb,
    make_settings,
    read_sse,
    upload,
)
from tests.fakes.llm import ScriptedLLM, text, tool_call


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        yield client


def send(client, conversation, scope=None):
    return client.post(
        f"/api/conversations/{conversation}/messages",
        json={"content": "你好", **({"scope": scope} if scope is not None else {})},
    )


def test_auto_without_libraries_can_chat_and_persist(client):
    install_llm(client, ScriptedLLM([text("你好，可以直接交流。")]))
    conversation = make_conversation(client)
    events = read_sse(send(client, conversation))
    assert events[-1][1]["stop_reason"] == "answered"
    assert not any(name == "tool_call" for name, _ in events)
    detail = client.get(f"/api/conversations/{conversation}").json()
    assert detail["messages"][0]["scope"]["selection"]["mode"] == "auto"
    assert detail["messages"][0]["scope"]["knowledge_base_ids"] == []


def test_auto_discovers_all_owned_libraries_and_excludes_other_users(client):
    first, second = make_kb(client, "一"), make_kb(client, "二")
    make_kb(client, "私有", user="bob")
    llm = install_llm(
        client,
        ScriptedLLM(
            [
                [tool_call("list_knowledge_bases", {})],
                text("找到了两个知识库。"),
            ]
        ),
    )
    conversation = make_conversation(client)
    read_sse(send(client, conversation, {"mode": "auto"}))
    snapshot = client.get(f"/api/conversations/{conversation}").json()["messages"][0]["scope"]
    assert set(snapshot["knowledge_base_ids"]) == {first, second}
    assert llm.calls


def test_document_scope_is_enforced_on_search_read_and_listing(client):
    kb = make_kb(client)
    chosen = upload(client, kb, "chosen.txt", "共同关键词：允许的证据。")
    excluded = upload(client, kb, "excluded.txt", "共同关键词：不可见的证据。")
    with client.app.state.db.connect() as connection:
        snapshot = resolve_scope(
            connection, "demo", ChatScope(mode="selected", document_ids=[chosen])
        )
    ctx = ToolContext(
        db=client.app.state.db,
        settings=client.app.state.settings,
        user_id="demo",
        allowed_kb_ids=[kb],
        allowed_document_ids=snapshot.document_ids,
    )
    hits = execute(ctx, "search_chunks", {"query": "共同关键词"})
    assert hits.ok
    assert {hit["document_id"] for hit in hits.data["hits"]} == {chosen}
    denied = execute(ctx, "read_document", {"document_id": excluded})
    assert not denied.ok and denied.error.code == "out_of_scope"
    denied_search = execute(
        ctx, "search_chunks", {"query": "共同关键词", "document_ids": [excluded]}
    )
    assert not denied_search.ok and denied_search.error.code == "out_of_scope"
    listing = execute(ctx, "list_files", {"knowledge_base_id": kb})
    assert listing.data["total"] == 1
    assert listing.data["files"][0]["document_id"] == chosen


def test_folder_scope_includes_descendants_and_empty_folder_stays_empty(client):
    kb = make_kb(client)
    folder = client.post(f"/api/knowledge-bases/{kb}/folders", json={"name": "目录"}).json()["id"]
    child = client.post(
        f"/api/knowledge-bases/{kb}/folders", json={"name": "子目录", "parent_id": folder}
    ).json()["id"]
    chosen = upload(client, kb, "nested.txt", "目录证据")
    client.patch(f"/api/documents/{chosen}", json={"folder_id": child})
    upload(client, kb, "outside.txt", "目录外证据")
    with client.app.state.db.connect() as connection:
        snapshot = resolve_scope(
            connection, "demo", ChatScope(mode="selected", folder_ids=[folder])
        )
    assert snapshot.document_ids == [chosen]
    empty = client.post(f"/api/knowledge-bases/{kb}/folders", json={"name": "空目录"}).json()["id"]
    with client.app.state.db.connect() as connection:
        snapshot = resolve_scope(connection, "demo", ChatScope(mode="selected", folder_ids=[empty]))
    ctx = ToolContext(
        db=client.app.state.db,
        settings=client.app.state.settings,
        user_id="demo",
        allowed_kb_ids=[kb],
        allowed_document_ids=snapshot.document_ids,
    )
    assert execute(ctx, "search_chunks", {"query": "证据"}).data["hits"] == []
    assert execute(ctx, "list_files", {"knowledge_base_id": kb}).data["total"] == 0


def test_selected_scope_snapshot_survives_rename_and_rejects_foreign_resources(client):
    kb = make_kb(client)
    doc = upload(client, kb, "原名称.txt", "公开样例")
    install_llm(client, ScriptedLLM([text("你好")]))
    conversation = make_conversation(client)
    read_sse(send(client, conversation, {"mode": "selected", "document_ids": [doc]}))
    client.patch(f"/api/documents/{doc}", json={"filename": "新名称.txt"})
    snapshot = client.get(f"/api/conversations/{conversation}").json()["messages"][0]["scope"]
    assert snapshot["labels"] == ["原名称.txt"]
    assert snapshot["selection"]["document_ids"] == [doc]
    foreign_kb = make_kb(client, user="bob")
    foreign = upload(client, foreign_kb, "私有.txt", "私有内容", user="bob")
    denied = send(client, conversation, {"mode": "selected", "document_ids": [foreign]})
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "document_not_found"
    assert send(client, conversation, {"mode": "selected"}).status_code == 422


def test_http_scope_reaches_agent_tools_even_when_model_omits_filter(client):
    kb = make_kb(client)
    chosen = upload(client, kb, "允许.txt", "共同关键词：允许")
    upload(client, kb, "排除.txt", "共同关键词：排除")
    llm = install_llm(
        client,
        ScriptedLLM(
            [
                [tool_call("search_chunks", {"query": "共同关键词"})],
                text("允许[c1]"),
            ]
        ),
    )
    conversation = make_conversation(client)
    response = send(client, conversation, {"mode": "selected", "document_ids": [chosen]})
    assert read_sse(response)[-1][1]["stop_reason"] == "answered"
    detail = client.get(f"/api/conversations/{conversation}").json()
    assert {item["document_id"] for item in detail["messages"][-1]["citations"]} == {chosen}
    tool_messages = llm.contents(1, "tool")
    assert "允许.txt" in tool_messages[0]
    assert "排除.txt" not in tool_messages[0]


def test_unready_document_is_rejected_before_run_creation(client):
    kb = make_kb(client)
    doc = upload(client, kb, "解析中.txt", "公开样例")
    with client.app.state.db.connect() as connection:
        connection.execute("UPDATE documents SET status='parsing' WHERE id=?", (doc,))
    conversation = make_conversation(client)
    response = send(client, conversation, {"mode": "selected", "document_ids": [doc]})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "document_not_ready"
    assert client.get(f"/api/conversations/{conversation}").json()["messages"] == []


def test_automatic_scope_is_not_limited_by_public_search_batch_size(client):
    libraries = [make_kb(client, str(index)) for index in range(21)]
    doc = upload(client, libraries[-1], "最后一个库.txt", "独特检索词")
    ctx = ToolContext(
        db=client.app.state.db,
        settings=client.app.state.settings,
        user_id="demo",
        allowed_kb_ids=libraries,
    )
    result = execute(ctx, "search_chunks", {"query": "独特检索词"})
    assert result.ok
    assert result.data["hits"][0]["document_id"] == doc
