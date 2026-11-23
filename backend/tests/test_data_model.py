import sqlite3
from io import BytesIO

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import SCHEMA_VERSION, UPGRADE_ERROR
from app.main import create_app
from app.services.ingestion import MAX_CHUNK_CHARS, SPLIT_OVERLAP


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, answer_mode="extractive")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def as_user(user_id):
    return {"X-User-Id": user_id}


def kb(client, user="demo", name="测试库"):
    response = client.post("/api/knowledge-bases", json={"name": name}, headers=as_user(user))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client, kb_id, name, data, user="demo"):
    response = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        files={"file": (name, data)},
        headers=as_user(user),
    )
    assert response.status_code == 202, response.text
    return response.json()


def chunks(client, document_id, user="demo"):
    response = client.get(f"/api/documents/{document_id}/chunks", headers=as_user(user))
    assert response.status_code == 200, response.text
    return response.json()


def test_users_cannot_see_each_others_data(client):
    alice_kb = kb(client, "alice")
    document = upload(client, alice_kb, "a.txt", "爱丽丝的私有笔记".encode(), user="alice")
    bob_kb = kb(client, "bob")

    assert [
        item["id"] for item in client.get("/api/knowledge-bases", headers=as_user("bob")).json()
    ] == [bob_kb]
    for method, url in [
        ("get", f"/api/knowledge-bases/{alice_kb}/documents"),
        ("get", f"/api/documents/{document['id']}"),
        ("get", f"/api/documents/{document['id']}/chunks"),
        ("post", f"/api/documents/{document['id']}/retry"),
        ("delete", f"/api/documents/{document['id']}"),
    ]:
        response = client.request(method, url, headers=as_user("bob"))
        assert response.status_code == 404, (method, url)
    search = {"query": "私有笔记", "knowledge_base_ids": [alice_kb]}
    assert client.post("/api/search", json=search, headers=as_user("bob")).status_code == 404
    # Mixing an owned and a foreign knowledge base is rejected rather than silently filtered.
    search["knowledge_base_ids"] = [bob_kb, alice_kb]
    assert client.post("/api/search", json=search, headers=as_user("bob")).status_code == 404
    # The owner still sees the document, so the 404s above came from the ownership check.
    assert (
        client.get(f"/api/documents/{document['id']}", headers=as_user("alice")).status_code == 200
    )


def test_missing_header_uses_default_user_and_invalid_header_is_rejected(client):
    kb_id = kb(client, "demo")
    assert [item["id"] for item in client.get("/api/knowledge-bases").json()] == [kb_id]
    response = client.get("/api/knowledge-bases", headers=as_user("not valid!"))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_user_id"


def test_document_metadata(client):
    kb_id = kb(client)
    document = upload(client, kb_id, "notes.TXT", b"hello")
    assert document["file_type"] == "txt"
    assert document["size_bytes"] == 5
    assert document["folder_id"] is None
    assert document["updated_at"]


def test_txt_lines_merge_into_chunks_with_line_range(client):
    kb_id = kb(client)
    text = "第一行内容\n\n第二行内容\n第三行内容\n"
    document = upload(client, kb_id, "lines.txt", text.encode())
    [chunk] = chunks(client, document["id"])
    assert chunk["content"] == "第一行内容\n第二行内容\n第三行内容"
    assert chunk["location"]["kind"] == "txt_lines"
    assert (chunk["location"]["start"], chunk["location"]["end"]) == (1, 4)
    assert chunk["location"]["label"] == "第 1–4 行"


def test_long_line_is_split_with_overlap_and_char_range(client):
    kb_id = kb(client)
    line = "".join(str(i % 10) for i in range(MAX_CHUNK_CHARS + 300))
    document = upload(client, kb_id, "long.txt", f"短行\n{line}\n".encode())
    result = chunks(client, document["id"])
    assert result[0]["content"] == "短行"
    pieces = result[1:]
    assert len(pieces) == 2
    first, second = (piece["location"] for piece in pieces)
    assert (first["start"], first["char_start"], first["char_end"]) == (2, 0, MAX_CHUNK_CHARS)
    assert second["char_start"] == MAX_CHUNK_CHARS - SPLIT_OVERLAP
    assert second["char_end"] == len(line)
    assert first["label"] == f"第 2 行，字符 1–{MAX_CHUNK_CHARS}"
    assert "".join([pieces[0]["content"], pieces[1]["content"][SPLIT_OVERLAP:]]) == line


def test_gbk_text_is_decoded(client):
    kb_id = kb(client)
    document = upload(client, kb_id, "gbk.txt", "中文编码测试".encode("gbk"))
    status = client.get(f"/api/documents/{document['id']}").json()
    assert status["status"] == "success", status
    assert chunks(client, document["id"])[0]["content"] == "中文编码测试"


def test_docx_chunks_follow_heading_sections(client):
    kb_id = kb(client)
    doc = Document()
    doc.add_paragraph("前言段落")
    doc.add_heading("第一章", level=1)
    doc.add_paragraph("第一章正文")
    doc.add_heading("1.1 小节", level=2)
    doc.add_paragraph("小节正文一")
    doc.add_paragraph("小节正文二")
    doc.add_heading("第二章", level=1)
    doc.add_paragraph("第二章正文")
    data = BytesIO()
    doc.save(data)
    document = upload(client, kb_id, "book.docx", data.getvalue())
    result = [(c["content"], c["location"]) for c in chunks(client, document["id"])]
    assert [content for content, _ in result] == [
        "前言段落",
        "第一章\n第一章正文",
        "1.1 小节\n小节正文一\n小节正文二",
        "第二章\n第二章正文",
    ]
    _, section = result[2]
    assert section["heading_path"] == ["第一章", "1.1 小节"]
    assert section["label"] == "第一章 › 1.1 小节 › 第 4–6 段"
    assert result[0][1]["heading_path"] == []


