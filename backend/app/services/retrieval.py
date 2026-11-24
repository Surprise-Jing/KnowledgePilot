import json
import math
import re
from collections import Counter

from app.config import Settings
from app.db import Database
from app.errors import AppError
from app.schemas import Location, SearchHit, SearchRequest
from app.services.access import require_knowledge_bases
from app.services.embeddings import embed, embedding_text, profile, unpack


def tokens(text: str) -> Counter:
    words = re.findall(r"[a-z0-9_]+|[㐀-鿿]+", text.lower())
    result = []
    for word in words:
        if re.fullmatch(r"[㐀-鿿]+", word):
            result.extend(word)
            result.extend(word[i : i + 2] for i in range(len(word) - 1))
        else:
            result.append(word)
    return Counter(result)


def lexical_scores(contents: list[str], query: str, *, bm25: bool) -> list[float]:
    terms = [tokens(content) for content in contents]
    query_terms = tokens(query)
    if not bm25:
        query_norm = math.sqrt(sum(n * n for n in query_terms.values()))
        return [
            sum(n * counts[t] for t, n in query_terms.items())
            / (query_norm * math.sqrt(sum(n * n for n in counts.values())))
            if query_norm and counts
            else 0.0
            for counts in terms
        ]
    lengths = [sum(counts.values()) for counts in terms]
    average = sum(lengths) / len(lengths) if lengths else 0
    frequency = Counter(term for counts in terms for term in counts)
    scores = []
    for counts, length in zip(terms, lengths, strict=True):
        score = 0.0
        for term in query_terms:
            tf = counts[term]
            if tf:
                idf = math.log(1 + (len(terms) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * length / average))
        scores.append(score)
    return scores


def reciprocal_rank_fusion(rankings: list[list[int]]) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, index in enumerate(ranking, 1):
            scores[index] = scores.get(index, 0.0) + 1 / (60 + rank)
    return scores


def search(
    db: Database, user_id: str, request: SearchRequest, settings: Settings | None = None
) -> list[SearchHit]:
    settings = settings or Settings()
    vector_mode = settings.retriever in {"vector", "hybrid"}
    # Validate scope before transmitting the query to an external embedding service.
    with db.connect() as connection:
        ids = require_knowledge_bases(connection, user_id, request.knowledge_base_ids)
    query_settings = settings.model_copy(
        update={
            "embedding_timeout": min(settings.embedding_timeout, settings.embedding_query_timeout)
        }
    )
    query_vector = embed(query_settings, [request.query])[0] if vector_mode else None
    with db.connect() as connection:
        # Revalidate after the network call; no DB transaction spans provider latency.
        ids = require_knowledge_bases(connection, user_id, ids)
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""
            SELECT c.id AS chunk_id, c.document_id, d.knowledge_base_id, d.filename, c.content,
                   c.loc_kind, c.loc_start, c.loc_end, c.loc_table, c.heading_path,
                   c.char_start, c.char_end, c.elements, e.profile, e.dimensions, e.vector
            FROM chunks c JOIN documents d ON d.id=c.document_id
            LEFT JOIN chunk_embeddings e ON e.chunk_id=c.id
            WHERE d.knowledge_base_id IN ({placeholders}) AND d.status='success'
            ORDER BY c.id
        """,
            ids,
        ).fetchall()
    if not rows:
        return []
    dense = []
    if vector_mode:
        if any(
            row["profile"] != profile(settings)
            or row["dimensions"] != settings.embedding_dimensions
            for row in rows
        ):
            raise AppError(409, "embedding_index_not_ready", "向量索引缺失或配置已变化，请重建索引")
        for row in rows:
            vector = unpack(row["vector"], settings.embedding_dimensions)
            dense.append(sum(a * b for a, b in zip(query_vector, vector, strict=True)))
    lexical = (
        lexical_scores(
            # Same text the vectors were built from: headings count as matches too.
            [embedding_text(json.loads(row["heading_path"]), row["content"]) for row in rows],
            request.query,
            bm25=settings.retriever != "cosine",
        )
        if settings.retriever != "vector"
        else []
    )

    def ranked(scores: list[float]) -> list[int]:
        return sorted(
            (i for i, score in enumerate(scores) if score > 0),
            key=lambda i: (-scores[i], rows[i]["chunk_id"]),
        )

    if settings.retriever == "hybrid":
        limit = max(request.top_k, settings.retrieval_candidates)
        fused = reciprocal_rank_fusion([ranked(lexical)[:limit], ranked(dense)[:limit]])
        scores = [fused.get(i, 0.0) for i in range(len(rows))]
    else:
        scores = dense if vector_mode else lexical
    hits = []
    seen = set()
    for i in ranked(scores):
        row = rows[i]
        if row["content"] not in seen:
            seen.add(row["content"])
            hits.append(
                SearchHit(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    knowledge_base_id=row["knowledge_base_id"],
                    filename=row["filename"],
                    content=row["content"],
                    location=Location.from_row(row),
                    score=round(scores[i], 6),
                )
            )
        if len(hits) >= request.top_k:
            break
    return hits
