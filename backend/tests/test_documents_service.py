"""Milestone M1: list_files and read_document_range (docs/contracts.md §3.4). Owner: A."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.services.documents import list_files, read_document_range
from tests.fakes.helpers import headers, login


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        login(client)
        yield client


def make_kb(client, user="demo"):
    response = client.post(
        "/api/knowledge-bases", json={"name": "库"}, headers=headers(client, user)
    )
    return response.json()["id"]


def upload(client, kb_id, name, text, user="demo"):
    response = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        files={"file": (name, text.encode())},
        headers=headers(client, user),
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def connect(client):
    return client.app.state.db.connect()


def add_folder(client, kb_id, folder_id, name, parent_id=None):
    # The folder API is not implemented yet (A1); insert rows directly.
    with connect(client) as connection:
        connection.execute(
            "INSERT INTO folders(id, knowledge_base_id, parent_id, name) VALUES (?, ?, ?, ?)",
            (folder_id, kb_id, parent_id, name),
        )


def place(client, document_id, folder_id):
    with connect(client) as connection:
        connection.execute("UPDATE documents SET folder_id=? WHERE id=?", (folder_id, document_id))


@pytest.fixture
def tree(client):
    """root: r.txt; a/: a.txt; a/b/: b.txt; c/: c.txt"""
    kb_id = make_kb(client)
    add_folder(client, kb_id, "fa", "a")
    add_folder(client, kb_id, "fb", "b", "fa")
    add_folder(client, kb_id, "fc", "c")
    ids = {}
    for name, folder in [("r.txt", None), ("a.txt", "fa"), ("b.txt", "fb"), ("c.txt", "fc")]:
        ids[name] = upload(client, kb_id, name, f"内容 {name}")
        place(client, ids[name], folder)
    return kb_id, ids


def names(listing):
    return [entry.filename for entry in listing.files]


def test_list_files_scopes_and_order(client, tree):
    kb_id, _ = tree
    with connect(client) as connection:
        everything = list_files(connection, "demo", kb_id)
        assert names(everything) == ["r.txt", "a.txt", "b.txt", "c.txt"]
        assert everything.total == 4
        assert [entry.folder_path for entry in everything.files][2] == ["a", "b"]
        assert names(list_files(connection, "demo", kb_id, recursive=False)) == ["r.txt"]
        assert names(list_files(connection, "demo", kb_id, folder_id="fa")) == ["a.txt", "b.txt"]
        assert names(list_files(connection, "demo", kb_id, folder_id="fa", recursive=False)) == [
            "a.txt"
        ]


def test_list_files_pagination(client, tree):
    kb_id, _ = tree
    with connect(client) as connection:
        page = list_files(connection, "demo", kb_id, offset=1, limit=2)
    assert names(page) == ["a.txt", "b.txt"]
    assert (page.total, page.offset, page.limit) == (4, 1, 2)


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 51}, {"offset": -1}])
def test_list_files_rejects_bad_paging(client, tree, kwargs):
    kb_id, _ = tree
    with connect(client) as connection, pytest.raises(AppError) as error:
        list_files(connection, "demo", kb_id, **kwargs)
    assert error.value.code == "invalid_argument"


def test_list_files_ownership(client, tree):
    kb_id, _ = tree
    other_kb = make_kb(client, "bob")
    add_folder(client, other_kb, "fx", "x")
    with connect(client) as connection:
        with pytest.raises(AppError) as error:
            list_files(connection, "bob", kb_id)
        assert error.value.code == "knowledge_base_not_found"
        with pytest.raises(AppError) as error:
            list_files(connection, "demo", kb_id, folder_id="fx")
        assert error.value.code == "folder_not_found"


def test_read_document_range_pages_through_document(client):
    kb_id = make_kb(client)
    # Each line is ~700 chars, so every line becomes its own chunk.
    lines = [f"第{i}段" + "字" * 700 for i in range(4)]
    document_id = upload(client, kb_id, "long.txt", "\n".join(lines))
    with connect(client) as connection:
        first = read_document_range(connection, "demo", document_id, max_chars=1500)
        assert [chunk.ordinal for chunk in first.chunks] == [0, 1]
        assert first.truncated and first.next_ordinal == 2 and first.total_chunks == 4
        rest = read_document_range(
            connection, "demo", document_id, start_ordinal=first.next_ordinal, max_chars=8000
        )
        assert [chunk.ordinal for chunk in rest.chunks] == [2, 3]
        assert not rest.truncated and rest.next_ordinal is None
        # A chunk larger than max_chars is still returned so reading always progresses.
        single = read_document_range(connection, "demo", document_id, max_chars=500)
        assert len(single.chunks) == 1 and single.next_ordinal == 1
        assert single.chunks[0].location.label == "第 1 行"


def test_read_document_range_errors(client):
    kb_id = make_kb(client)
    document_id = upload(client, kb_id, "a.txt", "一行内容")
    with connect(client) as connection:
        for kwargs in ({"start_ordinal": 1}, {"start_ordinal": -1}, {"max_chars": 100}):
            with pytest.raises(AppError) as error:
                read_document_range(connection, "demo", document_id, **kwargs)
            assert error.value.code == "invalid_argument"
        with pytest.raises(AppError) as error:
            read_document_range(connection, "bob", document_id)
        assert error.value.code == "document_not_found"
        for status, retryable in [("queued", True), ("parsing", True), ("failed", False)]:
            connection.execute("UPDATE documents SET status=? WHERE id=?", (status, document_id))
            with pytest.raises(AppError) as error:
                read_document_range(connection, "demo", document_id)
            assert error.value.code == "document_not_ready"
            assert error.value.retryable is retryable
