"""Document ingestion: parse with the registered Parser, embed, and store chunks with their
source elements atomically. Parsers live in app/parsers (docs/contracts.md §11)."""

import json
import logging
from concurrent.futures import Future
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.db import Database
from app.errors import AppError
from app.parsers.base import ParsedChunk, ParseError, ParseRequest
from app.parsers.registry import ParserRegistry
from app.services import artifacts
from app.services.embeddings import embed, embedding_text, pack, profile

logger = logging.getLogger(__name__)

GENERIC_FAILURE = "文档解析失败，请检查文件是否损坏或加密"


def _elements_json(chunk: ParsedChunk) -> str:
    elements = [
        {**asdict(element), "bbox": list(element.bbox) if element.bbox else None}
        for element in chunk.elements
    ]
    return json.dumps(elements, ensure_ascii=False)


def _fail(db: Database, document_id: str, exc: Exception) -> None:
    if isinstance(exc, (ParseError, AppError, ValueError, UnicodeError)):
        logger.warning("Document ingestion failed: %s (%s)", document_id, type(exc).__name__)
        reason = str(exc)
    else:
        logger.exception("Document ingestion failed: %s", document_id)
        reason = GENERIC_FAILURE
    with db.connect() as connection:
        connection.execute(
            """UPDATE documents SET status='failed', error=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
            (reason[:500], document_id),
        )


def _log_unexpected(future: Future) -> None:
    if (exc := future.exception()) is not None:
        logger.error("Queued ingestion crashed", exc_info=exc)


def schedule(db: Database, document_id: str, settings: Settings, parsers: ParserRegistry) -> None:
    """Route entry point (runs in a BackgroundTasks thread). Parsers with their own executor
    (Docling: minutes per document, one at a time) get the job queued there, so documents
    waiting their turn never hold threads of the shared request pool; light parsers run
    inline."""
    with db.connect() as connection:
        row = connection.execute(
            "SELECT file_type FROM documents WHERE id=?", (document_id,)
        ).fetchone()
    if row is None:
        return
    try:
        executor = getattr(parsers.for_type(row["file_type"]), "executor", None)
    except ParseError:
        executor = None  # ingest() records the unsupported format as a failure
    if executor is None:
        ingest(db, document_id, settings, parsers)
        return
    executor.submit(ingest, db, document_id, settings, parsers).add_done_callback(_log_unexpected)


def ingest(db: Database, document_id: str, settings: Settings, parsers: ParserRegistry) -> None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT path, file_type, parse_mode, status FROM documents WHERE id=?",
            (document_id,),
        ).fetchone()
    if row is None or row["status"] != "queued":
        return
    try:
        parser = parsers.for_type(row["file_type"])
    except ParseError as exc:
        _fail(db, document_id, exc)
        return
    root = artifacts.artifacts_root(settings)
    # Holding the parser's lock before claiming keeps waiting documents in `queued`.
    with parser.exclusive():
        with db.connect() as connection:
            claimed = connection.execute(
                """UPDATE documents SET status='parsing', error=NULL,
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'""",
                (document_id,),
            ).rowcount
        if not claimed:
            return
        staging = None
        try:
            staging = artifacts.new_staging_dir(root, document_id)
            result = parser.parse(
                ParseRequest(Path(row["path"]), row["file_type"], row["parse_mode"], staging)
            )
            if not result.chunks:
                raise ParseError("未提取到文本")
            _store(db, settings, document_id, result, staging, root)
        except Exception as exc:  # noqa: BLE001 - _fail logs unexpected errors with traceback
            if staging is not None:
                artifacts.discard(staging)
            _fail(db, document_id, exc)


def _store(db, settings, document_id, result, staging, root) -> None:
    rows = [
        (
            str(uuid4()),
            document_id,
            ordinal,
            chunk.content,
            chunk.kind,
            chunk.start,
            chunk.end,
            chunk.table,
            json.dumps(list(chunk.heading_path), ensure_ascii=False),
            chunk.char_start,
            chunk.char_end,
            _elements_json(chunk),
        )
        for ordinal, chunk in enumerate(result.chunks)
    ]
    vectors = (
        embed(settings, [embedding_text(c.heading_path, c.content) for c in result.chunks])
        if settings.retriever in {"vector", "hybrid"}
        else []
    )
    with db.connect() as connection:
        # Acquire a write lock before checking existence: deletion cannot race the insert.
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone():
            artifacts.discard(staging)
            return
        connection.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
        connection.executemany(
            """INSERT INTO chunks
            (id, document_id, ordinal, content, loc_kind, loc_start, loc_end, loc_table,
             heading_path, char_start, char_end, elements)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        if vectors:
            connection.executemany(
                "INSERT INTO chunk_embeddings(chunk_id, profile, dimensions, vector) "
                "VALUES (?, ?, ?, ?)",
                [
                    (row[0], profile(settings), settings.embedding_dimensions, pack(vector))
                    for row, vector in zip(rows, vectors, strict=True)
                ],
            )
        connection.execute(
            """UPDATE documents SET status='success', error=NULL, parser=?, page_count=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (result.parser, result.page_count, document_id),
        )
        # Swap the artifacts while the write lock is held: a delete cannot run between the
        # chunks and the directory, and a failed rename rolls the chunks back with it.
        artifacts.commit(staging, artifacts.final_dir(root, document_id))
