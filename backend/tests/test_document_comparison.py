"""Document-scoped comparison retrieval and multi-source citation integrity, offline."""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tools.context import ToolContext
from app.tools.registry import execute
from tests.fakes.helpers import (
    ask,
    headers,
    install_embeddings,
    install_llm,
    login,
    make_conversation,
    make_kb,
    make_settings,
    read_sse,
    upload,
)
from tests.fakes.llm import ScriptedLLM, text, tool_call


@pytest.fixture
def corpus(tmp_path):
    settings = make_settings(tmp_path, retriever="bm25")
    with TestClient(create_app(settings)) as client:
        login(client)
        kb = make_kb(client)
        other_kb = make_kb(client, "另一个库")
        other_user_kb = make_kb(client, "其他用户", user="bob")
        a = upload(client, kb, "方案A.txt", "检索采用完全复制法。相同结论仍需保留来源。")
        # Different original bytes (UTF-8 BOM), identical parsed text: upload deduplication
        # must not collapse this fixture before retrieval can exercise source preservation.
        b = upload(client, kb, "方案B.txt", "\ufeff检索采用完全复制法。相同结论仍需保留来源。")
        c = upload(client, other_kb, "范围外.txt", "检索采用完全复制法。")
        d = upload(client, other_user_kb, "他人资料.txt", "检索采用完全复制法。", user="bob")
        yield client, settings, kb, other_kb, a, b, c, d


def query(client, kb, document_ids, top_k=5):
    return client.post(
        "/api/search",
        json={
            "query": "检索",
            "knowledge_base_ids": [kb],
            "document_ids": document_ids,
            "top_k": top_k,
        },
    )


def context(client, settings, kb):
    return ToolContext(
        db=client.app.state.db, settings=settings, user_id="demo", allowed_kb_ids=[kb]
    )


def test_identical_text_keeps_both_document_sources(corpus):
    client, settings, kb, _, a, b, *_ = corpus
    ctx = context(client, settings, kb)
    result = execute(ctx, "search_chunks", {"query": "检索", "document_ids": [a, b]})
    assert result.ok
    hits = result.data["hits"]
    assert [hit["document_id"] for hit in hits] == [a, b]
    assert hits[0]["content"] == hits[1]["content"]
    assert hits[0]["marker"] != hits[1]["marker"]
    assert result.data["document_coverage"] == [
        {"document_id": a, "hit_count": 1},
        {"document_id": b, "hit_count": 1},
    ]
    again = execute(ctx, "search_chunks", {"query": "复制法", "document_ids": [a, b]})
    assert [hit["marker"] for hit in again.data["hits"]] == [hit["marker"] for hit in hits]


def test_global_search_also_preserves_cross_document_sources(corpus):
    client, _, kb, _, a, b, *_ = corpus
    response = client.post("/api/search", json={"query": "检索", "knowledge_base_ids": [kb]})
    assert {hit["document_id"] for hit in response.json()} == {a, b}


def test_document_filter_is_applied_before_top_k_and_duplicate_ids_are_safe(corpus):
    client, _, kb, _, a, b, *_ = corpus
    assert [hit["document_id"] for hit in query(client, kb, [b, b], 1).json()] == [b]
    assert [hit["document_id"] for hit in query(client, kb, [a], 1).json()] == [a]


