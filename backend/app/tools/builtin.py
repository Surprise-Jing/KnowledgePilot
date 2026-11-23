"""The four knowledge tools (docs/contracts.md §5.2). Owner: B.

Tools only call the service layer, which checks ownership; they additionally restrict access
to the knowledge bases selected for the question (`allowed_kb_ids`)."""

import logging
from typing import Any, ClassVar

from app.errors import AppError
from app.schemas import SearchRequest
from app.services.access import require_document, require_knowledge_base
from app.services.documents import list_files, read_document_range
from app.services.retrieval import search
from app.tools.context import CitationSource, ToolContext

logger = logging.getLogger(__name__)


def _out_of_scope() -> AppError:
    return AppError(403, "out_of_scope", "该知识库不在本次提问选择的范围内，无法访问")


def _require_in_scope(ctx: ToolContext, kb_id: str) -> None:
    if kb_id not in ctx.allowed_kb_ids:
        raise _out_of_scope()


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
        hits = []
        for hit in search(ctx.db, ctx.user_id, request, ctx.settings):
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
                    "location": location,
                    "score": hit.score,
                    "content": hit.content,
                }
            )
        return {"hits": hits}

    def summarize(self, data: dict) -> str:
        hits = data["hits"]
        if not hits:
            return "未找到相关片段"
        files = len({hit["document_id"] for hit in hits})
        return f"命中 {len(hits)} 个片段（{files} 个文件）"


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
                ),
            )
            chunks.append({"marker": marker, "ordinal": chunk.ordinal, "location": location})
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

    def summarize(self, data: dict) -> str:
        summary = f"读取《{data['filename']}》{len(data['chunks'])} 个片段（共 {data['total_chunks']} 个）"
        return summary + "，未读完" if data["truncated"] else summary
