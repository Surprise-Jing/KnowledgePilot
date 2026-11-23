"""Knowledge base routes. Owner: A. Contract: docs/contracts.md §3.1–§3.2."""

from fastapi import APIRouter

from app.deps import DB, User
from app.schemas import KnowledgeBaseCreate, KnowledgeBaseOut, KnowledgeBaseUpdate
from app.services import knowledge_bases as service

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])


@router.post("", response_model=KnowledgeBaseOut, status_code=201)
def create_knowledge_base(payload: KnowledgeBaseCreate, user_id: User, db: DB):
    return service.create_knowledge_base(db, user_id, payload.name)


@router.get("", response_model=list[KnowledgeBaseOut])
def list_knowledge_bases(user_id: User, db: DB):
    with db.connect() as connection:
        return service.list_knowledge_bases(connection, user_id)


@router.patch("/{kb_id}", response_model=KnowledgeBaseOut)
def rename_knowledge_base(kb_id: str, payload: KnowledgeBaseUpdate, user_id: User, db: DB):
    return service.rename_knowledge_base(db, user_id, kb_id, payload.name)


@router.delete("/{kb_id}", status_code=204)
def delete_knowledge_base(kb_id: str, user_id: User, db: DB):
    service.delete_knowledge_base(db, user_id, kb_id)
