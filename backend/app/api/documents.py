"""Document upload, status, chunks, retry and delete routes. Owner: A."""

import hashlib
import sqlite3
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, UploadFile

from app.deps import DB, AppSettings, UploadDir, User
from app.errors import AppError
from app.schemas import ChunkOut, DocumentOut, Location
from app.services.access import require_document, require_knowledge_base
from app.services.ingestion import ingest

SUPPORTED_SUFFIXES = {".txt", ".pdf", ".docx"}

router = APIRouter(tags=["documents"])


def _get_document(db, user_id, document_id):
    with db.connect() as connection:
        return require_document(connection, user_id, document_id)


@router.post("/api/knowledge-bases/{kb_id}/documents", response_model=DocumentOut, status_code=202)
def upload_document(
    kb_id: str,
    user_id: User,
    db: DB,
    settings: AppSettings,
    upload_dir: UploadDir,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
):
    with db.connect() as connection:
        require_knowledge_base(connection, user_id, kb_id)
    filename = Path((file.filename or "").replace("\\", "/")).name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise AppError(415, "unsupported_format", "仅支持 TXT（UTF-8 或 GBK）、PDF、DOCX")
    content = file.file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise AppError(413, "file_too_large", "文件超过上传大小限制")
    if not content:
        raise AppError(400, "empty_file", "不能上传空文件")
    digest = hashlib.sha256(content).hexdigest()

    def existing():
        with db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE knowledge_base_id=? AND sha256=?",
                (kb_id, digest),
            ).fetchone()
        return dict(row) if row else None

    if found := existing():
        return found
    document_id = str(uuid4())
    path = (upload_dir / f"{document_id}{suffix}").resolve()
    path.write_bytes(content)
    try:
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO documents
                (id, knowledge_base_id, filename, file_type, size_bytes, sha256, path, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued')""",
                (document_id, kb_id, filename, suffix[1:], len(content), digest, str(path)),
            )
    except sqlite3.IntegrityError:
        # A concurrent upload of the same content won the unique constraint.
        path.unlink(missing_ok=True)
        if found := existing():
            return found
        raise
    except Exception:
        path.unlink(missing_ok=True)
        raise
    background_tasks.add_task(ingest, db, document_id)
    return _get_document(db, user_id, document_id)


@router.get("/api/knowledge-bases/{kb_id}/documents", response_model=list[DocumentOut])
def list_documents(kb_id: str, user_id: User, db: DB):
    with db.connect() as connection:
        require_knowledge_base(connection, user_id, kb_id)
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM documents WHERE knowledge_base_id=? ORDER BY created_at, id",
                (kb_id,),
            )
        ]


@router.get("/api/documents/{document_id}", response_model=DocumentOut)
def document_status(document_id: str, user_id: User, db: DB):
    return _get_document(db, user_id, document_id)


@router.get("/api/documents/{document_id}/chunks", response_model=list[ChunkOut])
def read_document(document_id: str, user_id: User, db: DB):
    with db.connect() as connection:
        require_document(connection, user_id, document_id)
        rows = connection.execute(
            "SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal", (document_id,)
        ).fetchall()
    return [
        ChunkOut(
            id=row["id"],
            ordinal=row["ordinal"],
            content=row["content"],
            location=Location.from_row(row),
        )
        for row in rows
    ]


@router.post("/api/documents/{document_id}/retry", response_model=DocumentOut, status_code=202)
def retry_document(document_id: str, user_id: User, db: DB, background_tasks: BackgroundTasks):
    with db.connect() as connection:
        require_document(connection, user_id, document_id)
        changed = connection.execute(
            """UPDATE documents SET status='queued', error=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='failed'""",
            (document_id,),
        ).rowcount
    if not changed:
        raise AppError(409, "document_not_failed", "仅失败的文档可以重试")
    background_tasks.add_task(ingest, db, document_id)
    return _get_document(db, user_id, document_id)


@router.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: str, user_id: User, db: DB):
    row = _get_document(db, user_id, document_id)
    with db.connect() as connection:
        connection.execute("DELETE FROM documents WHERE id=?", (document_id,))
    Path(row["path"]).unlink(missing_ok=True)
