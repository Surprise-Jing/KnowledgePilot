"""Upload into folders, duplicates, folder-scoped listing, rename/move and idempotent delete
(docs/contracts.md §3.1–§3.2). Owner: A."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import documents as documents_service
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


def post_file(client, kb_id, name, text, folder_id=None, user="demo"):
    return client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        files={"file": (name, text.encode())},
        data={} if folder_id is None else {"folder_id": folder_id},
        headers=headers(client, user),
    )


def upload(client, kb_id, name, text, folder_id=None, user="demo"):
    response = post_file(client, kb_id, name, text, folder_id, user)
    assert response.status_code == 202, response.text
    return response.json()


def listing(client, kb_id, **params):
    response = client.get(f"/api/knowledge-bases/{kb_id}/documents", params=params)
    assert response.status_code == 200, response.text
    # Upload order uses second-resolution timestamps, so compare names regardless of order.
    return sorted(item["filename"] for item in response.json())


def patch(client, document_id, payload, user="demo"):
    return client.patch(
        f"/api/documents/{document_id}", json=payload, headers=headers(client, user)
    )


def search(client, kb_id, text):
    response = client.post("/api/search", json={"query": text, "knowledge_base_ids": [kb_id]})
    assert response.status_code == 200, response.text
    return response.json()


def stored_files(settings):
    return sorted(path.name for path in (settings.data_dir / "uploads").iterdir())


def error_code(response):
    return response.json()["error"]["code"]


def test_upload_into_folder(client, settings):
    kb_id = make_kb(client)
    folder = make_folder(client, kb_id, "资料")
    document = upload(client, kb_id, "a.txt", "放进目录的文件", folder_id=folder)
    assert document["folder_id"] == folder and document["duplicate"] is False
    assert client.get(f"/api/documents/{document['id']}").json()["status"] == "success"
    # An empty form value means the root, as HTML forms send it.
    root = upload(client, kb_id, "b.txt", "根目录的文件", folder_id="")
    assert root["folder_id"] is None

    other_kb = make_kb(client, "另一个库")
    foreign = make_folder(client, other_kb, "外部")
    before = stored_files(settings)
    for folder_id in (foreign, "missing"):
        response = post_file(client, kb_id, "c.txt", "不该保存的文件", folder_id=folder_id)
        assert response.status_code == 404 and error_code(response) == "folder_not_found"
    assert stored_files(settings) == before
    assert len(listing(client, kb_id)) == 2

    response = post_file(client, kb_id, "c.txt", "别人的库", user="bob")
    assert response.status_code == 404 and error_code(response) == "knowledge_base_not_found"


def test_duplicate_upload_returns_existing_record(client, settings):
    kb_id = make_kb(client)
    folder = make_folder(client, kb_id, "资料")
    first = upload(client, kb_id, "a.txt", "重复的内容")
    assert first["duplicate"] is False
    again = upload(client, kb_id, "copy.txt", "重复的内容", folder_id=folder)
    assert again["duplicate"] is True
    # The existing record is returned unchanged, still in its original folder.
    assert (again["id"], again["filename"], again["folder_id"]) == (first["id"], "a.txt", None)
    assert len(stored_files(settings)) == 1
    assert client.get(f"/api/documents/{first['id']}").json()["duplicate"] is False
    # Duplicates are per knowledge base.
    other = upload(client, make_kb(client, "另一个库"), "a.txt", "重复的内容")
    assert other["duplicate"] is False and other["id"] != first["id"]


def test_duplicate_detected_by_unique_constraint(client, settings, monkeypatch):
    """A concurrent upload can insert the same content after the pre-check; the unique
    constraint catches it and the winner is returned as a duplicate."""
    kb_id = make_kb(client)
    first = upload(client, kb_id, "a.txt", "并发上传的内容")
    real = documents_service._find_duplicate
    calls = []

    def miss_first_check(connection, kb, digest):
        calls.append(digest)
        return None if len(calls) == 1 else real(connection, kb, digest)

    monkeypatch.setattr(documents_service, "_find_duplicate", miss_first_check)
    again = upload(client, kb_id, "b.txt", "并发上传的内容")
    assert len(calls) == 2
    assert again["duplicate"] is True and again["id"] == first["id"]
    assert len(stored_files(settings)) == 1


@pytest.fixture
def tree(client):
    """root: r.txt; a/: a.txt; a/b/: b.txt; c/: c.txt"""
    kb_id = make_kb(client)
    a = make_folder(client, kb_id, "a")
    b = make_folder(client, kb_id, "b", a)
    c = make_folder(client, kb_id, "c")
    for name, folder in [("r.txt", None), ("a.txt", a), ("b.txt", b), ("c.txt", c)]:
        upload(client, kb_id, name, f"内容 {name}", folder_id=folder)
    return kb_id, {"a": a, "b": b, "c": c}


def test_list_by_folder(client, tree):
    kb_id, ids = tree
    everything = ["a.txt", "b.txt", "c.txt", "r.txt"]
    assert listing(client, kb_id) == everything
    assert listing(client, kb_id, recursive="true") == everything
    assert listing(client, kb_id, folder_id="") == everything
    assert listing(client, kb_id, recursive="false") == ["r.txt"]
    assert listing(client, kb_id, folder_id=ids["a"]) == ["a.txt", "b.txt"]
    assert listing(client, kb_id, folder_id=ids["a"], recursive="false") == ["a.txt"]
    assert listing(client, kb_id, folder_id=ids["b"]) == ["b.txt"]

    other_kb = make_kb(client, "另一个库")
    foreign = make_folder(client, other_kb, "外部")
    for folder_id in (foreign, "missing"):
        response = client.get(
            f"/api/knowledge-bases/{kb_id}/documents", params={"folder_id": folder_id}
        )
        assert response.status_code == 404 and error_code(response) == "folder_not_found"
    response = client.get(f"/api/knowledge-bases/{kb_id}/documents", headers=headers(client, "bob"))
    assert response.status_code == 404 and error_code(response) == "knowledge_base_not_found"


def test_rename_keeps_type_and_chunks(client):
    kb_id = make_kb(client)
    document = upload(client, kb_id, "旧名.txt", "重命名后仍可检索的萤火虫资料")
    chunks_before = client.get(f"/api/documents/{document['id']}/chunks").json()
    with client.app.state.db.connect() as connection:
        connection.execute(
            "UPDATE documents SET updated_at='2000-01-01 00:00:00' WHERE id=?", (document["id"],)
        )

    response = patch(client, document["id"], {"filename": "  新名.pdf  "})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["filename"] == "新名.pdf"
    assert body["file_type"] == "txt" and body["status"] == "success"
    assert body["folder_id"] is None
    assert body["updated_at"] > "2000-01-01 00:00:00"
    assert client.get(f"/api/documents/{document['id']}/chunks").json() == chunks_before
    hits = search(client, kb_id, "萤火虫资料")
    assert [(hit["document_id"], hit["filename"]) for hit in hits] == [(document["id"], "新名.pdf")]


def test_move_within_knowledge_base(client):
    kb_id = make_kb(client)
    a = make_folder(client, kb_id, "a")
    b = make_folder(client, kb_id, "b", a)
    document = upload(client, kb_id, "doc.txt", "要移动的文件")

    moved = patch(client, document["id"], {"folder_id": b})
    assert moved.status_code == 200 and moved.json()["folder_id"] == b
    assert listing(client, kb_id, folder_id=a, recursive="false") == []
    assert listing(client, kb_id, folder_id=b) == ["doc.txt"]

    both = patch(client, document["id"], {"folder_id": a, "filename": "renamed.txt"})
    assert (both.json()["folder_id"], both.json()["filename"]) == (a, "renamed.txt")

    to_root = patch(client, document["id"], {"folder_id": None})
    assert to_root.status_code == 200 and to_root.json()["folder_id"] is None
    assert to_root.json()["filename"] == "renamed.txt"


def test_update_rejects_invalid_requests(client):
    kb_id = make_kb(client)
    folder = make_folder(client, kb_id, "a")
    document = upload(client, kb_id, "doc.txt", "不能乱动的文件", folder_id=folder)

    other_kb = make_kb(client, "另一个库")
    foreign = make_folder(client, other_kb, "外部")
    for folder_id in (foreign, "missing"):
        response = patch(client, document["id"], {"folder_id": folder_id})
        assert response.status_code == 404 and error_code(response) == "folder_not_found"

    for payload in ({}, {"filename": None}, {"filename": "a/b.txt"}, {"filename": "a\\b.txt"}):
        response = patch(client, document["id"], payload)
        assert response.status_code == 422 and error_code(response) == "invalid_argument", payload

    response = patch(client, document["id"], {"filename": "x.txt"}, user="bob")
    assert response.status_code == 404 and error_code(response) == "document_not_found"
    response = patch(client, "missing", {"filename": "x.txt"})
    assert response.status_code == 404 and error_code(response) == "document_not_found"

    current = client.get(f"/api/documents/{document['id']}").json()
    assert (current["filename"], current["folder_id"]) == ("doc.txt", folder)


def test_delete_is_idempotent(client, settings):
    kb_id = make_kb(client)
    document = upload(client, kb_id, "doc.txt", "将被删除的海螺资料")
    with client.app.state.db.connect() as connection:
        path = Path(
            connection.execute(
                "SELECT path FROM documents WHERE id=?", (document["id"],)
            ).fetchone()["path"]
        )

    # Another user's delete is a silent no-op.
    response = client.delete(f"/api/documents/{document['id']}", headers=headers(client, "bob"))
    assert response.status_code == 204
    assert path.exists()
    assert client.get(f"/api/documents/{document['id']}").status_code == 200

    assert client.delete(f"/api/documents/{document['id']}").status_code == 204
    assert not path.exists()
    assert client.get(f"/api/documents/{document['id']}").status_code == 404
    assert search(client, kb_id, "海螺资料") == []
    assert client.delete(f"/api/documents/{document['id']}").status_code == 204
    assert client.delete("/api/documents/never-existed").status_code == 204
