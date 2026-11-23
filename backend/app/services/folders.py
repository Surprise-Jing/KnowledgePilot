"""Folder tree services. Owner: A. Contract: docs/contracts.md §3.2.

Folders form a tree per knowledge base through `parent_id` (NULL = root). Sibling names are
unique via a database index, so conflicts are detected atomically by the insert/update."""

import sqlite3
from uuid import uuid4

from app.db import Database
from app.errors import AppError
from app.schemas import FolderUpdate
from app.services.access import require_folder, require_folder_in, require_knowledge_base
from app.services.documents import load_folders, remove_stored_files, subtree

NAME_CONFLICT_MESSAGE = "同一目录下已存在同名目录"


def _is_name_conflict(exc: sqlite3.IntegrityError) -> bool:
    # Only the sibling-name index is a user error; any other constraint failure is a bug and
    # is re-raised so it surfaces as a logged internal_error.
    return exc.sqlite_errorname == "SQLITE_CONSTRAINT_UNIQUE"


def _get(connection, folder_id: str) -> dict:
    return dict(connection.execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone())


def list_folders(connection, user_id: str, kb_id: str) -> list[dict]:
    """Flat list ordered by name; clients build the tree from `parent_id`."""
    require_knowledge_base(connection, user_id, kb_id)
    rows = connection.execute(
        "SELECT * FROM folders WHERE knowledge_base_id=? ORDER BY name, id", (kb_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def create_folder(
    db: Database, user_id: str, kb_id: str, name: str, parent_id: str | None = None
) -> dict:
    folder_id = str(uuid4())
    with db.connect() as connection:
        # Lock first so the parent cannot be deleted between the check and the insert.
        connection.execute("BEGIN IMMEDIATE")
        require_knowledge_base(connection, user_id, kb_id)
        if parent_id is not None:
            require_folder_in(connection, kb_id, parent_id)
        try:
            connection.execute(
                "INSERT INTO folders(id, knowledge_base_id, parent_id, name) VALUES (?, ?, ?, ?)",
                (folder_id, kb_id, parent_id, name),
            )
        except sqlite3.IntegrityError as exc:
            if not _is_name_conflict(exc):
                raise
            raise AppError(409, "name_conflict", NAME_CONFLICT_MESSAGE) from exc
        return _get(connection, folder_id)


def _creates_cycle(connection, kb_id: str, folder_id: str, new_parent_id: str) -> bool:
    """True when `new_parent_id` is `folder_id` itself or one of its descendants, i.e. when
    walking up from the new parent reaches the folder being moved."""
    folders = load_folders(connection, kb_id)
    current: str | None = new_parent_id
    seen: set[str] = set()
    while current is not None and current not in seen:
        if current == folder_id:
            return True
        seen.add(current)
        current = folders.get(current, (None, ""))[0]
    return False


def update_folder(db: Database, user_id: str, folder_id: str, changes: FolderUpdate) -> dict:
    """Rename and/or move within the same knowledge base. Omitted fields stay unchanged;
    an explicit `parent_id: null` moves the folder to the root."""
    fields = changes.model_fields_set
    with db.connect() as connection:
        # Lock first so the tree cannot change between the cycle check and the update.
        connection.execute("BEGIN IMMEDIATE")
        folder = require_folder(connection, user_id, folder_id)
        kb_id = folder["knowledge_base_id"]
        name = changes.name if "name" in fields else folder["name"]
        parent_id = changes.parent_id if "parent_id" in fields else folder["parent_id"]
        if "parent_id" in fields and parent_id is not None:
            require_folder_in(connection, kb_id, parent_id)
            if _creates_cycle(connection, kb_id, folder_id, parent_id):
                raise AppError(409, "folder_cycle", "不能把目录移动到它自己或它的子目录下")
        try:
            connection.execute(
                """UPDATE folders SET name=?, parent_id=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
                (name, parent_id, folder_id),
            )
        except sqlite3.IntegrityError as exc:
            if not _is_name_conflict(exc):
                raise
            raise AppError(409, "name_conflict", NAME_CONFLICT_MESSAGE) from exc
        return _get(connection, folder_id)


def delete_folder(db: Database, user_id: str, folder_id: str, *, recursive: bool = False) -> None:
    """Idempotent: a missing or foreign folder is a no-op. A non-empty folder is only deleted
    with `recursive`; then the whole subtree goes by cascade and the original files of its
    documents are removed after commit."""
    with db.connect() as connection:
        # The write lock keeps uploads and moves out of the subtree while it is inspected
        # and deleted, so no document is removed without its file being collected.
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT f.knowledge_base_id FROM folders f
            JOIN knowledge_bases k ON k.id = f.knowledge_base_id
            WHERE f.id=? AND k.user_id=?""",
            (folder_id, user_id),
        ).fetchone()
        if row is None:
            return
        kb_id = row["knowledge_base_id"]
        scope = subtree(load_folders(connection, kb_id), folder_id)
        placeholders = ",".join("?" for _ in scope)
        files = [
            (doc["id"], doc["path"])
            for doc in connection.execute(
                f"SELECT id, path FROM documents WHERE folder_id IN ({placeholders})",
                list(scope),
            )
        ]
        if not recursive and (len(scope) > 1 or files):
            raise AppError(
                409, "folder_not_empty", "目录不为空，请先清空或使用递归删除（recursive=true）"
            )
        connection.execute("DELETE FROM folders WHERE id=?", (folder_id,))
    remove_stored_files(files)
