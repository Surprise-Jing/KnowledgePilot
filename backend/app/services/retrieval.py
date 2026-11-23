import math
import re
from collections import Counter

from app.db import Database
from app.schemas import Location, SearchHit, SearchRequest
from app.services.access import require_knowledge_bases


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


def search(db: Database, user_id: str, request: SearchRequest) -> list[SearchHit]:
    with db.connect() as connection:
        ids = require_knowledge_bases(connection, user_id, request.knowledge_base_ids)
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""
            SELECT c.id AS chunk_id, c.document_id, d.knowledge_base_id, d.filename, c.content,
                   c.loc_kind, c.loc_start, c.loc_end, c.loc_table, c.heading_path,
                   c.char_start, c.char_end
            FROM chunks c JOIN documents d ON d.id=c.document_id
            WHERE d.knowledge_base_id IN ({placeholders}) AND d.status='success'
            ORDER BY c.id
        """,
            ids,
        ).fetchall()
    query = tokens(request.query)
    query_norm = math.sqrt(sum(n * n for n in query.values()))
    hits = []
    if not query_norm:
        return hits
    for row in rows:
        terms = tokens(row["content"])
        norm = math.sqrt(sum(n * n for n in terms.values()))
        score = sum(n * terms[t] for t, n in query.items()) / (query_norm * norm) if norm else 0
        if score > 0:
            hits.append(
                SearchHit(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    knowledge_base_id=row["knowledge_base_id"],
                    filename=row["filename"],
                    content=row["content"],
                    location=Location.from_row(row),
                    score=round(score, 6),
                )
            )
    hits.sort(key=lambda hit: (-hit.score, hit.chunk_id))
    unique = {}
    for hit in hits:
        unique.setdefault(hit.content, hit)
    return list(unique.values())[: request.top_k]
