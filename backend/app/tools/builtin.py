"""The four knowledge tools (docs/contracts.md §5.2). Owner: B.

Tools only call the service layer, which checks ownership; they additionally restrict access
to the knowledge bases selected for the question (`allowed_kb_ids`).

Tool-level degradation (contracts §10.2) lives here so the in-app Agent and the MCP server
behave the same: `search_chunks` falls back to BM25 inside `run`, and `read_document` offers
`fallback`, which the registry applies to the final failed attempt of a call."""

import logging
import re
from typing import Any, ClassVar

from app.errors import AppError
from app.schemas import Location, SearchHit, SearchRequest, ToolError
from app.services.access import require_document, require_knowledge_base
from app.services.documents import list_files, read_document_range
from app.services.retrieval import search
from app.tools.context import CitationSource, ToolContext

logger = logging.getLogger(__name__)


def model_location(location: Location) -> dict:
    """Location as the model sees it: page-region elements (bounding boxes) would only cost
    context, so they stay in the citation snapshot for the UI (contracts §11)."""
    return location.model_dump(exclude={"elements"})


def _out_of_scope() -> AppError:
    return AppError(403, "out_of_scope", "该知识库不在本次提问选择的范围内，无法访问")


def _require_in_scope(ctx: ToolContext, kb_id: str) -> None:
    if kb_id not in ctx.allowed_kb_ids:
        raise _out_of_scope()


VECTOR_RETRIEVERS = {"vector", "hybrid"}
BM25_FALLBACK_NOTE = "检索服务异常，已改用关键词检索："
SNIPPETS_FALLBACK_NOTE = "读取原文失败，已改用本次已检索到的片段："
_MARKER_NUMBER = re.compile(r"c(\d+)")


def _search_with_fallback(
    ctx: ToolContext, request: SearchRequest
) -> tuple[list[SearchHit], dict | None]:
    """Vector/hybrid retrieval; when the embedding side fails, the same query and scope run on
    BM25 instead. The service layer itself never degrades (contracts §9); this explicit,
    visible substitution is the only one, and the result says so in `degraded`."""
    try:
        return search(ctx.db, ctx.user_id, request, ctx.settings), None
    except AppError as exc:
        if ctx.settings.retriever not in VECTOR_RETRIEVERS or not exc.code.startswith("embedding_"):
            raise
        logger.warning(
            "search degraded to bm25",
            extra={"reason": exc.code, "retriever": ctx.settings.retriever},
        )
        bm25 = ctx.settings.model_copy(update={"retriever": "bm25"})
        return search(ctx.db, ctx.user_id, request, bm25), {"kind": "bm25", "reason": exc.code}


