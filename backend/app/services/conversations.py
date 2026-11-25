"""Conversations, messages, runs, tool calls and citations (docs/contracts.md §3.3). Owner: B.

Every function opens its own short transaction, so callers on the event loop can run them in
a worker thread and never hold a connection across `await`."""

import json
from dataclasses import dataclass, field
from uuid import uuid4

from app.db import Database
from app.errors import AppError
from app.schemas import (
    ChatScope,
    CitationOut,
    ConversationDetail,
    ConversationOut,
    Location,
    MessageOut,
    RunOut,
    ScopeSnapshot,
    ToolCallOut,
    ToolError,
    Usage,
)
from app.services.access import require_knowledge_bases
from app.services.chat_scope import resolve_scope

DEFAULT_TITLE = "新对话"
TITLE_CHARS = 30
MAX_RESULT_JSON_CHARS = 8000
INTERRUPTED_MESSAGE = "回答已中断"
FOCUS_RUNS = 3


def require_conversation(connection, user_id: str, conversation_id: str) -> dict:
    row = connection.execute(
        "SELECT * FROM conversations WHERE id=? AND user_id=?", (conversation_id, user_id)
    ).fetchone()
    if row is None:
        raise AppError(404, "conversation_not_found", "会话不存在")
    return dict(row)


def create_conversation(db: Database, user_id: str, title: str | None) -> ConversationOut:
    conversation_id = str(uuid4())
    title = (title or "").strip() or DEFAULT_TITLE
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO conversations(id, user_id, title) VALUES (?, ?, ?)",
            (conversation_id, user_id, title),
        )
        return ConversationOut(**require_conversation(connection, user_id, conversation_id))


def rename_conversation(
    db: Database, user_id: str, conversation_id: str, title: str
) -> ConversationOut:
    with db.connect() as connection:
        require_conversation(connection, user_id, conversation_id)
        connection.execute(
            "UPDATE conversations SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (title, conversation_id),
        )
        return ConversationOut(**require_conversation(connection, user_id, conversation_id))


def list_conversations(db: Database, user_id: str) -> list[ConversationOut]:
    # Timestamps have one-second resolution; the latest message rowid breaks ties so the
    # conversation with the most recent activity still comes first.
    with db.connect() as connection:
        rows = connection.execute(
            """SELECT c.* FROM conversations c WHERE c.user_id=?
            ORDER BY c.updated_at DESC,
                COALESCE((SELECT max(m.rowid) FROM messages m WHERE m.conversation_id=c.id), 0)
                    DESC,
                c.rowid DESC""",
            (user_id,),
        ).fetchall()
    return [ConversationOut(**dict(row)) for row in rows]


def _citation_from_row(row) -> CitationOut:
    location = Location(**json.loads(row["location_json"])) if row["location_json"] else None
    return CitationOut(
        marker=row["marker"],
        chunk_id=row["chunk_id"],
        document_id=row["document_id"],
        filename=row["filename"],
        location=location,
        location_label=row["location_label"],
        content=row["content"],
    )


def get_conversation(db: Database, user_id: str, conversation_id: str) -> ConversationDetail:
    with db.connect() as connection:
        conversation = require_conversation(connection, user_id, conversation_id)
        messages = connection.execute(
            """SELECT m.*, r.id AS run_id FROM messages m
            LEFT JOIN runs r ON r.assistant_message_id = m.id
            WHERE m.conversation_id=? ORDER BY m.created_at, m.rowid""",
            (conversation_id,),
        ).fetchall()
        citation_rows = connection.execute(
            """SELECT ci.* FROM citations ci JOIN messages m ON m.id = ci.message_id
            WHERE m.conversation_id=? ORDER BY ci.rowid""",
            (conversation_id,),
        ).fetchall()
    citations: dict[str, list[CitationOut]] = {}
    for row in citation_rows:
        citations.setdefault(row["message_id"], []).append(_citation_from_row(row))
    return ConversationDetail(
        **conversation,
        messages=[
            MessageOut(
                id=row["id"],
                role=row["role"],
                content=row["content"],
                status=row["status"],
                error=row["error"],
                created_at=row["created_at"],
                run_id=row["run_id"],
                citations=citations.get(row["id"], []),
                scope=ScopeSnapshot.model_validate_json(row["scope_json"])
                if row["scope_json"]
                else None,
            )
            for row in messages
        ],
    )


