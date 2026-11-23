"""Ownership checks shared by HTTP routes and, later, Agent tools and the MCP server.

Every lookup is scoped by user. A resource owned by someone else is reported as missing,
so callers cannot probe which ids exist."""

import re

from app.errors import AppError

USER_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


def ensure_user(connection, user_id: str) -> str:
    if not USER_ID_PATTERN.fullmatch(user_id):
        raise AppError(
            400, "invalid_user_id", "用户标识只能包含字母、数字、下划线和连字符，最长 64 位"
        )
    connection.execute("INSERT OR IGNORE INTO users(id, name) VALUES (?, ?)", (user_id, user_id))
    return user_id


def require_knowledge_base(connection, user_id: str, kb_id: str) -> dict:
    row = connection.execute(
        "SELECT * FROM knowledge_bases WHERE id=? AND user_id=?", (kb_id, user_id)
    ).fetchone()
    if row is None:
        raise AppError(404, "knowledge_base_not_found", "知识库不存在")
    return dict(row)


def require_knowledge_bases(connection, user_id: str, kb_ids: list[str]) -> list[str]:
    ids = list(dict.fromkeys(kb_ids))
    placeholders = ",".join("?" for _ in ids)
    count = connection.execute(
        f"SELECT count(*) FROM knowledge_bases WHERE user_id=? AND id IN ({placeholders})",
        [user_id, *ids],
    ).fetchone()[0]
    if count != len(ids):
        raise AppError(404, "knowledge_base_not_found", "指定的知识库不存在")
    return ids


def require_document(connection, user_id: str, document_id: str) -> dict:
    row = connection.execute(
        """SELECT d.* FROM documents d JOIN knowledge_bases k ON k.id = d.knowledge_base_id
        WHERE d.id=? AND k.user_id=?""",
        (document_id, user_id),
    ).fetchone()
    if row is None:
        raise AppError(404, "document_not_found", "文档不存在")
    return dict(row)


def require_folder(connection, user_id: str, folder_id: str) -> dict:
    row = connection.execute(
        """SELECT f.* FROM folders f JOIN knowledge_bases k ON k.id = f.knowledge_base_id
        WHERE f.id=? AND k.user_id=?""",
        (folder_id, user_id),
    ).fetchone()
    if row is None:
        raise AppError(404, "folder_not_found", "目录不存在")
    return dict(row)


def require_folder_in(connection, kb_id: str, folder_id: str) -> dict:
    """Check that `folder_id` belongs to `kb_id`. Callers have already checked that the user
    owns `kb_id`, so a folder of another knowledge base (or another user) reads as missing."""
    row = connection.execute(
        "SELECT * FROM folders WHERE id=? AND knowledge_base_id=?", (folder_id, kb_id)
    ).fetchone()
    if row is None:
        raise AppError(404, "folder_not_found", "目录不存在")
    return dict(row)
