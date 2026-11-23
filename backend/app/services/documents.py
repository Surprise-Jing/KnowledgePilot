"""Document read services shared by HTTP routes, Agent tools and the MCP server. Owner: A.

`list_files` and `read_document_range` were delivered first (milestone M1) as the stable
interface the tool layer builds on; see docs/contracts.md §3.4. Both check ownership
themselves, so callers never need to query tables directly."""

from app.errors import AppError
from app.schemas import ChunkOut, DocumentRange, FileEntry, FileListing, Location
from app.services.access import require_document, require_knowledge_base

MAX_LIST_LIMIT = 50
MIN_READ_CHARS = 500
MAX_READ_CHARS = 8000


def _load_folders(connection, kb_id: str) -> dict[str, tuple[str | None, str]]:
    rows = connection.execute(
        "SELECT id, parent_id, name FROM folders WHERE knowledge_base_id=?", (kb_id,)
    ).fetchall()
    return {row["id"]: (row["parent_id"], row["name"]) for row in rows}


def _folder_paths(folders: dict[str, tuple[str | None, str]]) -> dict[str, list[str]]:
    """Map each folder id to its names from the root. The move API rejects cycles; the visited
    set only keeps a corrupted tree from looping forever."""
    paths: dict[str, list[str]] = {}

    def resolve(folder_id: str) -> list[str]:
        if folder_id in paths:
            return paths[folder_id]
        chain, current, seen = [], folder_id, set()
        while current is not None and current in folders and current not in seen:
            seen.add(current)
            parent_id, name = folders[current]
            chain.append(name)
            current = parent_id
        paths[folder_id] = list(reversed(chain))
        return paths[folder_id]

    for folder_id in folders:
        resolve(folder_id)
    return paths


def _subtree(folders: dict[str, tuple[str | None, str]], root_id: str) -> set[str]:
    children: dict[str, list[str]] = {}
    for folder_id, (parent_id, _) in folders.items():
        if parent_id is not None:
            children.setdefault(parent_id, []).append(folder_id)
    result, stack = set(), [root_id]
    while stack:
        current = stack.pop()
        if current not in result:
            result.add(current)
            stack.extend(children.get(current, []))
    return result


def list_files(
    connection,
    user_id: str,
    kb_id: str,
    *,
    folder_id: str | None = None,
    recursive: bool = True,
    offset: int = 0,
    limit: int = MAX_LIST_LIMIT,
) -> FileListing:
    """List documents of one knowledge base. `folder_id=None` starts at the root; with
    `recursive` descendants are included, otherwise only direct children."""
    if offset < 0 or not 1 <= limit <= MAX_LIST_LIMIT:
        raise AppError(422, "invalid_argument", f"offset 不能小于 0，limit 取值 1–{MAX_LIST_LIMIT}")
    require_knowledge_base(connection, user_id, kb_id)
    folders = _load_folders(connection, kb_id)
    if folder_id is not None and folder_id not in folders:
        raise AppError(404, "folder_not_found", "目录不存在")
    paths = _folder_paths(folders)
    if recursive:
        scope = None if folder_id is None else _subtree(folders, folder_id)

        def included(doc_folder):
            return scope is None or doc_folder in scope
    else:

        def included(doc_folder):
            return doc_folder == folder_id

    rows = connection.execute(
        """SELECT id, knowledge_base_id, folder_id, filename, file_type, status, error
        FROM documents WHERE knowledge_base_id=?""",
        (kb_id,),
    ).fetchall()
    entries = [
        FileEntry(
            document_id=row["id"],
            knowledge_base_id=row["knowledge_base_id"],
            folder_id=row["folder_id"],
            folder_path=paths.get(row["folder_id"], []) if row["folder_id"] else [],
            filename=row["filename"],
            file_type=row["file_type"],
            status=row["status"],
            error=row["error"],
        )
        for row in rows
        if included(row["folder_id"])
    ]
    entries.sort(key=lambda entry: (entry.folder_path, entry.filename, entry.document_id))
    return FileListing(
        files=entries[offset : offset + limit], total=len(entries), offset=offset, limit=limit
    )


def read_document_range(
    connection,
    user_id: str,
    document_id: str,
    *,
    start_ordinal: int = 0,
    max_chars: int = 4000,
) -> DocumentRange:
    """Read consecutive chunks from `start_ordinal` until adding the next one would exceed
    `max_chars`. At least one chunk is always returned so reading can make progress."""
    if not MIN_READ_CHARS <= max_chars <= MAX_READ_CHARS:
        raise AppError(422, "invalid_argument", f"max_chars 取值 {MIN_READ_CHARS}–{MAX_READ_CHARS}")
    if start_ordinal < 0:
        raise AppError(422, "invalid_argument", "start_ordinal 不能小于 0")
    document = require_document(connection, user_id, document_id)
    if document["status"] != "success":
        pending = document["status"] in ("queued", "parsing")
        message = "文档正在解析，请稍后再读" if pending else "文档解析失败，无法读取原文"
        raise AppError(409, "document_not_ready", message, retryable=pending)
    total = connection.execute(
        "SELECT count(*) FROM chunks WHERE document_id=?", (document_id,)
    ).fetchone()[0]
    if start_ordinal >= total:
        raise AppError(422, "invalid_argument", f"start_ordinal 超出范围，文档共 {total} 个片段")
    cursor = connection.execute(
        "SELECT * FROM chunks WHERE document_id=? AND ordinal>=? ORDER BY ordinal",
        (document_id, start_ordinal),
    )
    chunks: list[ChunkOut] = []
    used = 0
    for row in cursor:
        if chunks and used + len(row["content"]) > max_chars:
            break
        chunks.append(
            ChunkOut(
                id=row["id"],
                ordinal=row["ordinal"],
                content=row["content"],
                location=Location.from_row(row),
            )
        )
        used += len(row["content"])
    next_ordinal = chunks[-1].ordinal + 1
    has_more = next_ordinal < total
    return DocumentRange(
        document_id=document_id,
        knowledge_base_id=document["knowledge_base_id"],
        filename=document["filename"],
        chunks=chunks,
        truncated=has_more,
        next_ordinal=next_ordinal if has_more else None,
        total_chunks=total,
    )
