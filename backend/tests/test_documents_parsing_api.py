"""Parse modes, reparse, Markdown export, original file access and artifact cleanup
(docs/contracts.md §3.1 v1.7). PDF/DOCX parse with the offline legacy double (conftest)."""

import shutil
from io import BytesIO
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.config import Settings
from app.main import create_app
from tests.fakes.helpers import headers, login


def pdf_bytes(text="Knowledge retrieval"):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    data = BytesIO()
    writer.write(data)
    return data.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, frontend_dir=tmp_path / "none")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        login(client)
        yield client


@pytest.fixture
def kb_id(client):
    return client.post("/api/knowledge-bases", json={"name": "库"}).json()["id"]


def upload(client, kb_id, name, data, **form):
    response = client.post(
        f"/api/knowledge-bases/{kb_id}/documents", files={"file": (name, data)}, data=form
    )
    return response


def artifact_dir(settings, document_id):
    return settings.data_dir / "artifacts" / document_id


def test_upload_parse_mode(client, kb_id):
    document = upload(client, kb_id, "a.pdf", pdf_bytes(), parse_mode="enhanced").json()
    assert document["parse_mode"] == "enhanced"
    detail = client.get(f"/api/documents/{document['id']}").json()
    assert (detail["status"], detail["parser"], detail["page_count"]) == (
        "success",
        "legacy-test",
        1,
    )
    bad = upload(client, kb_id, "b.pdf", pdf_bytes("other"), parse_mode="bogus")
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_argument"
    text = upload(client, kb_id, "c.txt", "文本".encode(), parse_mode="enhanced").json()
    assert text["parse_mode"] == "standard"
    default = upload(client, kb_id, "d.pdf", pdf_bytes("third"), parse_mode="").json()
    assert default["parse_mode"] == "standard"


def test_reparse_flow(client, kb_id):
    document_id = upload(client, kb_id, "a.pdf", pdf_bytes()).json()["id"]
    response = client.post(f"/api/documents/{document_id}/reparse", json={"parse_mode": "enhanced"})
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    detail = client.get(f"/api/documents/{document_id}").json()
    assert (detail["status"], detail["parse_mode"]) == ("success", "enhanced")
    # An empty body keeps the current mode.
    assert client.post(f"/api/documents/{document_id}/reparse").status_code == 202
    assert client.get(f"/api/documents/{document_id}").json()["parse_mode"] == "enhanced"
    with client.app.state.db.connect() as connection:
        connection.execute("UPDATE documents SET status='parsing' WHERE id=?", (document_id,))
    busy = client.post(f"/api/documents/{document_id}/reparse", json={})
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "document_busy"
    assert busy.json()["error"]["retryable"] is True


def test_reparse_txt_stays_standard(client, kb_id):
    document_id = upload(client, kb_id, "a.txt", "文本".encode()).json()["id"]
    client.post(f"/api/documents/{document_id}/reparse", json={"parse_mode": "enhanced"})
    assert client.get(f"/api/documents/{document_id}").json()["parse_mode"] == "standard"


def test_markdown_export_chinese_filename(client, kb_id):
    document_id = upload(client, kb_id, "年报-2024.pdf", pdf_bytes()).json()["id"]
    response = client.get(f"/api/documents/{document_id}/markdown")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    # RFC 5987: the charset name is case-insensitive (Starlette writes "utf-8").
    assert f"filename*=utf-8''{quote('年报-2024.md')}" in disposition.replace("UTF-8''", "utf-8''")
    assert "Knowledge retrieval" in response.text


def test_markdown_not_available(client, settings, kb_id):
    document_id = upload(client, kb_id, "a.pdf", pdf_bytes()).json()["id"]
    shutil.rmtree(artifact_dir(settings, document_id))
    missing = client.get(f"/api/documents/{document_id}/markdown")
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "markdown_not_available"
    with client.app.state.db.connect() as connection:
        connection.execute("UPDATE documents SET status='failed' WHERE id=?", (document_id,))
    failed = client.get(f"/api/documents/{document_id}/markdown")
    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "document_not_ready"


def test_original_file_inline(client, kb_id):
    data = pdf_bytes()
    document_id = upload(client, kb_id, "原文.pdf", data).json()["id"]
    response = client.get(f"/api/documents/{document_id}/file")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.content == data
    other = client.get(f"/api/documents/{document_id}/file", headers=headers(client, "other"))
    assert other.status_code == 404


def test_delete_removes_artifacts(client, settings, kb_id):
    first = upload(client, kb_id, "a.pdf", pdf_bytes("one")).json()["id"]
    assert artifact_dir(settings, first).is_dir()
    assert client.delete(f"/api/documents/{first}").status_code == 204
    assert not artifact_dir(settings, first).exists()

    folder = client.post(f"/api/knowledge-bases/{kb_id}/folders", json={"name": "f"}).json()
    second = upload(client, kb_id, "b.pdf", pdf_bytes("two"), folder_id=folder["id"]).json()
    client.delete(f"/api/folders/{folder['id']}", params={"recursive": "true"})
    assert not artifact_dir(settings, second["id"]).exists()

    third = upload(client, kb_id, "c.pdf", pdf_bytes("three")).json()["id"]
    client.delete(f"/api/knowledge-bases/{kb_id}")
    assert not artifact_dir(settings, third).exists()


def test_startup_removes_stale_staging(settings):
    stale = settings.data_dir / "artifacts" / "x.tmp-1"
    stale.mkdir(parents=True)
    with TestClient(create_app(settings)):
        assert not stale.exists()
