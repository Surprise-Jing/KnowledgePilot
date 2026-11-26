"""Resolve a question's selected resources once, without holding a transaction across awaits."""

from app.errors import AppError
from app.schemas import ChatScope, ScopeSnapshot
from app.services.access import require_document, require_folder, require_knowledge_bases
from app.services.documents import load_folders, subtree


def resolve_scope(connection, user_id: str, selection: ChatScope) -> ScopeSnapshot:
    if selection.mode == "auto":
        ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM knowledge_bases WHERE user_id=? ORDER BY id", (user_id,)
            )
        ]
        return ScopeSnapshot(selection=selection, labels=["自动查找资料"], knowledge_base_ids=ids)

    kb_ids = require_knowledge_bases(connection, user_id, selection.knowledge_base_ids)
    labels, documents = [], {}
    for kb_id in kb_ids:
        kb = connection.execute("SELECT name FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone()
        labels.append(kb["name"] + " · 全库")
        for row in connection.execute(
            "SELECT * FROM documents WHERE knowledge_base_id=?", (kb_id,)
        ):
            documents[row["id"]] = dict(row)
    for folder_id in dict.fromkeys(selection.folder_ids):
        folder = require_folder(connection, user_id, folder_id)
        kb_id = folder["knowledge_base_id"]
        if kb_id not in kb_ids:
            kb_ids.append(kb_id)
        labels.append(folder["name"] + " · 含子目录")
        folder_ids = subtree(load_folders(connection, kb_id), folder_id)
        for row in connection.execute(
            "SELECT * FROM documents WHERE knowledge_base_id=?", (kb_id,)
        ):
            if row["folder_id"] in folder_ids:
                documents[row["id"]] = dict(row)
    for document_id in dict.fromkeys(selection.document_ids):
        document = require_document(connection, user_id, document_id)
        if document["status"] != "success":
            raise AppError(
                409,
                "document_not_ready",
                "所选文档尚未解析成功，请完成解析后再提问",
                retryable=document["status"] in {"queued", "parsing"},
            )
        documents[document_id] = document
        if document["knowledge_base_id"] not in kb_ids:
            kb_ids.append(document["knowledge_base_id"])
        labels.append(document["filename"])
    return ScopeSnapshot(
        selection=selection, labels=labels, knowledge_base_ids=kb_ids, document_ids=list(documents)
    )
