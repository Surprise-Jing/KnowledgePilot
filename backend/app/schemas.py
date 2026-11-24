"""Shared request/response models. Owner: Tech Lead; the contract is docs/contracts.md.

PATCH models distinguish "field omitted" from "field set to null" with `model_fields_set`:
`{"parent_id": null}` moves a folder to the root, while `{}` leaves it where it is."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

LocationKind = Literal["txt_lines", "pdf_page", "docx_paragraphs", "docx_table_rows"]
LOCATION_UNITS = {
    "txt_lines": "行",
    "pdf_page": "页",
    "docx_paragraphs": "段",
    "docx_table_rows": "行",
}


class Location(BaseModel):
    """Where a chunk comes from. `start`/`end` are 1-based and inclusive; their unit depends on
    `kind`: TXT lines, a PDF page, DOCX paragraphs, or rows of DOCX table `table`.
    `char_start`/`char_end` (0-based, end exclusive) are set only when one long line, page,
    paragraph or row was split into several chunks."""

    kind: LocationKind
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    table: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    char_start: int | None = None
    char_end: int | None = None

    @computed_field
    @property
    def label(self) -> str:
        unit = LOCATION_UNITS[self.kind]
        span = (
            f"第 {self.start} {unit}"
            if self.start == self.end
            else (f"第 {self.start}–{self.end} {unit}")
        )
        if self.kind == "docx_table_rows":
            span = f"表 {self.table} {span}"
        label = " › ".join([*self.heading_path, span])
        if self.char_start is not None:
            label += f"，字符 {self.char_start + 1}–{self.char_end}"
        return label

    @classmethod
    def from_row(cls, row) -> "Location":
        return cls(
            kind=row["loc_kind"],
            start=row["loc_start"],
            end=row["loc_end"],
            table=row["loc_table"],
            heading_path=json.loads(row["heading_path"]),
            char_start=row["char_start"],
            char_end=row["char_end"],
        )


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


def _strip_required(value: str, message: str) -> str:
    if not value.strip():
        raise ValueError(message)
    return value.strip()


class KnowledgeBaseUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value):
        return _strip_required(value, "名称不能为空")


class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    created_at: str
    updated_at: str


NAME_PATTERN = r"^[^/\\]+$"


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=NAME_PATTERN)
    parent_id: str | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value):
        return _strip_required(value, "名称不能为空")


class FolderUpdate(BaseModel):
    """Rename and/or move. Omitted fields are unchanged; `parent_id: null` means root."""

    name: str | None = Field(default=None, min_length=1, max_length=100, pattern=NAME_PATTERN)
    parent_id: str | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value):
        return None if value is None else _strip_required(value, "名称不能为空")

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("至少提供 name 或 parent_id")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("名称不能为空")
        return self


class FolderOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    knowledge_base_id: str
    parent_id: str | None
    name: str
    created_at: str
    updated_at: str


class DocumentUpdate(BaseModel):
    """Rename and/or move within the same knowledge base. `folder_id: null` means root."""

    filename: str | None = Field(default=None, min_length=1, max_length=255, pattern=NAME_PATTERN)
    folder_id: str | None = None

    @field_validator("filename")
    @classmethod
    def strip_filename(cls, value):
        return None if value is None else _strip_required(value, "文件名不能为空")

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("至少提供 filename 或 folder_id")
        if "filename" in self.model_fields_set and self.filename is None:
            raise ValueError("文件名不能为空")
        return self


class DocumentOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    knowledge_base_id: str
    folder_id: str | None
    filename: str
    file_type: Literal["txt", "pdf", "docx"]
    size_bytes: int
    status: Literal["queued", "parsing", "success", "failed"]
    error: str | None
    created_at: str
    updated_at: str
    # True when an upload matched existing content in this knowledge base and the existing
    # record was returned instead of creating a new one.
    duplicate: bool = False


class ChunkOut(BaseModel):
    id: str
    ordinal: int
    content: str
    location: Location


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    knowledge_base_ids: list[str] = Field(min_length=1, max_length=20)
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    knowledge_base_id: str
    filename: str
    content: str
    location: Location
    score: float


class AnswerOut(BaseModel):
    answer: str
    mode: str
    citations: list[SearchHit]


# ---- Service-level models shared by HTTP, tools and MCP (docs/contracts.md §3.4) ----


class FileEntry(BaseModel):
    document_id: str
    knowledge_base_id: str
    folder_id: str | None
    # Folder names from the root, e.g. ["方案", "2024"]; empty for the knowledge base root.
    folder_path: list[str]
    filename: str
    file_type: Literal["txt", "pdf", "docx"]
    status: Literal["queued", "parsing", "success", "failed"]
    error: str | None


class FileListing(BaseModel):
    files: list[FileEntry]
    total: int
    offset: int
    limit: int


class DocumentRange(BaseModel):
    """A contiguous run of chunks of one document, bounded by `max_chars`."""

    document_id: str
    knowledge_base_id: str
    filename: str
    chunks: list[ChunkOut]
    truncated: bool
    # Ordinal to pass as start_ordinal to continue reading; None when the end was reached.
    next_ordinal: int | None
    total_chunks: int


# ---- Tools (docs/contracts.md §5) ----


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None


# ---- Conversations, runs and citations (docs/contracts.md §3.3) ----


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=100)


class ConversationOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    created_at: str
    updated_at: str


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    knowledge_base_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value):
        return _strip_required(value, "问题不能为空")


class CitationOut(BaseModel):
    marker: str
    chunk_id: str | None
    document_id: str | None
    filename: str
    location: Location | None
    location_label: str
    content: str


class MessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    status: Literal["streaming", "completed", "failed"]
    error: str | None
    created_at: str
    # Set on assistant messages: the run that produced the answer.
    run_id: str | None = None
    citations: list[CitationOut] = Field(default_factory=list)


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


StopReason = Literal["answered", "max_steps", "model_error", "timeout", "interrupted"]
# Degradation applied to a tool call (docs/contracts.md §10).
FallbackKind = Literal["bm25", "retrieved_snippets"]


class ToolCallOut(BaseModel):
    id: str
    step: int
    tool_name: str
    arguments: dict[str, Any]
    status: Literal["running", "success", "error"]
    result_summary: str | None
    error: ToolError | None
    duration_ms: int | None
    created_at: str
    # Automatic retry: attempt 2 links to the first call through retry_of.
    attempt: int = 1
    retry_of: str | None = None
    fallback: FallbackKind | None = None


class RunOut(BaseModel):
    id: str
    conversation_id: str
    user_message_id: str
    assistant_message_id: str | None
    knowledge_base_ids: list[str]
    status: Literal["running", "completed", "failed"]
    stop_reason: StopReason | None
    step_count: int
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    error: str | None
    dropped_citations: list[str]
    started_at: str
    finished_at: str | None
    tool_calls: list[ToolCallOut]
    degraded: bool = False
    model_retries: int = 0


# ---- SSE event payloads (docs/contracts.md §4); `event:` names are the class docstrings ----


class RunStartedEvent(BaseModel):
    """run_started"""

    run_id: str
    conversation_id: str
    user_message_id: str
    assistant_message_id: str


class ToolCallEvent(BaseModel):
    """tool_call"""

    call_id: str
    step: int
    name: str
    arguments: dict[str, Any]
    attempt: int = 1
    retry_of: str | None = None


class ToolResultEvent(BaseModel):
    """tool_result"""

    call_id: str
    status: Literal["success", "error"]
    summary: str
    error: ToolError | None = None
    duration_ms: int
    fallback: FallbackKind | None = None


class TokenEvent(BaseModel):
    """token"""

    delta: str


class CitationEvent(CitationOut):
    """citation"""


class ErrorEvent(BaseModel):
    """error"""

    code: str
    message: str
    retryable: bool = False


class Usage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class DoneEvent(BaseModel):
    """done"""

    run_id: str
    stop_reason: StopReason
    step_count: int
    usage: Usage
    degraded: bool = False
