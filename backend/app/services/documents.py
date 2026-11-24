"""Document services shared by HTTP routes, Agent tools and the MCP server. Owner: A.

`list_files` and `read_document_range` were delivered first (milestone M1) as the stable
interface the tool layer builds on; see docs/contracts.md §3.4. Both check ownership
themselves, so callers never need to query tables directly. The upload / rename / move /
delete services below implement docs/contracts.md §3.1–§3.2."""

import hashlib
import logging
import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path
from uuid import uuid4

from app.db import Database
from app.errors import AppError
from app.schemas import (
    ChunkOut,
    DocumentRange,
    DocumentUpdate,
    FileEntry,
    FileListing,
    Location,
    ParseMode,
)
from app.services import artifacts
from app.services.access import require_document, require_folder_in, require_knowledge_base

logger = logging.getLogger(__name__)

MAX_LIST_LIMIT = 50
MIN_READ_CHARS = 500
MAX_READ_CHARS = 8000
SUPPORTED_SUFFIXES = {".txt", ".pdf", ".docx"}

FolderMap = dict[str, tuple[str | None, str]]


def load_folders(connection, kb_id: str) -> FolderMap:
    """Map folder id → (parent_id, name) for one knowledge base."""
    rows = connection.execute(
        "SELECT id, parent_id, name FROM folders WHERE knowledge_base_id=?", (kb_id,)
    ).fetchall()
    return {row["id"]: (row["parent_id"], row["name"]) for row in rows}


def _folder_paths(folders: FolderMap) -> dict[str, list[str]]:
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


def subtree(folders: FolderMap, root_id: str) -> set[str]:
    """`root_id` and all of its descendants."""
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


def _folder_scope(
    folders: FolderMap, folder_id: str | None, recursive: bool
) -> Callable[[str | None], bool]:
    """Predicate on a document's folder_id: inside `folder_id` (None = root), including
    descendants when `recursive`, otherwise only direct children."""
    if folder_id is not None and folder_id not in folders:
        raise AppError(404, "folder_not_found", "目录不存在")
    if not recursive:
        return lambda doc_folder: doc_folder == folder_id
    if folder_id is None:
        return lambda doc_folder: True
    scope = subtree(folders, folder_id)
    return lambda doc_folder: doc_folder in scope


def remove_stored_files(files: Iterable[tuple[str, str]], artifacts_root: Path) -> None:
    """Unlink the original files and parse artifacts of `(document_id, path)` pairs after their
    rows were deleted and committed. A failure leaves only orphaned files, so it is logged
    instead of turning a committed delete into an error."""
    files = list(files)
    artifacts.remove_documents(artifacts_root, [document_id for document_id, _ in files])
    for document_id, path in files:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "Failed to remove stored file of document %s: %s", document_id, type(exc).__name__
            )


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
    folders = load_folders(connection, kb_id)
    included = _folder_scope(folders, folder_id, recursive)
    paths = _folder_paths(folders)
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
        chunks.append(_chunk_out(row))
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


def _chunk_out(row) -> ChunkOut:
    return ChunkOut(
        id=row["id"],
        ordinal=row["ordinal"],
        content=row["content"],
        location=Location.from_row(row),
    )


# ---- HTTP document management (docs/contracts.md §3.1, §3.2) ----


def list_documents(
    connection,
    user_id: str,
    kb_id: str,
    *,
    folder_id: str | None = None,
    recursive: bool = True,
) -> list[dict]:
    """Documents of one knowledge base in upload order, scoped like `list_files`. The defaults
    (root, recursive) return every document, as the endpoint did before folders existed."""
    require_knowledge_base(connection, user_id, kb_id)
    included = _folder_scope(load_folders(connection, kb_id), folder_id, recursive)
    rows = connection.execute(
        "SELECT * FROM documents WHERE knowledge_base_id=? ORDER BY created_at, id", (kb_id,)
    ).fetchall()
    return [dict(row) for row in rows if included(row["folder_id"])]


def list_chunks(connection, user_id: str, document_id: str) -> list[ChunkOut]:
    require_document(connection, user_id, document_id)
    rows = connection.execute(
        "SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal", (document_id,)
    ).fetchall()
    return [_chunk_out(row) for row in rows]


def check_upload_target(connection, user_id: str, kb_id: str, folder_id: str | None) -> None:
    require_knowledge_base(connection, user_id, kb_id)
    if folder_id is not None:
        require_folder_in(connection, kb_id, folder_id)


def _find_duplicate(connection, kb_id: str, digest: str) -> dict | None:
    row = connection.execute(
        "SELECT * FROM documents WHERE knowledge_base_id=? AND sha256=?", (kb_id, digest)
    ).fetchone()
    return dict(row) if row else None


