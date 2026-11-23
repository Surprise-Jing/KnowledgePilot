"""Conversation, streaming message and run trace routes. Owner: B.
Contract: docs/contracts.md §3.3 and §4."""

import asyncio
import logging
from collections.abc import Callable

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from app.agent.loop import RunSetup
from app.agent.sse import HEARTBEAT_SECONDS, stream_run
from app.deps import DB, AppSettings, User
from app.llm.factory import default_llm_factory
from app.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    MessageCreate,
    RunOut,
    Usage,
)
from app.services import conversations
from app.services.access import require_knowledge_bases

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class SSEResponse(StreamingResponse):
    media_type = "text/event-stream"

    def __init__(self, content, *, on_close: Callable[[], None], **kwargs):
        super().__init__(content, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # With ASGI spec >= 2.4 a disconnect surfaces as a failed send and the body
            # iterator is left suspended; closing it runs its cleanup (cancel and persist the
            # run) now instead of whenever it is garbage collected.
            await self.body_iterator.aclose()
            # If the body never started, nothing else would finish the run and the
            # conversation would stay busy. Idempotent for runs that already finished.
            try:
                self._on_close()
            except Exception:
                logger.exception("cannot finalize run after the stream closed")


@router.post("/api/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(payload: ConversationCreate, user_id: User, db: DB):
    return conversations.create_conversation(db, user_id, payload.title)


@router.get("/api/conversations", response_model=list[ConversationOut])
def list_conversations(user_id: User, db: DB):
    return conversations.list_conversations(db, user_id)


@router.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str, user_id: User, db: DB):
    return conversations.get_conversation(db, user_id, conversation_id)


@router.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, user_id: User, db: DB):
    conversations.delete_conversation(db, user_id, conversation_id)
    return Response(status_code=204)


def _validate_scope(db, user_id: str, conversation_id: str, kb_ids: list[str]) -> list[str]:
    with db.connect() as connection:
        conversations.require_conversation(connection, user_id, conversation_id)
        return require_knowledge_bases(connection, user_id, kb_ids)


@router.post(
    "/api/conversations/{conversation_id}/messages",
    response_class=SSEResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "SSE 事件流"}},
)
async def post_message(
    conversation_id: str,
    payload: MessageCreate,
    request: Request,
    user_id: User,
    db: DB,
    settings: AppSettings,
):
    # Everything that can fail before streaming is checked here, so those errors are ordinary
    # JSON responses: ownership, model configuration and the one-running-run rule.
    kb_ids = await asyncio.to_thread(
        _validate_scope, db, user_id, conversation_id, payload.knowledge_base_ids
    )
    factory = request.app.state.llm_factory or default_llm_factory
    llm = factory(settings)
    started = await asyncio.to_thread(
        conversations.start_run, db, user_id, conversation_id, payload.content, kb_ids, llm.model
    )
    setup = RunSetup(
        db=db,
        settings=settings,
        llm=llm,
        user_id=user_id,
        question=payload.content,
        knowledge_base_ids=kb_ids,
        started=started,
    )
    # Tests shorten the heartbeat through app.state; production uses the contract's 15 s.
    heartbeat = getattr(request.app.state, "sse_heartbeat_seconds", HEARTBEAT_SECONDS)

    def close_run() -> None:
        conversations.interrupt_run(db, started.run_id, content=None, step_count=0, usage=Usage())

    return SSEResponse(stream_run(setup, heartbeat), headers=SSE_HEADERS, on_close=close_run)


@router.get("/api/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, user_id: User, db: DB):
    return conversations.get_run(db, user_id, run_id)
