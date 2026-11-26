"""OpenAI-compatible dense embeddings; no provider response bodies enter logs."""

import hashlib
import json
import math
import struct
from collections.abc import Sequence

import httpx

from app.config import Settings
from app.db import Database
from app.errors import AppError

EMBEDDING_TEXT_VERSION = "text:v2"


def embedding_text(heading_path: Sequence[str], content: str) -> str:
    """Headings give a chunk its context (a table under "合并资产负债表"); same layout as
    Docling's HybridChunker.contextualize(). U+FEFF is dropped: it carries no meaning, and some
    gateways reject any request containing it (an HTML page instead of vectors). Only the model
    input changes, so EMBEDDING_TEXT_VERSION and stored chunk text stay as they are."""
    return "\n".join([*heading_path, content]).replace("﻿", "")


def profile(settings: Settings) -> str:
    identity = [
        settings.embedding_base_url.rstrip("/"),
        settings.embedding_model,
        settings.embedding_dimensions,
        settings.embedding_revision,
        # Version of the text sent to the model (see embedding_text).
        EMBEDDING_TEXT_VERSION,
    ]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(blob: bytes, dimensions: int) -> tuple[float, ...]:
    return struct.unpack(f"<{dimensions}f", blob)


def embed(settings: Settings, texts: list[str]) -> list[list[float]]:
    if not settings.embedding_base_url or not settings.embedding_model:
        raise AppError(503, "embedding_not_configured", "请配置 Embedding 服务地址和模型")
    headers = {}
    if key := settings.embedding_api_key.get_secret_value():
        headers["Authorization"] = f"Bearer {key}"
    vectors = []
    for offset in range(0, len(texts), settings.embedding_batch_size):
        batch = texts[offset : offset + settings.embedding_batch_size]
        try:
            response = httpx.post(
                settings.embedding_base_url.rstrip("/") + "/embeddings",
                headers=headers,
                json={
                    "model": settings.embedding_model,
                    "input": batch,
                    "encoding_format": "float",
                },
                timeout=settings.embedding_timeout,
            )
            response.raise_for_status()
            data = response.json()["data"]
            if not isinstance(data, list) or len(data) != len(batch):
                raise ValueError("Invalid embedding count")
            by_index = {}
            for item in data:
                index, values = item["index"], item["embedding"]
                if type(index) is not int or index in by_index or not 0 <= index < len(batch):
                    raise ValueError("Invalid embedding index")
                if not isinstance(values, list) or len(values) != settings.embedding_dimensions:
                    raise ValueError("Invalid embedding dimensions")
                if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                    raise ValueError("Invalid embedding values")
                norm = math.hypot(*values)
                if not math.isfinite(norm) or norm == 0:
                    raise ValueError("Invalid embedding norm")
                by_index[index] = [v / norm for v in values]
            vectors.extend(by_index[i] for i in range(len(batch)))
        except httpx.TimeoutException:
            raise AppError(
                504, "embedding_timeout", "向量模型请求超时，请稍后重试", retryable=True
            ) from None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, OverflowError):
            raise AppError(
                502, "embedding_error", "向量模型调用失败或返回无效向量，请检查配置", retryable=True
            ) from None
    return vectors


def reindex(db: Database, settings: Settings, *, user_id: str) -> int:
    """Rebuild one document atomically, retaining old vectors on provider failure."""
    with db.connect() as connection:
        documents = connection.execute(
            "SELECT d.id FROM documents d JOIN knowledge_bases k ON k.id=d.knowledge_base_id "
            "WHERE k.user_id=? AND d.status='success' ORDER BY d.id",
            (user_id,),
        ).fetchall()
    count = 0
    for document in documents:
        with db.connect() as connection:
            chunks = connection.execute(
                "SELECT id, content, heading_path FROM chunks WHERE document_id=? ORDER BY ordinal",
                (document["id"],),
            ).fetchall()
        if not chunks:
            continue
        vectors = embed(
            settings,
            [embedding_text(json.loads(c["heading_path"]), c["content"]) for c in chunks],
        )
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT c.id, c.content, c.heading_path FROM chunks c "
                "JOIN documents d ON d.id=c.document_id "
                "JOIN knowledge_bases k ON k.id=d.knowledge_base_id "
                "WHERE d.id=? AND d.status='success' AND k.user_id=? ORDER BY c.ordinal",
                (document["id"], user_id),
            ).fetchall()
            if [tuple(row) for row in current] != [tuple(row) for row in chunks]:
                raise AppError(409, "embedding_index_changed", "文档在重建期间发生变化，请重试")
            connection.executemany(
                "INSERT INTO chunk_embeddings(chunk_id, profile, dimensions, vector) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(chunk_id) DO UPDATE SET "
                "profile=excluded.profile, dimensions=excluded.dimensions, vector=excluded.vector",
                [
                    (chunk["id"], profile(settings), settings.embedding_dimensions, pack(vector))
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ],
            )
        count += len(chunks)
    return count
