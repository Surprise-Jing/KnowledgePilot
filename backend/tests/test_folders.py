"""Folder tree CRUD (docs/contracts.md §3.2). Owner: A."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        yield client


def as_user(user):
    return {"X-User-Id": user}


def make_kb(client, name="库", user="demo"):
    response = client.post("/api/knowledge-bases", json={"name": name}, headers=as_user(user))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_folder(client, kb_id, name, parent_id=None, user="demo"):
    payload = {"name": name} if parent_id is None else {"name": name, "parent_id": parent_id}
    return client.post(f"/api/knowledge-bases/{kb_id}/folders", json=payload, headers=as_user(user))


def make_folder(client, kb_id, name, parent_id=None, user="demo"):
    response = create_folder(client, kb_id, name, parent_id, user)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def folders(client, kb_id, user="demo"):
    response = client.get(f"/api/knowledge-bases/{kb_id}/folders", headers=as_user(user))
    assert response.status_code == 200, response.text
    return response.json()


def patch(client, folder_id, payload, user="demo"):
    return client.patch(f"/api/folders/{folder_id}", json=payload, headers=as_user(user))


def upload(client, kb_id, name, text, folder_id=None):
    response = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        files={"file": (name, text.encode())},
        data={"folder_id": folder_id} if folder_id else {},
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def stored_path(client, document_id):
    with client.app.state.db.connect() as connection:
        row = connection.execute("SELECT path FROM documents WHERE id=?", (document_id,)).fetchone()
    return Path(row["path"])


def error_code(response):
    return response.json()["error"]["code"]


@pytest.fixture
def tree(client):
    """a/ → b/ → c/ → d/ (four levels) and a sibling root folder z/."""
    kb_id = make_kb(client)
    a = make_folder(client, kb_id, "a")
    b = make_folder(client, kb_id, "b", a)
    c = make_folder(client, kb_id, "c", b)
    d = make_folder(client, kb_id, "d", c)
    z = make_folder(client, kb_id, "z")
    return kb_id, {"a": a, "b": b, "c": c, "d": d, "z": z}


def test_nested_folders_are_listed_flat_by_name(client, tree):
    kb_id, ids = tree
    listing = folders(client, kb_id)
    assert [item["name"] for item in listing] == ["a", "b", "c", "d", "z"]
    parents = {item["id"]: item["parent_id"] for item in listing}
    assert parents == {
        ids["a"]: None,
        ids["b"]: ids["a"],
        ids["c"]: ids["b"],
        ids["d"]: ids["c"],
        ids["z"]: None,
    }
    assert all(item["knowledge_base_id"] == kb_id for item in listing)


def test_create_validates_names_and_parents(client, tree):
    kb_id, ids = tree
    for parent in (None, ids["a"]):
        existing = "a" if parent is None else "b"
        response = create_folder(client, kb_id, existing, parent)
        assert response.status_code == 409 and error_code(response) == "name_conflict"
    # Same name under a different parent is fine.
    assert create_folder(client, kb_id, "b", ids["z"]).status_code == 201

    other_kb = make_kb(client, "另一个库")
    foreign = make_folder(client, other_kb, "x")
    for parent in (foreign, "missing"):
        response = create_folder(client, kb_id, "新目录", parent)
        assert response.status_code == 404 and error_code(response) == "folder_not_found"

    for name in ("a/b", "a\\b", "", "   "):
        response = create_folder(client, kb_id, name)
        assert response.status_code == 422 and error_code(response) == "invalid_argument", name

    missing_kb = create_folder(client, "missing", "x")
    assert missing_kb.status_code == 404 and error_code(missing_kb) == "knowledge_base_not_found"


def test_other_users_cannot_see_or_use_folders(client, tree):
    kb_id, ids = tree
    response = client.get(f"/api/knowledge-bases/{kb_id}/folders", headers=as_user("bob"))
    assert response.status_code == 404 and error_code(response) == "knowledge_base_not_found"
    response = create_folder(client, kb_id, "入侵", user="bob")
    assert response.status_code == 404 and error_code(response) == "knowledge_base_not_found"
    response = patch(client, ids["a"], {"name": "改名"}, user="bob")
    assert response.status_code == 404 and error_code(response) == "folder_not_found"
    # A folder of another user cannot be used as a parent either.
    bob_kb = make_kb(client, user="bob")
    response = create_folder(client, bob_kb, "x", ids["a"], user="bob")
    assert response.status_code == 404 and error_code(response) == "folder_not_found"
    assert len(folders(client, kb_id)) == 5


def test_rename_and_move(client, tree):
    kb_id, ids = tree
    with client.app.state.db.connect() as connection:
        connection.execute("UPDATE folders SET updated_at='2000-01-01 00:00:00'")
    renamed = patch(client, ids["c"], {"name": "c2"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "c2" and renamed.json()["parent_id"] == ids["b"]
    assert renamed.json()["updated_at"] > "2000-01-01 00:00:00"

    moved = patch(client, ids["c"], {"parent_id": ids["z"]})
    assert moved.json()["parent_id"] == ids["z"] and moved.json()["name"] == "c2"
    # The subtree moves along: d still hangs below c.
    parents = {item["id"]: item["parent_id"] for item in folders(client, kb_id)}
    assert parents[ids["d"]] == ids["c"]

    to_root = patch(client, ids["c"], {"parent_id": None})
    assert to_root.status_code == 200 and to_root.json()["parent_id"] is None

    both = patch(client, ids["c"], {"name": "c3", "parent_id": ids["a"]})
    assert (both.json()["name"], both.json()["parent_id"]) == ("c3", ids["a"])


def test_update_rejects_invalid_requests(client, tree):
    kb_id, ids = tree
    for payload in ({}, {"name": None}, {"name": "x/y"}, {"name": "x\\y"}, {"name": "  "}):
        response = patch(client, ids["b"], payload)
        assert response.status_code == 422 and error_code(response) == "invalid_argument", payload

    # Renaming to a sibling's name, or moving next to a same-named folder.
    response = patch(client, ids["z"], {"name": "a"})
    assert response.status_code == 409 and error_code(response) == "name_conflict"
    response = patch(client, ids["b"], {"parent_id": None, "name": "z"})
    assert response.status_code == 409 and error_code(response) == "name_conflict"

    other_kb = make_kb(client, "另一个库")
    foreign = make_folder(client, other_kb, "x")
    for parent in (foreign, "missing"):
        response = patch(client, ids["b"], {"parent_id": parent})
        assert response.status_code == 404 and error_code(response) == "folder_not_found"

    response = patch(client, "missing", {"name": "x"})
    assert response.status_code == 404 and error_code(response) == "folder_not_found"
    # Nothing above changed the tree.
    listing = {item["id"]: (item["name"], item["parent_id"]) for item in folders(client, kb_id)}
    assert listing[ids["b"]] == ("b", ids["a"])
    assert listing[ids["z"]] == ("z", None)


@pytest.mark.parametrize("target", ["a", "b", "c", "d"])
def test_move_into_itself_or_descendant_is_a_cycle(client, tree, target):
    kb_id, ids = tree
    response = patch(client, ids["a"], {"parent_id": ids[target]})
    assert response.status_code == 409 and error_code(response) == "folder_cycle"
    parents = {item["id"]: item["parent_id"] for item in folders(client, kb_id)}
    assert parents[ids["a"]] is None


def test_move_next_to_itself_is_not_a_cycle(client, tree):
    _, ids = tree
    # d's ancestor chain contains a, but moving d up under a is a legal move.
    response = patch(client, ids["d"], {"parent_id": ids["a"]})
    assert response.status_code == 200 and response.json()["parent_id"] == ids["a"]


def test_delete_empty_and_non_empty_folders(client, tree):
    kb_id, ids = tree
    assert client.delete(f"/api/folders/{ids['z']}").status_code == 204
    # Child folder only.
    response = client.delete(f"/api/folders/{ids['c']}")
    assert response.status_code == 409 and error_code(response) == "folder_not_empty"
    # Document only.
    upload(client, kb_id, "d.txt", "最深层的文件", folder_id=ids["d"])
    response = client.delete(f"/api/folders/{ids['d']}?recursive=false")
    assert response.status_code == 409 and error_code(response) == "folder_not_empty"
    assert {item["name"] for item in folders(client, kb_id)} == {"a", "b", "c", "d"}
    response = client.delete(f"/api/folders/{ids['d']}?recursive=maybe")
    assert response.status_code == 422 and error_code(response) == "invalid_argument"


def test_recursive_delete_removes_subtree_and_files(client, tree):
    kb_id, ids = tree
    inside = {
        "b": upload(client, kb_id, "b.txt", "第二层的极光资料", folder_id=ids["b"]),
        "d": upload(client, kb_id, "d.txt", "第四层的极光资料", folder_id=ids["d"]),
    }
    outside = {
        "a": upload(client, kb_id, "a.txt", "第一层的极光资料", folder_id=ids["a"]),
        "z": upload(client, kb_id, "z.txt", "兄弟目录的极光资料", folder_id=ids["z"]),
        "root": upload(client, kb_id, "r.txt", "根目录的极光资料"),
    }
    inside_paths = [stored_path(client, doc) for doc in inside.values()]
    outside_paths = [stored_path(client, doc) for doc in outside.values()]

    assert client.delete(f"/api/folders/{ids['b']}?recursive=true").status_code == 204

    assert {item["name"] for item in folders(client, kb_id)} == {"a", "z"}
    assert not any(path.exists() for path in inside_paths)
    assert all(path.exists() for path in outside_paths)
    for document_id in inside.values():
        assert client.get(f"/api/documents/{document_id}").status_code == 404
    hits = client.post("/api/search", json={"query": "极光资料", "knowledge_base_ids": [kb_id]})
    assert {hit["document_id"] for hit in hits.json()} == set(outside.values())
    with client.app.state.db.connect() as connection:
        left = connection.execute(
            "SELECT count(*) FROM chunks WHERE document_id IN (?, ?)", tuple(inside.values())
        ).fetchone()[0]
    assert left == 0


def test_delete_is_idempotent_and_ignores_foreign_folders(client, tree):
    kb_id, ids = tree
    assert (
        client.delete(f"/api/folders/{ids['a']}?recursive=true", headers=as_user("bob")).status_code
        == 204
    )
    assert len(folders(client, kb_id)) == 5
    assert client.delete(f"/api/folders/{ids['z']}").status_code == 204
    assert client.delete(f"/api/folders/{ids['z']}").status_code == 204
    assert client.delete(f"/api/folders/{ids['z']}?recursive=true").status_code == 204
    assert client.delete("/api/folders/never-existed").status_code == 204
    assert {item["name"] for item in folders(client, kb_id)} == {"a", "b", "c", "d"}