def delete_conversation(db: Database, user_id: str, conversation_id: str) -> None:
    """Idempotent; messages, runs, tool calls and citations cascade."""
    with db.connect() as connection:
        connection.execute(
            "DELETE FROM conversations WHERE id=? AND user_id=?", (conversation_id, user_id)
        )


@dataclass(frozen=True)
class StartedRun:
    run_id: str
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    scope: ScopeSnapshot | None = None


def start_run(
    db: Database,
    user_id: str,
    conversation_id: str,
    content: str,
    knowledge_base_ids: list[str],
    model: str | None,
    *,
    scope: ChatScope | None = None,
) -> StartedRun:
    """Write the user message, the streaming assistant message and the running run in one
    transaction. The run insert is conditional on no other running run in the conversation;
    the message inserts before it take SQLite's write lock, so the check cannot race."""
    run_id, user_message_id, assistant_message_id = str(uuid4()), str(uuid4()), str(uuid4())
    with db.connect() as connection:
        require_conversation(connection, user_id, conversation_id)
        snapshot = resolve_scope(connection, user_id, scope) if scope is not None else None
        kb_ids = (
            snapshot.knowledge_base_ids
            if snapshot
            else require_knowledge_bases(connection, user_id, knowledge_base_ids)
        )
        # The first question names a conversation that was created without a title.
        connection.execute(
            """UPDATE conversations SET title=?
            WHERE id=? AND title=?
                AND NOT EXISTS (SELECT 1 FROM messages WHERE conversation_id=?)""",
            (content[:TITLE_CHARS], conversation_id, DEFAULT_TITLE, conversation_id),
        )
        connection.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,)
        )
        connection.executemany(
            "INSERT INTO messages(id, conversation_id, role, content, status) VALUES (?,?,?,?,?)",
            [
                (user_message_id, conversation_id, "user", content, "completed"),
                (assistant_message_id, conversation_id, "assistant", "", "streaming"),
            ],
        )
        if snapshot:
            connection.execute(
                "UPDATE messages SET scope_json=? WHERE id IN (?,?)",
                (snapshot.model_dump_json(), user_message_id, assistant_message_id),
            )
        inserted = connection.execute(
            """INSERT INTO runs(id, conversation_id, user_message_id, assistant_message_id,
                knowledge_base_ids, status, model)
            SELECT ?, ?, ?, ?, ?, 'running', ?
            WHERE NOT EXISTS (
                SELECT 1 FROM runs WHERE conversation_id=? AND status='running')""",
            (
                run_id,
                conversation_id,
                user_message_id,
                assistant_message_id,
                json.dumps(kb_ids),
                model,
                conversation_id,
            ),
        ).rowcount
        if inserted == 0:
            # Raising inside the transaction rolls back the message inserts.
            raise AppError(
                409, "conversation_busy", "该会话正在回答上一个问题，请稍后再试", retryable=True
            )
    return StartedRun(run_id, conversation_id, user_message_id, assistant_message_id, snapshot)


def knowledge_base_names(db: Database, user_id: str, kb_ids: list[str]) -> list[tuple[str, str]]:
    if not kb_ids:
        return []
    placeholders = ",".join("?" for _ in kb_ids)
    with db.connect() as connection:
        rows = connection.execute(
            f"SELECT id, name FROM knowledge_bases WHERE user_id=? AND id IN ({placeholders})",
            [user_id, *kb_ids],
        ).fetchall()
    names = {row["id"]: row["name"] for row in rows}
    return [(kb_id, names[kb_id]) for kb_id in kb_ids if kb_id in names]


def load_history(db: Database, conversation_id: str, before_message_id: str) -> list[dict]:
    """Completed user/assistant messages before `before_message_id`, oldest first."""
    with db.connect() as connection:
        rows = connection.execute(
            """SELECT role, content FROM messages
            WHERE conversation_id=? AND status='completed'
                AND rowid < (SELECT rowid FROM messages WHERE id=?)
            ORDER BY rowid""",
            (conversation_id, before_message_id),
        ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in rows]


@dataclass
class SessionFocus:
    # (document_id, filename) cited by the most recent runs, most recent first.
    documents: list[tuple[str, str]] = field(default_factory=list)
    # (filename, location_label) from citations whose source document was deleted later.
    deleted_documents: list[tuple[str, str]] = field(default_factory=list)
    # (knowledge_base_id, name) searched by the previous question.
    last_scope: list[tuple[str, str]] = field(default_factory=list)