def test_balanced_retrieval_returns_each_target_before_adding_depth(corpus):
    client, _, kb, _, a, b, *_ = corpus
    # Extra high-relevance chunks in A must not use up B's comparison slot.
    with client.app.state.db.connect() as connection:
        original = connection.execute("SELECT * FROM chunks WHERE document_id=?", (a,)).fetchone()
        for ordinal in range(1, 6):
            connection.execute(
                """INSERT INTO chunks(id, document_id, ordinal, content, loc_kind, loc_start,
                       loc_end, heading_path) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    f"comparison-{ordinal}",
                    a,
                    ordinal,
                    "检索" * (ordinal + 1),
                    original["loc_kind"],
                    ordinal + 1,
                    ordinal + 1,
                    "[]",
                ),
            )
    hits = query(client, kb, [a, b], 3).json()
    assert [hit["document_id"] for hit in hits] == [a, b, a]
    assert len(query(client, kb, [a, b], 1).json()) == 1


@pytest.mark.parametrize(
    ("target", "code", "status"),
    [("outside", "out_of_scope", 403), ("foreign", "document_not_found", 404)],
)
def test_scope_errors_happen_before_embedding(corpus, monkeypatch, target, code, status):
    from app.services import retrieval

    client, settings, kb, _, _, _, outside, foreign = corpus
    settings.retriever = "vector"

    def forbidden(*args, **kwargs):
        pytest.fail("越权请求不得发送查询到Embedding服务")

    monkeypatch.setattr(retrieval, "embed", forbidden)
    doc = outside if target == "outside" else foreign
    response = query(client, kb, [doc])
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    result = execute(
        context(client, settings, kb), "search_chunks", {"query": "检索", "document_ids": [doc]}
    )
    assert not result.ok and result.error.code == code


def test_empty_document_list_is_invalid(corpus):
    client, settings, kb, *_ = corpus
    assert query(client, kb, []).status_code == 422
    result = execute(
        context(client, settings, kb), "search_chunks", {"query": "检索", "document_ids": []}
    )
    assert not result.ok and result.error.code == "invalid_argument"


def test_vector_scoped_search_ignores_unselected_unindexed_documents(tmp_path, monkeypatch):
    settings = make_settings(
        tmp_path,
        retriever="hybrid",
        embedding_base_url="https://embedding.example/v1",
        embedding_dimensions=3,
    )
    install_embeddings(monkeypatch)
    with TestClient(create_app(settings)) as client:
        login(client)
        kb = make_kb(client)
        a = upload(client, kb, "a.txt", "原文A")
        b = upload(client, kb, "b.txt", "原文B")
        with client.app.state.db.connect() as connection:
            connection.execute(
                "DELETE FROM chunk_embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id=?)",
                (a,),
            )
        settings.retriever = "vector"
        assert query(client, kb, [b]).status_code == 200
        assert query(client, kb, [a, b]).json()["error"]["code"] == "embedding_index_not_ready"


def test_document_is_revalidated_after_embedding(corpus, monkeypatch):
    from app.services import retrieval

    client, settings, kb, _, a, *_ = corpus
    settings.retriever = "vector"

    def delete_after_validation(*args):
        client.delete(f"/api/documents/{a}")
        return [[1, 0, 0]]

    monkeypatch.setattr(retrieval, "embed", delete_after_validation)
    assert query(client, kb, [a]).json()["error"]["code"] == "document_not_found"


def test_hybrid_candidate_cutoff_preserves_a_lower_ranked_target(tmp_path, monkeypatch):
    import struct

    from app.services import retrieval

    settings = make_settings(
        tmp_path,
        retriever="hybrid",
        embedding_base_url="https://embedding.example/v1",
        embedding_dimensions=3,
        retrieval_candidates=10,
    )
    install_embeddings(monkeypatch)
    with TestClient(create_app(settings)) as client:
        login(client)
        kb = make_kb(client)
        a = upload(client, kb, "a.txt", "检索A")
        b = upload(client, kb, "b.txt", "检索B")
        with client.app.state.db.connect() as connection:
            embedding = connection.execute("SELECT * FROM chunk_embeddings LIMIT 1").fetchone()
            connection.execute(
                "UPDATE chunk_embeddings SET vector=?",
                (struct.pack("<3f", 1, 0, 0),),
            )
            connection.execute(
                """UPDATE chunk_embeddings SET vector=? WHERE chunk_id IN
                   (SELECT id FROM chunks WHERE document_id=?)""",
                (struct.pack("<3f", 0.1, 0.99, 0), b),
            )
            for ordinal in range(1, 13):
                chunk_id = f"hybrid-comparison-{ordinal}"
                connection.execute(
                    """INSERT INTO chunks(id, document_id, ordinal, content, loc_kind,
                           loc_start, loc_end, heading_path) VALUES(?,?,?,?,?,?,?,?)""",
                    (chunk_id, a, ordinal, f"检索A{ordinal}", "txt_lines", ordinal, ordinal, "[]"),
                )
                connection.execute(
                    "INSERT INTO chunk_embeddings VALUES(?,?,?,?)",
                    (chunk_id, embedding["profile"], 3, struct.pack("<3f", 1, 0, 0)),
                )
        monkeypatch.setattr(retrieval, "embed", lambda *args: [[1, 0, 0]])
        monkeypatch.setattr(
            retrieval,
            "lexical_scores",
            lambda contents, *args, **kwargs: [10 if "A" in content else 1 for content in contents],
        )
        hits = query(client, kb, [a, b], 2).json()
        assert [hit["document_id"] for hit in hits] == [a, b]
        assert hits[1]["score"] > 0


def test_empty_allowed_scope_cannot_be_bypassed_with_document_ids(corpus):
    client, settings, _, _, a, *_ = corpus
    ctx = ToolContext(db=client.app.state.db, settings=settings, user_id="demo", allowed_kb_ids=[])
    result = execute(ctx, "search_chunks", {"query": "检索", "document_ids": [a]})
    assert not result.ok and result.error.code == "out_of_scope"


def test_empty_target_is_explicitly_uncovered_not_claimed_absent(corpus):
    client, settings, kb, _, a, *_ = corpus
    b = upload(client, kb, "无相关内容.txt", "香蕉苹果")
    result = execute(
        context(client, settings, kb), "search_chunks", {"query": "检索", "document_ids": [a, b]}
    )
    assert result.ok
    assert result.data["document_coverage"][1] == {"document_id": b, "hit_count": 0}
    assert "不代表文档未提及" in result.data["coverage_note"]


def test_embedding_fallback_retains_document_filter(tmp_path, monkeypatch):
    from app.errors import AppError
    from app.services import retrieval

    settings = make_settings(tmp_path, retriever="bm25")
    with TestClient(create_app(settings)) as client:
        login(client)
        kb = make_kb(client)
        upload(client, kb, "a.txt", "检索原文A")
        b = upload(client, kb, "b.txt", "检索原文B")
        settings.retriever = "hybrid"

        def failure(*args):
            raise AppError(504, "embedding_timeout", "向量超时", retryable=True)

        monkeypatch.setattr(retrieval, "embed", failure)
        result = execute(
            context(client, settings, kb), "search_chunks", {"query": "检索", "document_ids": [b]}
        )
        assert result.ok and result.data["degraded"]["kind"] == "bm25"
        assert {hit["document_id"] for hit in result.data["hits"]} == {b}


def test_two_report_comparison_citations_survive_history_reload(corpus):
    client, _, kb, _, a, b, *_ = corpus
    llm = ScriptedLLM(
        [
            [tool_call("search_chunks", {"query": "检索", "document_ids": [a, b]})],
            text("两份方案均采用完全复制法。[c1][c2][c1]"),
        ]
    )
    install_llm(client, llm)
    conversation = make_conversation(client)
    events = read_sse(ask(client, conversation, "比较两份方案的检索方法", [kb]))
    citations = [payload for name, payload in events if name == "citation"]
    assert [item["marker"] for item in citations] == ["c1", "c2"]
    assert {item["document_id"] for item in citations} == {a, b}
    messages = client.get(f"/api/conversations/{conversation}", headers=headers(client)).json()[
        "messages"
    ]
    assert messages[-1]["citations"] == citations
    assert json.loads(llm.messages(1)[-1]["content"])["data"]["document_coverage"]
    assert "文档×维度" in llm.messages(0)[0]["content"]