def create_document(
    db: Database,
    user_id: str,
    kb_id: str,
    upload_dir: Path,
    *,
    filename: str | None,
    content: bytes,
    folder_id: str | None,
    max_bytes: int,
    parse_mode: ParseMode = "standard",
) -> dict:
    """Store an upload as a queued document and return its row with a `duplicate` flag.
    `content` may be one byte longer than `max_bytes` so oversize uploads are detected
    without reading the whole stream. Same content in the same knowledge base returns the
    existing record (possibly in another folder) with `duplicate: True`; the caller only
    schedules ingestion for new records."""
    name = Path((filename or "").replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise AppError(415, "unsupported_format", "仅支持 TXT（UTF-8 或 GBK）、PDF、DOCX")
    if len(content) > max_bytes:
        raise AppError(413, "file_too_large", "文件超过上传大小限制")
    if not content:
        raise AppError(400, "empty_file", "不能上传空文件")
    digest = hashlib.sha256(content).hexdigest()
    # Only PDF/DOCX go through Docling; TXT has no enhanced (OCR) mode.
    if suffix == ".txt":
        parse_mode = "standard"

    with db.connect() as connection:
        check_upload_target(connection, user_id, kb_id, folder_id)
        if found := _find_duplicate(connection, kb_id, digest):
            return {**found, "duplicate": True}

    document_id = str(uuid4())
    path = (upload_dir / f"{document_id}{suffix}").resolve()
    # Write the file outside any transaction so no database lock is held during disk I/O.
    path.write_bytes(content)
    try:
        with db.connect() as connection:
            # The write lock makes the target checks and the insert atomic: the knowledge base
            # or folder cannot be deleted in between and leave this row orphaned.
            connection.execute("BEGIN IMMEDIATE")
            check_upload_target(connection, user_id, kb_id, folder_id)
            connection.execute(
                """INSERT INTO documents
                (id, knowledge_base_id, folder_id, filename, file_type, size_bytes, sha256, path,
                 status, parse_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
                (
                    document_id,
                    kb_id,
                    folder_id,
                    name,
                    suffix[1:],
                    len(content),
                    digest,
                    str(path),
                    parse_mode,
                ),
            )
            row = dict(
                connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            )
    except sqlite3.IntegrityError:
        # A concurrent upload of the same content won the unique constraint.
        path.unlink(missing_ok=True)
        with db.connect() as connection:
            found = _find_duplicate(connection, kb_id, digest)
        if found:
            return {**found, "duplicate": True}
        raise
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {**row, "duplicate": False}


def requeue_failed_document(db: Database, user_id: str, document_id: str) -> dict:
    """Mark a failed document as queued again; the caller schedules ingestion."""
    with db.connect() as connection:
        require_document(connection, user_id, document_id)
        changed = connection.execute(
            """UPDATE documents SET status='queued', error=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='failed'""",
            (document_id,),
        ).rowcount
        if not changed:
            raise AppError(409, "document_not_failed", "仅失败的文档可以重试")
        return require_document(connection, user_id, document_id)


def requeue_document(
    db: Database, user_id: str, document_id: str, parse_mode: ParseMode | None
) -> dict:
    """Queue a parsed or failed document for parsing again, optionally switching the parse
    mode (None keeps the current one). The caller schedules ingestion."""
    with db.connect() as connection:
        document = require_document(connection, user_id, document_id)
        if document["status"] in ("queued", "parsing"):
            raise AppError(409, "document_busy", "文档正在解析，请稍后再试", retryable=True)
        mode = parse_mode or document["parse_mode"]
        if document["file_type"] == "txt":
            mode = "standard"
        changed = connection.execute(
            """UPDATE documents SET status='queued', error=NULL, parse_mode=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('success','failed')""",
            (mode, document_id),
        ).rowcount
        if not changed:
            raise AppError(409, "document_busy", "文档正在解析，请稍后再试", retryable=True)
        return require_document(connection, user_id, document_id)


MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain; charset=utf-8",
}


def markdown_export(
    connection, user_id: str, document_id: str, artifacts_root: Path
) -> tuple[Path, str]:
    """Path of the document's Markdown artifact and its download name (`<stem>.md`)."""
    document = require_document(connection, user_id, document_id)
    if document["status"] != "success":
        pending = document["status"] in ("queued", "parsing")
        message = "文档正在解析，请稍后再导出" if pending else "文档解析失败，无法导出"
        raise AppError(409, "document_not_ready", message, retryable=pending)
    path = artifacts.final_dir(artifacts_root, document_id) / artifacts.MARKDOWN
    if not path.is_file():
        raise AppError(409, "markdown_not_available", "请重新解析后再导出")
    stem = Path(document["filename"]).stem.strip()
    return path, f"{stem or 'document'}.md"


def original_file(connection, user_id: str, document_id: str) -> tuple[Path, str, str]:
    """(path, filename, media type) of the stored upload."""
    document = require_document(connection, user_id, document_id)
    path = Path(document["path"])
    if not path.is_file():
        raise AppError(404, "document_not_found", "原文件不存在")
    return path, document["filename"], MEDIA_TYPES[document["file_type"]]


def update_document(db: Database, user_id: str, document_id: str, changes: DocumentUpdate) -> dict:
    """Rename and/or move a document within its knowledge base. Renaming only changes the
    display name: `file_type`, the stored file and the parsed chunks stay as they are."""
    fields = changes.model_fields_set
    with db.connect() as connection:
        # Lock first so the target folder cannot be deleted between the check and the update.
        connection.execute("BEGIN IMMEDIATE")
        document = require_document(connection, user_id, document_id)
        filename = changes.filename if "filename" in fields else document["filename"]
        folder_id = changes.folder_id if "folder_id" in fields else document["folder_id"]
        if "folder_id" in fields and folder_id is not None:
            # Only same-knowledge-base moves are supported; another base's folder is "missing".
            require_folder_in(connection, document["knowledge_base_id"], folder_id)
        connection.execute(
            """UPDATE documents SET filename=?, folder_id=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
            (filename, folder_id, document_id),
        )
        return require_document(connection, user_id, document_id)


def delete_document(db: Database, user_id: str, document_id: str, *, artifacts_root: Path) -> None:
    """Idempotent: a missing or foreign document is a no-op, so the caller cannot tell them
    apart. Chunks and vectors are removed by cascade; the file is removed after commit."""
    with db.connect() as connection:
        row = connection.execute(
            """SELECT d.id, d.path FROM documents d
            JOIN knowledge_bases k ON k.id = d.knowledge_base_id
            WHERE d.id=? AND k.user_id=?""",
            (document_id, user_id),
        ).fetchone()
        if row is None:
            return
        connection.execute("DELETE FROM documents WHERE id=?", (document_id,))
    remove_stored_files([(row["id"], row["path"])], artifacts_root)
