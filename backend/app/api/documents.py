"""Document upload, listing, status, chunks, retry, rename/move and delete routes. Owner: A.
Contract: docs/contracts.md §3.1–§3.2."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile

from app.deps import DB, AppSettings, UploadDir, User
from app.schemas import ChunkOut, DocumentOut, DocumentUpdate
from app.services import documents as service
from app.services.access import require_document
from app.services.ingestion import ingest

router = APIRouter(tags=["documents"])


def _optional_id(value: str | None) -> str | None:
    # HTML forms and `?folder_id=` send an empty string for "no folder"; treat it as the root.
    return value or None


@router.post("/api/knowledge-bases/{kb_id}/documents", response_model=DocumentOut, status_code=202)
def upload_document(
    kb_id: str,
    user_id: User,
    db: DB,
    settings: AppSettings,
    upload_dir: UploadDir,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    folder_id: Annotated[str | None, Form()] = None,
):
    folder_id = _optional_id(folder_id)
    # Reject a missing knowledge base or folder before reading the upload body.
    with db.connect() as connection:
        service.check_upload_target(connection, user_id, kb_id, folder_id)
    document = service.create_document(
        db,
        user_id,
        kb_id,
        upload_dir,
        filename=file.filename,
        content=file.file.read(settings.max_upload_bytes + 1),
        folder_id=folder_id,
        max_bytes=settings.max_upload_bytes,
    )
    if not document["duplicate"]:
        # Vector retrieval needs the app settings to embed chunks during ingestion.
        background_tasks.add_task(ingest, db, document["id"], settings)
    return document


@router.get("/api/knowledge-bases/{kb_id}/documents", response_model=list[DocumentOut])
def list_documents(
    kb_id: str, user_id: User, db: DB, folder_id: str | None = None, recursive: bool = True
):
    with db.connect() as connection:
        return service.list_documents(
            connection, user_id, kb_id, folder_id=_optional_id(folder_id), recursive=recursive
        )


@router.get("/api/documents/{document_id}", response_model=DocumentOut)
def document_status(document_id: str, user_id: User, db: DB):
    with db.connect() as connection:
        return require_document(connection, user_id, document_id)


@router.get("/api/documents/{document_id}/chunks", response_model=list[ChunkOut])
def read_document(document_id: str, user_id: User, db: DB):
    with db.connect() as connection:
        return service.list_chunks(connection, user_id, document_id)


@router.post("/api/documents/{document_id}/retry", response_model=DocumentOut, status_code=202)
def retry_document(
    document_id: str,
    user_id: User,
    db: DB,
    background_tasks: BackgroundTasks,
    settings: AppSettings,
):
    document = service.requeue_failed_document(db, user_id, document_id)
    background_tasks.add_task(ingest, db, document_id, settings)
    return document


@router.patch("/api/documents/{document_id}", response_model=DocumentOut)
def update_document(document_id: str, payload: DocumentUpdate, user_id: User, db: DB):
    return service.update_document(db, user_id, document_id, payload)


@router.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: str, user_id: User, db: DB):
    service.delete_document(db, user_id, document_id)
