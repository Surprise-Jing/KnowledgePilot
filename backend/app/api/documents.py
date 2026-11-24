"""Document upload, listing, status, chunks, retry, rename/move and delete routes. Owner: A.
Contract: docs/contracts.md §3.1–§3.2."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile
from fastapi.responses import FileResponse

from app.deps import DB, AppSettings, ArtifactsRoot, Parsers, UploadDir, User
from app.errors import AppError
from app.schemas import ChunkOut, DocumentOut, DocumentUpdate, ParseMode, ReparseRequest
from app.services import documents as service
from app.services.access import require_document
from app.services.ingestion import schedule

router = APIRouter(tags=["documents"])


def _optional_id(value: str | None) -> str | None:
    # HTML forms and `?folder_id=` send an empty string for "no folder"; treat it as the root.
    return value or None


def _parse_mode(value: str | None) -> ParseMode:
    if not value:
        return "standard"
    if value not in ("standard", "enhanced"):
        raise AppError(422, "invalid_argument", "解析模式只能是 standard 或 enhanced")
    return value


@router.post("/api/knowledge-bases/{kb_id}/documents", response_model=DocumentOut, status_code=202)
def upload_document(
    kb_id: str,
    user_id: User,
    db: DB,
    settings: AppSettings,
    upload_dir: UploadDir,
    parsers: Parsers,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    folder_id: Annotated[str | None, Form()] = None,
    parse_mode: Annotated[str | None, Form()] = None,
):
    folder_id = _optional_id(folder_id)
    mode = _parse_mode(parse_mode)
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
        parse_mode=mode,
    )
    if not document["duplicate"]:
        # Vector retrieval needs the app settings to embed chunks during ingestion.
        background_tasks.add_task(schedule, db, document["id"], settings, parsers)
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
    parsers: Parsers,
):
    document = service.requeue_failed_document(db, user_id, document_id)
    background_tasks.add_task(schedule, db, document_id, settings, parsers)
    return document


@router.post("/api/documents/{document_id}/reparse", response_model=DocumentOut, status_code=202)
def reparse_document(
    document_id: str,
    user_id: User,
    db: DB,
    background_tasks: BackgroundTasks,
    settings: AppSettings,
    parsers: Parsers,
    payload: ReparseRequest | None = None,
):
    mode = payload.parse_mode if payload is not None else None
    document = service.requeue_document(db, user_id, document_id, mode)
    background_tasks.add_task(schedule, db, document_id, settings, parsers)
    return document


@router.get("/api/documents/{document_id}/markdown")
def export_markdown(document_id: str, user_id: User, db: DB, artifacts_root: ArtifactsRoot):
    with db.connect() as connection:
        path, filename = service.markdown_export(connection, user_id, document_id, artifacts_root)
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=filename)


@router.get("/api/documents/{document_id}/file")
def original_file(document_id: str, user_id: User, db: DB):
    with db.connect() as connection:
        path, filename, media_type = service.original_file(connection, user_id, document_id)
    return FileResponse(
        path, media_type=media_type, filename=filename, content_disposition_type="inline"
    )


@router.patch("/api/documents/{document_id}", response_model=DocumentOut)
def update_document(document_id: str, payload: DocumentUpdate, user_id: User, db: DB):
    return service.update_document(db, user_id, document_id, payload)


@router.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: str, user_id: User, db: DB, artifacts_root: ArtifactsRoot):
    service.delete_document(db, user_id, document_id, artifacts_root=artifacts_root)