class ListKnowledgeBasesTool:
    name = "list_knowledge_bases"
    description = (
        "列出本次提问可以使用的知识库及其文档数量。"
        "适用：不清楚有哪些知识库、需要知道知识库 ID 以便调用 list_files 时。"
        "不适用：查找具体内容（请用 search_chunks）。"
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def run(self, ctx: ToolContext, arguments: dict) -> dict:
        knowledge_bases = []
        with ctx.db.connect() as connection:
            for kb_id in dict.fromkeys(ctx.allowed_kb_ids):
                try:
                    kb = require_knowledge_base(connection, ctx.user_id, kb_id)
                except AppError as exc:
                    if exc.code != "knowledge_base_not_found":
                        raise
                    # Deleted after the question was asked: it is simply no longer available.
                    logger.info("knowledge base in scope no longer exists", extra={"kb": kb_id})
                    continue
                listing = list_files(connection, ctx.user_id, kb_id, limit=1)
                knowledge_bases.append(
                    {"id": kb["id"], "name": kb["name"], "document_count": listing.total}
                )
        return {"knowledge_bases": knowledge_bases}

    def summarize(self, data: dict) -> str:
        return f"可用知识库 {len(data['knowledge_bases'])} 个"


class SearchChunksTool:
    name = "search_chunks"
    description = (
        "在知识库中检索与问题相关的文档片段，返回片段内容、来源位置和引用标记（如 c1）。"
        "适用：回答任何需要依据资料的问题时首先使用；换关键词可再次检索。"
        "不适用：需要按目录浏览文件（用 list_files）或连续阅读某个文档的上下文（用 read_document）。"
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": "检索语句，使用问题中的关键词或其同义改写",
            },
            "knowledge_base_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "maxItems": 20,
                "description": "要检索的知识库 ID 列表；省略时检索本次提问选择的全部知识库",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
                "description": "返回的片段数量，1–10，默认 5",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def run(self, ctx: ToolContext, arguments: dict) -> dict:
        query = arguments["query"].strip()
        if not query:
            raise AppError(422, "invalid_argument", "参数 query 不能为空")
        if "knowledge_base_ids" in arguments:
            kb_ids = list(dict.fromkeys(arguments["knowledge_base_ids"]))
            for kb_id in kb_ids:
                _require_in_scope(ctx, kb_id)
        else:
            kb_ids = list(dict.fromkeys(ctx.allowed_kb_ids))
        if not kb_ids:
            return {"hits": []}
        request = SearchRequest(query=query, knowledge_base_ids=kb_ids, top_k=arguments["top_k"])
        results, degraded = _search_with_fallback(ctx, request)
        hits = []
        for hit in results:
            location = hit.location.model_dump()
            marker = ctx.markers.marker_for(
                hit.chunk_id,
                CitationSource(
                    chunk_id=hit.chunk_id,
                    document_id=hit.document_id,
                    filename=hit.filename,
                    location=location,
                    content=hit.content,
                ),
            )
            hits.append(
                {
                    "marker": marker,
                    "chunk_id": hit.chunk_id,
                    "document_id": hit.document_id,
                    "knowledge_base_id": hit.knowledge_base_id,
                    "filename": hit.filename,
                    "location": model_location(hit.location),
                    "score": hit.score,
                    "content": hit.content,
                }
            )
        return {"hits": hits} if degraded is None else {"hits": hits, "degraded": degraded}

    def summarize(self, data: dict) -> str:
        hits = data["hits"]
        if not hits:
            summary = "未找到相关片段"
        else:
            files = len({hit["document_id"] for hit in hits})
            summary = f"命中 {len(hits)} 个片段（{files} 个文件）"
        return BM25_FALLBACK_NOTE + summary if data.get("degraded") else summary


class ListFilesTool:
    name = "list_files"
    description = (
        "按目录列出某个知识库中的文件（文件名、目录路径、解析状态），支持分页。"
        "适用：用户询问有哪些文件、某个目录下有什么，或需要先找到文档 ID 再用 read_document 阅读。"
        "不适用：查找具体内容（用 search_chunks）。"
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "knowledge_base_id": {
                "type": "string",
                "minLength": 1,
                "description": "知识库 ID，可通过 list_knowledge_bases 获得",
            },
            "folder_id": {
                "type": "string",
                "minLength": 1,
                "description": "目录 ID；省略表示从知识库根目录开始",
            },
            "recursive": {
                "type": "boolean",
                "default": True,
                "description": "是否包含子孙目录中的文件，默认 true",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": "分页起始位置，默认 0",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 20,
                "description": "本页最多返回的文件数，1–50，默认 20",
            },
        },
        "required": ["knowledge_base_id"],
        "additionalProperties": False,
    }

    def run(self, ctx: ToolContext, arguments: dict) -> dict:
        kb_id = arguments["knowledge_base_id"]
        _require_in_scope(ctx, kb_id)
        with ctx.db.connect() as connection:
            listing = list_files(
                connection,
                ctx.user_id,
                kb_id,
                folder_id=arguments.get("folder_id"),
                recursive=arguments["recursive"],
                offset=arguments["offset"],
                limit=arguments["limit"],
            )
        return listing.model_dump()

    def summarize(self, data: dict) -> str:
        return f"列出 {len(data['files'])} 个文件（共 {data['total']} 个）"


