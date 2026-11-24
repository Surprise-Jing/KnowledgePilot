"""Real persistence and ranking, with an offline provider double at the HTTP boundary."""

import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import SCHEMA_VERSION
from app.errors import AppError
from app.main import create_app
from app.services.embeddings import embed, profile, reindex
from app.services.retrieval import lexical_scores, reciprocal_rank_fusion


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        retriever="hybrid",
        embedding_base_url="https://embedding.example/v1",
        embedding_dimensions=3,
    )


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls = []

    def respond(url: str, **kwargs) -> httpx.Response:
        assert url == "https://embedding.example/v1/embeddings"
        body = kwargs["json"]
        assert body["model"] == "bge-m3"
        assert body["encoding_format"] == "float"
        calls.append(body["input"])
        # Synonyms have aligned vectors despite sharing no lexical tokens.
        vectors = {
            "car": [1, 0, 0],
            "automobile": [1, 0, 0],
            "car insurance": [0.2, 1, 0],
            "banana": [0, 0, 1],
        }
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "data": [
                    {"index": i, "embedding": vectors[text]}
                    for i, text in reversed(list(enumerate(body["input"])))
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", respond)
    return calls


def kb(client: TestClient, name: str = "测试") -> str:
    response = client.post("/api/knowledge-bases", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def upload(client: TestClient, kb_id: str, text: str) -> str:
    response = client.post(
        f"/api/knowledge-bases/{kb_id}/documents", files={"file": ("sample.txt", text.encode())}
    )
    assert response.status_code == 202
    return response.json()["id"]


def query(client: TestClient, ids: list[str], text: str = "car", **kwargs) -> httpx.Response:
    return client.post("/api/search", json={"query": text, "knowledge_base_ids": ids}, **kwargs)


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_semantic_scope_persistence_and_delete(settings, provider, mode):
    settings.retriever = mode
    with TestClient(create_app(settings)) as client:
        a, b = kb(client, "A"), kb(client, "B")
        doc = upload(client, a, "automobile")
        other = upload(client, b, "car insurance")
        assert client.get(f"/api/documents/{doc}").json()["status"] == "success"
        response = query(client, [a])
        assert response.status_code == 200
        assert [hit["document_id"] for hit in response.json()] == [doc]
        assert response.json()[0]["location"]["kind"] == "txt_lines"
        assert {hit["document_id"] for hit in query(client, [a, b]).json()} == {doc, other}
        calls_before = len(provider)
        assert query(client, [a], headers={"X-User-Id": "other-user"}).status_code == 404
        assert len(provider) == calls_before
    with TestClient(create_app(settings)) as client:
        assert query(client, [a]).json()[0]["document_id"] == doc
        assert client.delete(f"/api/documents/{doc}").status_code == 204
        assert query(client, [a]).json() == []
        assert query(client, [b]).json()[0]["document_id"] == other
        with client.app.state.db.connect() as connection:
            assert connection.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0] == 1


def test_hybrid_uses_both_rankings(settings, provider):
    with TestClient(create_app(settings)) as client:
        a = kb(client)
        semantic = upload(client, a, "automobile")
        lexical = upload(client, a, "car insurance")
        settings.retriever = "bm25"
        assert [h["document_id"] for h in query(client, [a]).json()] == [lexical]
        settings.retriever = "vector"
        assert query(client, [a]).json()[0]["document_id"] == semantic
        settings.retriever = "hybrid"
        hits = query(client, [a]).json()
        assert [hit["document_id"] for hit in hits] == [lexical, semantic]
        assert hits[0]["score"] == round(1 / 61 + 1 / 62, 6)
        assert hits[1]["score"] == round(1 / 61, 6)


def test_legacy_index_rebuild_and_profile_change(settings, provider):
    settings.retriever = "cosine"
    with TestClient(create_app(settings)) as client:
        a = kb(client)
        doc = upload(client, a, "automobile")
        assert provider == []
        settings.retriever = "hybrid"
        response = query(client, [a])
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "embedding_index_not_ready"
        assert reindex(client.app.state.db, settings, user_id="demo") == 1
        assert query(client, [a]).json()[0]["document_id"] == doc
        settings.embedding_revision = "2"
        assert query(client, [a]).status_code == 409
        assert reindex(client.app.state.db, settings, user_id="demo") == 1
        assert query(client, [a]).status_code == 200


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_provider_timeout_fails_upload_and_search_without_fallback(settings, monkeypatch, mode):
    settings.retriever = mode

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("sensitive provider detail")

    monkeypatch.setattr(httpx, "post", timeout)
    with TestClient(create_app(settings)) as client:
        a = kb(client)
        doc = upload(client, a, "automobile")
        status = client.get(f"/api/documents/{doc}").json()
        assert status["status"] == "failed" and "超时" in status["error"]
        assert "sensitive" not in status["error"]
        with client.app.state.db.connect() as connection:
            assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0] == 0
        response = query(client, [a])
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "embedding_timeout"


