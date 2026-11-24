"""Knowledge base services. Owner: A. Contract: docs/contracts.md §3.1–§3.2."""

from pathlib import Path
from uuid import uuid4

from app.db import Database
from app.services.access import require_knowledge_base
from app.services.documents import remove_stored_files


def create_knowledge_base(db: Database, user_id: str, name: str) -> dict:
    kb_id = str(uuid4())
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO knowledge_bases(id, user_id, name) VALUES (?, ?, ?)",
            (kb_id, user_id, name),
        )
        return require_knowledge_base(connection, user_id, kb_id)


def list_knowledge_bases(connection, user_id: str) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM knowledge_bases WHERE user_id=? ORDER BY created_at, id", (user_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def rename_knowledge_base(db: Database, user_id: str, kb_id: str, name: str) -> dict:
    with db.connect() as connection:
        require_knowledge_base(connection, user_id, kb_id)
        connection.execute(
            "UPDATE knowledge_bases SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (name, kb_id),
        )
        return require_knowledge_base(connection, user_id, kb_id)


def delete_knowledge_base(db: Database, user_id: str, kb_id: str, *, artifacts_root: Path) -> None:
    """Idempotent: a missing or foreign knowledge base is a no-op, so the caller cannot tell
    them apart. Folders, documents, chunks and vectors go by cascade; the original files are
    removed only after the delete has committed, so a failed commit never loses files."""
    with db.connect() as connection:
        # The write lock keeps an upload from adding a document (and file) between collecting
        # the paths and deleting the rows.
        connection.execute("BEGIN IMMEDIATE")
        owned = connection.execute(
            "SELECT 1 FROM knowledge_bases WHERE id=? AND user_id=?", (kb_id, user_id)
        ).fetchone()
        if owned is None:
            return
        files = [
            (row["id"], row["path"])
            for row in connection.execute(
                "SELECT id, path FROM documents WHERE knowledge_base_id=?", (kb_id,)
            )
        ]
        connection.execute("DELETE FROM knowledge_bases WHERE id=?", (kb_id,))
    remove_stored_files(files, artifacts_root)