class ReadDocumentTool:
    name = "read_document"
    description = (
        "按顺序读取某个文档的连续片段，内容中每段前带引用标记（如 [c3]）。"
        "适用：需要某个文档的完整上下文、用户指定了某份文档，或检索片段不足以回答时；"
        "内容未读完时用返回的 next_ordinal 作为 start_ordinal 继续读。"
        "不适用：不知道文档 ID 时（先用 search_chunks 或 list_files）。"
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "minLength": 1,
                "description": "文档 ID，来自 search_chunks 或 list_files 的结果",
            },
            "start_ordinal": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": "从第几个片段开始读（从 0 开始），默认 0",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 500,
                "maximum": 4000,
                "default": 3000,
                "description": "本次最多读取的字符数，500–4000，默认 3000",
            },
        },
        "required": ["document_id"],
        "additionalProperties": False,
    }

    def run(self, ctx: ToolContext, arguments: dict) -> dict:
        with ctx.db.connect() as connection:
            # Ownership first (other users' documents are "not found"), then question scope.
            document = require_document(connection, ctx.user_id, arguments["document_id"])
            _require_in_scope(ctx, document["knowledge_base_id"])
            document_range = read_document_range(
                connection,
                ctx.user_id,
                arguments["document_id"],
                start_ordinal=arguments["start_ordinal"],
                max_chars=arguments["max_chars"],
            )
        chunks, parts = [], []
        for chunk in document_range.chunks:
            location = chunk.location.model_dump()
            marker = ctx.markers.marker_for(
                chunk.id,
                CitationSource(
                    chunk_id=chunk.id,
                    document_id=document_range.document_id,
                    filename=document_range.filename,
                    location=location,
                    content=chunk.content,
                    ordinal=chunk.ordinal,
                ),
            )
            chunks.append(
                {
                    "marker": marker,
                    "ordinal": chunk.ordinal,
                    "location": model_location(chunk.location),
                }
            )
            parts.append(f"[{marker}] {chunk.content}")
        return {
            "document_id": document_range.document_id,
            "filename": document_range.filename,
            "content": "\n\n".join(parts),
            "chunks": chunks,
            "truncated": document_range.truncated,
            "next_ordinal": document_range.next_ordinal,
            "total_chunks": document_range.total_chunks,
        }

    def fallback(self, ctx: ToolContext, arguments: dict, error: ToolError) -> dict | None:
        """After reading failed for good: the snippets of this document that tools already
        returned in this run (or MCP session), or None when there are none. They were already
        shown to this caller under the same scope, so returning them again reveals nothing new;
        no database access, since the database may be what failed."""
        snippets = ctx.markers.sources_for_document(arguments["document_id"])
        if not snippets:
            return None

        def position(item: tuple[str, Any]) -> tuple:
            marker, source = item
            number = _MARKER_NUMBER.fullmatch(marker)
            return (
                source.ordinal is None,
                source.ordinal or 0,
                source.location.get("start", 0),
                source.location.get("char_start") or 0,
                int(number.group(1)) if number else 0,
            )

        snippets.sort(key=position)
        return {
            "document_id": arguments["document_id"],
            "filename": snippets[0][1].filename,
            "content": "\n\n".join(f"[{marker}] {source.content}" for marker, source in snippets),
            "chunks": [
                {
                    "marker": marker,
                    "ordinal": source.ordinal,
                    "location": {k: v for k, v in source.location.items() if k != "elements"},
                }
                for marker, source in snippets
            ],
            # Only previously retrieved snippets: the document was not read in full.
            "truncated": True,
            "next_ordinal": None,
            "total_chunks": None,
            "degraded": {"kind": "retrieved_snippets", "reason": error.code},
        }

    def summarize(self, data: dict) -> str:
        if data.get("degraded"):
            return (
                f"{SNIPPETS_FALLBACK_NOTE}《{data['filename']}》{len(data['chunks'])} 个片段，"
                "原文可能不完整"
            )
        summary = f"读取《{data['filename']}》{len(data['chunks'])} 个片段（共 {data['total_chunks']} 个）"
        return summary + "，未读完" if data["truncated"] else summary