def test_embedding_batches_reorders_and_normalizes(settings, provider):
    settings.embedding_batch_size = 2
    vectors = embed(settings, ["car", "car insurance", "banana"])
    assert provider == [["car", "car insurance"], ["banana"]]
    assert vectors[0] == [1, 0, 0] and vectors[2] == [0, 0, 1]
    assert sum(v * v for v in vectors[1]) == pytest.approx(1)


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {"data": [{"index": 0, "embedding": [1, 2]}]},
        {"data": [{"index": 0, "embedding": [0, 0, 0]}]},
        {"data": [{"index": 1, "embedding": [1, 0, 0]}]},
        {"data": [{"index": False, "embedding": [1, 0, 0]}]},
        {"data": [{"index": 0, "embedding": [True, 0, 0]}]},
        {"data": [{"index": 0, "embedding": [float("nan"), 0, 0]}]},
        {"data": [{"index": 0, "embedding": [float("inf"), 0, 0]}]},
    ],
)
def test_invalid_vectors_are_structured_errors(settings, monkeypatch, payload):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: httpx.Response(
            200,
            request=httpx.Request("POST", "https://embedding.example"),
            content=json.dumps(payload),
        ),
    )
    with pytest.raises(AppError) as error:
        embed(settings, ["car"])
    assert error.value.code == "embedding_error"


def test_bm25_and_rrf_math():
    scores = lexical_scores(["car", "car banana banana", "banana"], "car", bm25=True)
    assert scores[0] > scores[1] > scores[2] == 0
    scores = reciprocal_rank_fusion([[0, 1], [1, 2]])
    assert scores[1] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[1] > scores[0] > scores[2]


def test_v2_migration_preserves_chunks(settings, provider):
    settings.retriever = "cosine"
    with TestClient(create_app(settings)) as client:
        a = kb(client)
        doc = upload(client, a, "automobile")
    with sqlite3.connect(settings.data_dir / "knowledge.db") as connection:
        # Rewind to a v2 database: drop what v3 and later migrations added.
        connection.execute("DROP TABLE chunk_embeddings")
        for table, column in [
            ("tool_calls", "attempt"),
            ("tool_calls", "retry_of"),
            ("tool_calls", "fallback"),
            ("runs", "degraded"),
            ("runs", "model_retries"),
            ("documents", "parse_mode"),
            ("documents", "parser"),
            ("documents", "page_count"),
            ("chunks", "elements"),
        ]:
            connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        connection.execute("PRAGMA user_version=2")
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/documents/{doc}").json()["status"] == "success"
        assert query(client, [a], "automobile").json()[0]["document_id"] == doc
        with client.app.state.db.connect() as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_rebuild_failure_keeps_existing_index(settings, provider, monkeypatch):
    with TestClient(create_app(settings)) as client:
        a = kb(client)
        upload(client, a, "automobile")
        original_profile = profile(settings)
        settings.embedding_revision = "2"

        def fail(*args, **kwargs):
            raise httpx.ReadTimeout("timeout")

        monkeypatch.setattr(httpx, "post", fail)
        with pytest.raises(AppError):
            reindex(client.app.state.db, settings, user_id="demo")
        with client.app.state.db.connect() as connection:
            row = connection.execute("SELECT profile FROM chunk_embeddings").fetchone()
            assert row["profile"] == original_profile


def test_missing_configuration_and_http_error(settings, monkeypatch):
    settings.embedding_base_url = ""
    with pytest.raises(AppError) as error:
        embed(settings, ["car"])
    assert error.value.code == "embedding_not_configured"
    settings.embedding_base_url = "https://embedding.example/v1"
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: httpx.Response(
            401,
            request=httpx.Request("POST", settings.embedding_base_url),
            text="private provider response",
        ),
    )
    with pytest.raises(AppError) as error:
        embed(settings, ["car"])
    assert error.value.code == "embedding_error"
    assert "private" not in str(error.value)


def test_retry_after_embedding_failure(settings, provider):
    settings.embedding_base_url = ""
    with TestClient(create_app(settings)) as client:
        a = kb(client)
        doc = upload(client, a, "automobile")
        assert client.get(f"/api/documents/{doc}").json()["status"] == "failed"
        settings.embedding_base_url = "https://embedding.example/v1"
        assert client.post(f"/api/documents/{doc}/retry").status_code == 202
        assert client.get(f"/api/documents/{doc}").json()["status"] == "success"
        assert query(client, [a]).json()[0]["document_id"] == doc


def test_delete_during_embedding_does_not_resurrect_document(settings, monkeypatch):
    with TestClient(create_app(settings)) as client:
        a = kb(client)

        def delete_during_request(url, **kwargs):
            # A separate write connection proves the network call holds no write transaction.
            with client.app.state.db.connect() as connection:
                connection.execute("DELETE FROM documents WHERE knowledge_base_id=?", (a,))
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"data": [{"index": 0, "embedding": [1, 0, 0]}]},
            )

        monkeypatch.setattr(httpx, "post", delete_during_request)
        doc = upload(client, a, "automobile")
        assert client.get(f"/api/documents/{doc}").status_code == 404
        with client.app.state.db.connect() as connection:
            assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0] == 0


def test_duplicate_provider_indexes_are_rejected(settings, monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: httpx.Response(
            200,
            request=httpx.Request("POST", settings.embedding_base_url),
            json={"data": [{"index": 0, "embedding": [1, 0, 0]}] * 2},
        ),
    )
    with pytest.raises(AppError) as error:
        embed(settings, ["car", "automobile"])
    assert error.value.code == "embedding_error"


def test_query_embedding_uses_the_short_timeout(settings, provider, monkeypatch):
    """Search must time out before PK_TOOL_TIMEOUT so the tool can fall back to BM25."""
    with TestClient(create_app(settings)) as client:
        a = kb(client)
        upload(client, a, "automobile")
        settings.embedding_query_timeout = 3
        timeouts = []
        real_post = httpx.post

        def spy(*args, **kwargs):
            timeouts.append(kwargs.get("timeout"))
            return real_post(*args, **kwargs)

        monkeypatch.setattr(httpx, "post", spy)
        query(client, [a], "automobile")
    assert timeouts == [3]
