"""Folder routes. Owner: A. Contract: docs/contracts.md §3.2."""

from fastapi import APIRouter

from app.deps import DB, ArtifactsRoot, User
from app.schemas import FolderCreate, FolderOut, FolderUpdate
from app.services import folders as service

router = APIRouter(tags=["folders"])


@router.get("/api/knowledge-bases/{kb_id}/folders", response_model=list[FolderOut])
def list_folders(kb_id: str, user_id: User, db: DB):
    with db.connect() as connection:
        return service.list_folders(connection, user_id, kb_id)


@router.post("/api/knowledge-bases/{kb_id}/folders", response_model=FolderOut, status_code=201)
def create_folder(kb_id: str, payload: FolderCreate, user_id: User, db: DB):
    return service.create_folder(db, user_id, kb_id, payload.name, payload.parent_id)


@router.patch("/api/folders/{folder_id}", response_model=FolderOut)
def update_folder(folder_id: str, payload: FolderUpdate, user_id: User, db: DB):
    return service.update_folder(db, user_id, folder_id, payload)


@router.delete("/api/folders/{folder_id}", status_code=204)
def delete_folder(
    folder_id: str,
    user_id: User,
    db: DB,
    artifacts_root: ArtifactsRoot,
    recursive: bool = False,
):
    service.delete_folder(
        db, user_id, folder_id, artifacts_root=artifacts_root, recursive=recursive
    )
