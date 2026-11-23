"""Offline smoke checks against real ingestion and SQLite, using isolated data."""

from io import BytesIO
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.config import Settings
from app.main import create_app


def sample_file(extension: str) -> bytes:
    text = "smokequartz retrieval retention"
    if extension == "txt":
        return text.encode()
    output = BytesIO()
    if extension == "docx":
        document = Document()
        document.add_paragraph(text)
        document.save(output)
    else:
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
        page[NameObject("/Contents")] = stream
        writer.write(output)
    return output.getvalue()


def create_kb(client: TestClient, name: str) -> str:
    response = client.post("/api/knowledge-bases", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def search(client: TestClient, ids: list[str], query: str = "smokequartz") -> list[dict]:
    response = client.post("/api/search", json={"query": query, "knowledge_base_ids": ids})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    ("extension", "kind"),
    [("txt", "txt_lines"), ("pdf", "pdf_page"), ("docx", "docx_paragraphs")],
)
def test_smoke_upload_scope_source_restart_delete(
    tmp_path: Path, extension: str, kind: str
) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, answer_mode="extractive")
    with TestClient(create_app(settings)) as client:
        a, b = create_kb(client, "冒烟 A"), create_kb(client, "冒烟 B")
        documents = []
        for kb_id, suffix in [(a, b""), (b, b"\notherlibrary")]:
            # Different content prevents cross-library deduplication hiding scope mistakes.
            payload = sample_file(extension) if kb_id == a else sample_file("txt") + suffix
            filename = f"sample.{extension}" if kb_id == a else "other.txt"
            response = client.post(
                f"/api/knowledge-bases/{kb_id}/documents", files={"file": (filename, payload)}
            )
            assert response.status_code == 202, response.text
            assert response.json()["status"] == "queued"
            document_id = response.json()["id"]
            documents.append(document_id)
            status = client.get(f"/api/documents/{document_id}")
            assert status.status_code == 200
            assert status.json()["status"] == "success"
            assert status.json()["error"] is None
        for kb_id, document_id in zip((a, b), documents, strict=True):
            hits = search(client, [kb_id])
            assert hits and {hit["document_id"] for hit in hits} == {document_id}
            assert {hit["knowledge_base_id"] for hit in hits} == {kb_id}
        assert {hit["document_id"] for hit in search(client, [a, b])} == set(documents)
        hit = search(client, [a])[0]
        assert hit["filename"] == f"sample.{extension}"
        assert hit["location"]["kind"] == kind
        assert hit["location"]["label"]
        response = client.get(f"/api/documents/{documents[0]}/chunks")
        assert response.status_code == 200
        chunk = next(chunk for chunk in response.json() if chunk["id"] == hit["chunk_id"])
        assert chunk["content"] == hit["content"]
        assert chunk["location"] == hit["location"]

    # A fresh app with the same directory checks persistence, not process/launcher behavior.
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/knowledge-bases")
        assert response.status_code == 200
        assert {kb["id"] for kb in response.json()} == {a, b}
        assert search(client, [a])[0] == hit
        document_id = documents[0]
        assert (tmp_path / "uploads" / f"{document_id}.{extension}").exists()
        assert client.delete(f"/api/documents/{document_id}").status_code == 204
        assert client.get(f"/api/documents/{document_id}").status_code == 404
        assert client.get(f"/api/documents/{document_id}/chunks").status_code == 404
        assert search(client, [a]) == []
        assert {hit["document_id"] for hit in search(client, [a, b])} == {documents[1]}
        assert not (tmp_path / "uploads" / f"{document_id}.{extension}").exists()
    with TestClient(create_app(settings)) as client:
        assert search(client, [a]) == []


def test_smoke_failed_parse_is_not_searchable(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, answer_mode="extractive")
    with TestClient(create_app(settings)) as client:
        kb_id = create_kb(client, "失败解析")
        response = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            files={"file": ("broken.pdf", b"smokequartz not a PDF")},
        )
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        document_id = response.json()["id"]
        status = client.get(f"/api/documents/{document_id}").json()
        assert status["status"] == "failed"
        assert status["error"]
        assert search(client, [kb_id]) == []
        response = client.get(f"/api/documents/{document_id}/chunks")
        assert response.status_code == 200 and response.json() == []
