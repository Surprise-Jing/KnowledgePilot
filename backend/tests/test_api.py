from io import BytesIO

import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.config import Settings
from app.main import create_app
from tests.fakes.helpers import login


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, answer_mode="extractive")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        login(client)
        yield client


def kb(client, name="测试库"):
    response = client.post("/api/knowledge-bases", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


DEFAULT_CONTENT = "删除文档时同步清理索引。".encode()


def upload(client, kb_id, name="test.txt", data=DEFAULT_CONTENT):
    response = client.post(f"/api/knowledge-bases/{kb_id}/documents", files={"file": (name, data)})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def query(kb_id, text="删除文档"):
    return {"query": text, "knowledge_base_ids": [kb_id]}


def test_upload_search_answer_delete_and_dedup(client):
    a, b = kb(client), kb(client, "另一个库")
    document_id = upload(client, a)
    assert upload(client, a, name="duplicate.txt") == document_id
    assert client.get(f"/api/documents/{document_id}").json()["status"] == "success"
    hits = client.post("/api/search", json=query(a)).json()
    assert hits[0]["document_id"] == document_id
    assert hits[0]["location"]["label"] == "第 1 行"
    assert client.post("/api/search", json=query(b)).json() == []
    result = client.post("/api/chat", json=query(a)).json()
    assert "[1]" in result["answer"]
    assert result["mode"] == "extractive"
    assert result["citations"][0]["chunk_id"] == hits[0]["chunk_id"]
    assert "content" in client.get(f"/api/documents/{document_id}/chunks").json()[0]
    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert client.post("/api/search", json=query(a)).json() == []
    assert client.post("/api/chat", json=query(a)).json()["citations"] == []


def test_invalid_upload_and_retry(client):
    a = kb(client)
    response = client.post(f"/api/knowledge-bases/{a}/documents", files={"file": ("a.exe", b"x")})
    assert response.status_code == 415
    document_id = upload(client, a, "broken.pdf", b"not a PDF")
    status = client.get(f"/api/documents/{document_id}").json()
    assert status["status"] == "failed" and status["error"]
    assert client.post(f"/api/documents/{document_id}/retry").status_code == 202
    assert client.get(f"/api/documents/{document_id}").json()["status"] == "failed"
    good = upload(client, a)
    assert client.post(f"/api/documents/{good}/retry").status_code == 409
    assert client.post("/api/search", json=query("missing")).status_code == 404
    assert client.post("/api/chat", json=query(a, "  ")).status_code == 422


def test_docx_and_pdf(client):
    a = kb(client)
    doc = Document()
    doc.add_paragraph("知识库支持文档解析。")
    doc.add_table(rows=1, cols=1).cell(0, 0).text = "表格也可检索"
    data = BytesIO()
    doc.save(data)
    document_id = upload(client, a, "sample.docx", data.getvalue())
    chunks = client.get(f"/api/documents/{document_id}/chunks").json()
    assert len(chunks) == 2
    assert chunks[1]["location"]["label"] == "表 1 第 1 行"
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
    stream.set_data(b"BT /F1 12 Tf 20 200 Td (Knowledge retrieval) Tj ET")
    page[NameObject("/Contents")] = stream
    data = BytesIO()
    writer.write(data)
    pdf_id = upload(client, a, "sample.pdf", data.getvalue())
    assert client.get(f"/api/documents/{pdf_id}").json()["status"] == "success"
    hits = client.post("/api/search", json=query(a, "retrieval")).json()
    assert hits[0]["document_id"] == pdf_id
    assert hits[0]["location"]["label"] == "第 1 页"


def test_persistence_and_interrupted_job(settings):
    with TestClient(create_app(settings)) as client:
        login(client)
        a = kb(client)
        document_id = upload(client, a)
        broken = upload(client, a, "broken.pdf", b"broken")
        with client.app.state.db.connect() as connection:
            connection.execute("UPDATE documents SET status='parsing' WHERE id=?", (broken,))
    with TestClient(create_app(settings)) as client:
        login(client)
        assert client.post("/api/search", json=query(a)).json()[0]["document_id"] == document_id
        status = client.get(f"/api/documents/{broken}").json()
        assert status["status"] == "failed" and "中断" in status["error"]


def test_model_adapter_and_failures(settings, monkeypatch):
    settings.answer_mode = "openai"
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("test-key")
    with TestClient(create_app(settings)) as client:
        login(client)
        a = kb(client)
        upload(client, a)

        def model_response(*args, **kwargs):
            assert "参考资料" in kwargs["json"]["messages"][1]["content"]
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "同步清理索引。[1]"}}]},
                request=httpx.Request("POST", "https://example.com"),
            )

        monkeypatch.setattr(httpx, "post", model_response)
        assert client.post("/api/chat", json=query(a)).json()["mode"] == "openai"

        def timeout(*args, **kwargs):
            raise httpx.ReadTimeout("timeout")

        monkeypatch.setattr(httpx, "post", timeout)
        response = client.post("/api/chat", json=query(a))
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "model_timeout"


def test_size_limit(settings):
    settings.max_upload_bytes = 4
    with TestClient(create_app(settings)) as client:
        login(client)
        a = kb(client)
        response = client.post(
            f"/api/knowledge-bases/{a}/documents", files={"file": ("a.txt", b"12345")}
        )
        assert response.status_code == 413
