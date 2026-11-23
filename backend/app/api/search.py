"""Search and legacy single-turn chat routes. Owner: B."""

from fastapi import APIRouter

from app.deps import DB, AppSettings, User
from app.schemas import AnswerOut, SearchHit, SearchRequest
from app.services.answering import answer
from app.services.retrieval import search

router = APIRouter(tags=["search"])


@router.post("/api/search", response_model=list[SearchHit])
def search_documents(payload: SearchRequest, user_id: User, db: DB, settings: AppSettings):
    return search(db, user_id, payload, settings)


@router.post("/api/chat", response_model=AnswerOut, deprecated=True)
def chat(payload: SearchRequest, user_id: User, db: DB, settings: AppSettings):
    """Legacy retrieve-then-answer endpoint kept for debugging. The Agent uses
    POST /api/conversations/{id}/messages."""
    return answer(settings, payload.query, search(db, user_id, payload, settings))