def session_focus(
    db: Database, user_id: str, conversation_id: str, exclude_run_id: str
) -> SessionFocus:
    with db.connect() as connection:
        runs = connection.execute(
            """SELECT id, assistant_message_id, knowledge_base_ids FROM runs
            WHERE conversation_id=? AND id<>?
            ORDER BY started_at DESC, rowid DESC LIMIT ?""",
            (conversation_id, exclude_run_id, FOCUS_RUNS),
        ).fetchall()
        documents: list[tuple[str, str]] = []
        deleted_documents: list[tuple[str, str]] = []
        for run in runs:
            for row in connection.execute(
                """SELECT ci.document_id, ci.filename, ci.location_label, d.id AS live_document_id
                FROM citations ci
                LEFT JOIN documents d ON d.id = ci.document_id
                WHERE ci.message_id=? ORDER BY ci.rowid""",
                (run["assistant_message_id"],),
            ):
                if row["live_document_id"] is not None:
                    entry = (row["live_document_id"], row["filename"])
                    if entry not in documents:
                        documents.append(entry)
                else:
                    entry = (row["filename"], row["location_label"])
                    if entry not in deleted_documents:
                        deleted_documents.append(entry)
    last_scope = json.loads(runs[0]["knowledge_base_ids"]) if runs else []
    return SessionFocus(
        documents=documents,
        deleted_documents=deleted_documents,
        last_scope=knowledge_base_names(db, user_id, last_scope),
    )


def insert_tool_call(
    db: Database,
    run_id: str,
    step: int,
    name: str,
    arguments: dict,
    *,
    attempt: int = 1,
    retry_of: str | None = None,
) -> str:
    """An automatic retry is its own row: attempt 2 points at the first call via retry_of."""
    call_id = str(uuid4())
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO tool_calls(id, run_id, step, tool_name, arguments, status, attempt,
                retry_of)
            VALUES (?, ?, ?, ?, ?, 'running', ?, ?)""",
            (
                call_id,
                run_id,
                step,
                name,
                json.dumps(arguments, ensure_ascii=False),
                attempt,
                retry_of,
            ),
        )
    return call_id


def finish_tool_call(
    db: Database,
    call_id: str,
    *,
    ok: bool,
    summary: str,
    result_json: str,
    error: ToolError | None,
    duration_ms: int,
    fallback: str | None = None,
) -> None:
    with db.connect() as connection:
        connection.execute(
            """UPDATE tool_calls SET status=?, result_summary=?, result_json=?, error_code=?,
                error_message=?, retryable=?, duration_ms=?, fallback=?
            WHERE id=?""",
            (
                "success" if ok else "error",
                summary,
                result_json[:MAX_RESULT_JSON_CHARS],
                error.code if error else None,
                error.message if error else None,
                int(error.retryable) if error else None,
                duration_ms,
                fallback,
                call_id,
            ),
        )


@dataclass(frozen=True)
class CitationRecord:
    marker: str
    chunk_id: str
    document_id: str
    filename: str
    location: dict
    content: str

    def to_out(self) -> CitationOut:
        location = Location(**self.location)
        return CitationOut(
            marker=self.marker,
            chunk_id=self.chunk_id,
            document_id=self.document_id,
            filename=self.filename,
            location=location,
            location_label=location.label,
            content=self.content,
        )


@dataclass
class RunResult:
    run_status: str  # completed | failed
    stop_reason: str
    message_status: str  # completed | failed
    content: str | None  # None keeps the stored content
    step_count: int
    usage: Usage
    error: str | None = None
    dropped_citations: list[str] = field(default_factory=list)
    citations: list[CitationRecord] = field(default_factory=list)
    # Any automatic tool retry, tool fallback or model retry happened (contracts §10.4).
    degraded: bool = False
    model_retries: int = 0


def finish_run(db: Database, run_id: str, result: RunResult) -> bool:
    """Finalize a run, its assistant message and citations. Only a running run is updated,
    so whichever of normal completion and interruption commits first wins. Returns whether
    this call finalized the run."""
    with db.connect() as connection:
        updated = connection.execute(
            """UPDATE runs SET status=?, stop_reason=?, step_count=?, prompt_tokens=?,
                completion_tokens=?, error=?, dropped_citations=?, degraded=?, model_retries=?,
                finished_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='running'""",
            (
                result.run_status,
                result.stop_reason,
                result.step_count,
                result.usage.prompt_tokens,
                result.usage.completion_tokens,
                result.error,
                json.dumps(result.dropped_citations),
                int(result.degraded),
                result.model_retries,
                run_id,
            ),
        ).rowcount
        if not updated:
            return False
        run = connection.execute(
            "SELECT conversation_id, assistant_message_id FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        message_id = run["assistant_message_id"]
        connection.execute(
            "UPDATE messages SET content=COALESCE(?, content), status=?, error=? WHERE id=?",
            (
                result.content,
                result.message_status,
                result.error if result.message_status == "failed" else None,
                message_id,
            ),
        )
        # Tool calls still running were abandoned (interrupted or timed out in a thread).
        connection.execute(
            """UPDATE tool_calls SET status='error', result_summary='提问已结束，工具未完成'
            WHERE run_id=? AND status='running'""",
            (run_id,),
        )
        for citation in result.citations:
            location = Location(**citation.location)
            # The chunk or document may have been deleted during the run; the snapshot stays.
            connection.execute(
                """INSERT INTO citations(id, message_id, marker, chunk_id, document_id, filename,
                    location_label, content, location_json)
                VALUES (?, ?, ?, (SELECT id FROM chunks WHERE id=?),
                    (SELECT id FROM documents WHERE id=?), ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    message_id,
                    citation.marker,
                    citation.chunk_id,
                    citation.document_id,
                    citation.filename,
                    location.label,
                    citation.content,
                    location.model_dump_json(),
                ),
            )
        connection.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (run["conversation_id"],),
        )
    return True