def test_fresh_database_is_at_current_schema_version(settings):
    with TestClient(create_app(settings)):
        pass
    connection = sqlite3.connect(settings.data_dir / "knowledge.db")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "users",
        "folders",
        "conversations",
        "messages",
        "runs",
        "tool_calls",
        "citations",
    } <= tables


def test_newer_database_version_is_refused(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.data_dir / "knowledge.db")
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    connection.close()
    with pytest.raises(RuntimeError, match="高于程序支持的版本"), TestClient(create_app(settings)):
        pass


def test_day_one_database_is_migrated(settings):
    """A database created by the day-one prototype keeps its knowledge bases and files."""
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True)
    kept = upload_dir / "doc-1.txt"
    kept.write_text("迁移后需要重新解析的内容", encoding="utf-8")
    connection = sqlite3.connect(settings.data_dir / "knowledge.db")
    connection.executescript(f"""
        CREATE TABLE knowledge_bases (id TEXT PRIMARY KEY, name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE documents (id TEXT PRIMARY KEY,
            knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id),
            filename TEXT NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL,
            status TEXT NOT NULL, error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(knowledge_base_id, sha256));
        CREATE TABLE chunks (id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL, content TEXT NOT NULL, location TEXT NOT NULL);
        CREATE INDEX idx_documents_kb ON documents(knowledge_base_id);
        INSERT INTO knowledge_bases(id, name) VALUES ('kb-1', '旧知识库');
        INSERT INTO documents(id, knowledge_base_id, filename, sha256, path, status)
            VALUES ('doc-1', 'kb-1', '旧文档.txt', 'abc', '{kept}', 'success'),
                   ('doc-2', 'kb-1', '丢失.txt', 'def', '{upload_dir / "gone.txt"}', 'success');
        INSERT INTO chunks VALUES ('c-1', 'doc-1', 0, '旧片段', '第 1 行');
    """)
    connection.close()

    with TestClient(create_app(settings)) as client:
        assert [item["name"] for item in client.get("/api/knowledge-bases").json()] == ["旧知识库"]
        assert client.get("/api/knowledge-bases", headers=as_user("someone")).json() == []
        documents = {d["id"]: d for d in client.get("/api/knowledge-bases/kb-1/documents").json()}
        assert documents["doc-1"]["status"] == "failed"
        assert documents["doc-1"]["error"] == UPGRADE_ERROR
        assert documents["doc-1"]["size_bytes"] == kept.stat().st_size
        assert documents["doc-2"]["error"] == "原文件缺失，请删除后重新上传"
        assert chunks(client, "doc-1") == []

        assert client.post("/api/documents/doc-1/retry").status_code == 202
        assert client.get("/api/documents/doc-1").json()["status"] == "success"
        assert chunks(client, "doc-1")[0]["location"]["label"] == "第 1 行"

    connection = sqlite3.connect(settings.data_dir / "knowledge.db")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert not any(name.startswith("legacy_") for name in names)


def test_interrupted_runs_are_closed_on_restart(settings):
    db = create_app(settings).state.db
    with TestClient(create_app(settings)), db.connect() as connection:
        connection.execute("INSERT INTO users(id, name) VALUES ('u', 'u')")
        connection.execute("INSERT INTO conversations(id, user_id, title) VALUES ('c', 'u', 't')")
        connection.execute(
            "INSERT INTO messages(id, conversation_id, role, content, status) "
            "VALUES ('m1', 'c', 'user', '问题', 'completed'), "
            "('m2', 'c', 'assistant', '', 'streaming')"
        )
        connection.execute(
            "INSERT INTO runs(id, conversation_id, user_message_id, assistant_message_id, "
            "knowledge_base_ids, status) VALUES ('r', 'c', 'm1', 'm2', '[]', 'running')"
        )
    with TestClient(create_app(settings)), db.connect() as connection:
        run = connection.execute("SELECT * FROM runs WHERE id='r'").fetchone()
        message = connection.execute("SELECT * FROM messages WHERE id='m2'").fetchone()
    assert (run["status"], run["stop_reason"]) == ("failed", "interrupted")
    assert run["finished_at"]
    assert message["status"] == "failed"


def test_knowledge_base_delete_cascades(client):
    kb_id = kb(client)
    document = upload(client, kb_id, "a.txt", b"cascade")
    with client.app.state.db.connect() as connection:
        connection.execute(
            "INSERT INTO folders(id, knowledge_base_id, name) VALUES ('f', ?, 'dir')", (kb_id,)
        )
        connection.execute("DELETE FROM knowledge_bases WHERE id=?", (kb_id,))
        for table in ("folders", "documents", "chunks"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert client.get(f"/api/documents/{document['id']}").status_code == 404


def test_sibling_folder_names_are_unique(client):
    kb_id = kb(client)
    with client.app.state.db.connect() as connection:
        connection.execute(
            "INSERT INTO folders(id, knowledge_base_id, name) VALUES ('a', ?, 'x')", (kb_id,)
        )
        connection.execute(
            "INSERT INTO folders(id, knowledge_base_id, parent_id, name) VALUES ('b', ?, 'a', 'x')",
            (kb_id,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO folders(id, knowledge_base_id, name) VALUES ('c', ?, 'x')", (kb_id,)
            )
