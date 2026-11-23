"""Knowledge base routes. Owner: A."""

from uuid import uuid4

from fastapi import APIRouter

from app.deps import DB, User
from app.schemas import KnowledgeBaseCreate, KnowledgeBaseOut
from app.services.access import require_knowledge_base

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])


@router.post("", response_model=KnowledgeBaseOut, status_code=201)
def create_knowledge_base(payload: KnowledgeBaseCreate, user_id: User, db: DB):
    kb_id = str(uuid4())
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO knowledge_bases(id, user_id, name) VALUES (?, ?, ?)",
            (kb_id, user_id, payload.name),
        )
        return require_knowledge_base(connection, user_id, kb_id)


@router.get("", response_model=list[KnowledgeBaseOut])
def list_knowledge_bases(user_id: User, db: DB):
    with db.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM knowledge_bases WHERE user_id=? ORDER BY created_at, id",
                (user_id,),
            )
        ]
