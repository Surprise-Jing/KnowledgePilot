"""Knowledge base rename and delete (docs/contracts.md §3.2). Owner: A."""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes.helpers import headers, login


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        login(client)
        yield client


def make_kb(client, name="库", user="demo"):
    response = client.post(
        "/api/knowledge-bases", json={"name": name}, headers=headers(client, user)
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_folder(client, kb_id, name, parent_id=None, user="demo"):
    response = client.post(
        f"/api/knowledge-bases/{kb_id}/folders",
        json={"name": name, "parent_id": parent_id},
        headers=headers(client, user),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client, kb_id, name, text, folder_id=None, user="demo"):
    data = {"folder_id": folder_id} if folder_id else {}
    response = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        files={"file": (name, text.encode())},
        data=data,
        headers=headers(client, user),
    )
    assert response.status_code == 202, response.text
    return response.json()


def stored_path(client, document_id):
    with client.app.state.db.connect() as connection:
        row = connection.execute("SELECT path FROM documents WHERE id=?", (document_id,)).fetchone()
    return Path(row["path"])


def error_code(response):
    return response.json()["error"]["code"]


def test_rename_updates_name_and_timestamp(client):
    kb_id = make_kb(client, "旧名")
    with client.app.state.db.connect() as connection:
        connection.execute(
            "UPDATE knowledge_bases SET updated_at='2000-01-01 00:00:00' WHERE id=?", (kb_id,)
        )
    response = client.patch(f"/api/knowledge-bases/{kb_id}", json={"name": "  新名  "})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "新名"
    assert body["updated_at"] > "2000-01-01 00:00:00"
    assert [item["name"] for item in client.get("/api/knowledge-bases").json()] == ["新名"]


def test_rename_rejects_foreign_missing_and_invalid(client):
    kb_id = make_kb(client, "爱丽丝的库", user="alice")
    response = client.patch(
        f"/api/knowledge-bases/{kb_id}", json={"name": "改名"}, headers=headers(client, "bob")
    )
    assert response.status_code == 404 and error_code(response) == "knowledge_base_not_found"
    missing = client.patch("/api/knowledge-bases/missing", json={"name": "改名"})
    assert missing.status_code == 404 and error_code(missing) == "knowledge_base_not_found"
    for payload in ({}, {"name": "   "}, {"name": ""}, {"name": None}):
        response = client.patch(
            f"/api/knowledge-bases/{kb_id}", json=payload, headers=headers(client, "alice")
        )
        assert response.status_code == 422 and error_code(response) == "invalid_argument", payload
    owner_view = client.get("/api/knowledge-bases", headers=headers(client, "alice")).json()
    assert owner_view[0]["name"] == "爱丽丝的库"


def test_delete_cascades_and_removes_files(client):
    kb_id = make_kb(client)
    keep_kb = make_kb(client, "保留")
    folder = make_folder(client, kb_id, "资料")
    child = make_folder(client, kb_id, "子目录", folder)
    docs = [
        upload(client, kb_id, "root.txt", "根目录的星辰笔记"),
        upload(client, kb_id, "nested.txt", "子目录的星辰笔记", folder_id=child),
    ]
    kept = upload(client, keep_kb, "keep.txt", "保留库的星辰笔记")
    paths = [stored_path(client, doc["id"]) for doc in docs]
    assert all(path.exists() for path in paths)

    assert client.delete(f"/api/knowledge-bases/{kb_id}").status_code == 204

    assert [item["id"] for item in client.get("/api/knowledge-bases").json()] == [keep_kb]
    assert not any(path.exists() for path in paths)
    assert stored_path(client, kept["id"]).exists()
    search = client.post("/api/search", json={"query": "星辰笔记", "knowledge_base_ids": [kb_id]})
    assert search.status_code == 404 and error_code(search) == "knowledge_base_not_found"
    remaining = client.post(
        "/api/search", json={"query": "星辰笔记", "knowledge_base_ids": [keep_kb]}
    ).json()
    assert [hit["document_id"] for hit in remaining] == [kept["id"]]
    with client.app.state.db.connect() as connection:
        for table, column in [("folders", "knowledge_base_id"), ("documents", "knowledge_base_id")]:
            count = connection.execute(
                f"SELECT count(*) FROM {table} WHERE {column}=?", (kb_id,)
            ).fetchone()[0]
            assert count == 0, table
        orphan_chunks = connection.execute(
            "SELECT count(*) FROM chunks WHERE document_id IN (?, ?)",
            (docs[0]["id"], docs[1]["id"]),
        ).fetchone()[0]
        assert orphan_chunks == 0
    for document in docs:
        assert client.get(f"/api/documents/{document['id']}").status_code == 404


def test_delete_is_idempotent_and_ignores_foreign_knowledge_bases(client):
    kb_id = make_kb(client, user="alice")
    document = upload(client, kb_id, "a.txt", "爱丽丝的私有资料", user="alice")
    path = stored_path(client, document["id"])

    assert (
        client.delete(f"/api/knowledge-bases/{kb_id}", headers=headers(client, "bob")).status_code
        == 204
    )
    owner_view = client.get("/api/knowledge-bases", headers=headers(client, "alice")).json()
    assert [item["id"] for item in owner_view] == [kb_id]
    assert client.get(f"/api/documents/{document['id']}", headers=headers(client, "alice")).json()[
        "status"
    ] == ("success")
    assert path.exists()

    assert (
        client.delete(f"/api/knowledge-bases/{kb_id}", headers=headers(client, "alice")).status_code
        == 204
    )
    assert (
        client.delete(f"/api/knowledge-bases/{kb_id}", headers=headers(client, "alice")).status_code
        == 204
    )
    assert client.delete("/api/knowledge-bases/never-existed").status_code == 204
    assert not path.exists()


def test_failed_file_removal_is_logged_and_does_not_roll_back(client, monkeypatch, caplog):
    kb_id = make_kb(client)
    document = upload(client, kb_id, "a.txt", "无法删除的文件")
    path = stored_path(client, document["id"])
    original_unlink = Path.unlink

    def failing_unlink(self, missing_ok=False):
        if self == path:
            raise PermissionError("locked")
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    with caplog.at_level(logging.WARNING, logger="app.services.documents"):
        assert client.delete(f"/api/knowledge-bases/{kb_id}").status_code == 204
    assert client.get("/api/knowledge-bases").json() == []
    assert path.exists()
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert any(document["id"] in record.getMessage() for record in warnings)
    # The log names the document, never the internal storage path.
    assert all(str(path) not in record.getMessage() for record in warnings)