def interrupt_run(
    db: Database,
    run_id: str,
    *,
    content: str | None,
    step_count: int,
    usage: Usage,
    degraded: bool = False,
    model_retries: int = 0,
) -> bool:
    return finish_run(
        db,
        run_id,
        RunResult(
            run_status="failed",
            stop_reason="interrupted",
            message_status="failed",
            content=content,
            step_count=step_count,
            usage=usage,
            error=INTERRUPTED_MESSAGE,
            degraded=degraded,
            model_retries=model_retries,
        ),
    )


def get_run(db: Database, user_id: str, run_id: str) -> RunOut:
    with db.connect() as connection:
        run = connection.execute(
            """SELECT r.* FROM runs r JOIN conversations c ON c.id = r.conversation_id
            WHERE r.id=? AND c.user_id=?""",
            (run_id, user_id),
        ).fetchone()
        if run is None:
            raise AppError(404, "run_not_found", "运行记录不存在")
        calls = connection.execute(
            "SELECT * FROM tool_calls WHERE run_id=? ORDER BY step, created_at, rowid", (run_id,)
        ).fetchall()
    return RunOut(
        id=run["id"],
        conversation_id=run["conversation_id"],
        user_message_id=run["user_message_id"],
        assistant_message_id=run["assistant_message_id"],
        knowledge_base_ids=json.loads(run["knowledge_base_ids"]),
        status=run["status"],
        stop_reason=run["stop_reason"],
        step_count=run["step_count"],
        model=run["model"],
        prompt_tokens=run["prompt_tokens"],
        completion_tokens=run["completion_tokens"],
        error=run["error"],
        dropped_citations=json.loads(run["dropped_citations"]),
        started_at=run["started_at"],
        finished_at=run["finished_at"],
        tool_calls=[
            ToolCallOut(
                id=call["id"],
                step=call["step"],
                tool_name=call["tool_name"],
                arguments=json.loads(call["arguments"]),
                status=call["status"],
                result_summary=call["result_summary"],
                error=(
                    ToolError(
                        code=call["error_code"],
                        message=call["error_message"] or "",
                        retryable=bool(call["retryable"]),
                    )
                    if call["error_code"]
                    else None
                ),
                duration_ms=call["duration_ms"],
                created_at=call["created_at"],
                attempt=call["attempt"],
                retry_of=call["retry_of"],
                fallback=call["fallback"],
            )
            for call in calls
        ],
        degraded=bool(run["degraded"]),
        model_retries=run["model_retries"],
    )
