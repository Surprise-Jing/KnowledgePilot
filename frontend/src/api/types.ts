// Mirrors backend/app/schemas.py and docs/contracts.md. Keep field names identical.

export type LocationKind = 'txt_lines' | 'pdf_page' | 'docx_paragraphs' | 'docx_table_rows'

export type ParseMode = 'standard' | 'enhanced'

/** A parsed document element a chunk came from. `bbox` is [x0, y0, x1, y1], normalized 0–1,
 * top-left origin; `page` and `bbox` are null for DOCX and TXT. */
export interface SourceElement {
  ref: string
  label: string
  page: number | null
  bbox: [number, number, number, number] | null
}

export interface Location {
  kind: LocationKind
  start: number
  end: number
  table: number | null
  heading_path: string[]
  char_start: number | null
  char_end: number | null
  elements: SourceElement[]
  /** Computed by the backend, e.g. "第 3–5 行". */
  label: string
}

export interface HealthOut {
  status: string
  answer_mode: string
}

// ---- Knowledge bases, folders, documents ----

export interface KnowledgeBaseCreate {
  name: string
}

export interface KnowledgeBaseUpdate {
  name: string
}

export interface KnowledgeBaseOut {
  id: string
  name: string
  created_at: string
  updated_at: string
}

export interface FolderCreate {
  name: string
  parent_id?: string | null
}

/** Omitted fields are unchanged; `parent_id: null` moves the folder to the root. */
export interface FolderUpdate {
  name?: string
  parent_id?: string | null
}

export interface FolderOut {
  id: string
  knowledge_base_id: string
  parent_id: string | null
  name: string
  created_at: string
  updated_at: string
}

export type FileType = 'txt' | 'pdf' | 'docx'
export type DocumentStatus = 'queued' | 'parsing' | 'success' | 'failed'

/** Omitted fields are unchanged; `folder_id: null` moves the document to the root. */
export interface DocumentUpdate {
  filename?: string
  folder_id?: string | null
}

export interface DocumentOut {
  id: string
  knowledge_base_id: string
  folder_id: string | null
  filename: string
  file_type: FileType
  size_bytes: number
  status: DocumentStatus
  error: string | null
  created_at: string
  updated_at: string
  parse_mode: ParseMode
  /** Parser that produced the current chunks; null until the first successful parse. */
  parser: string | null
  page_count: number | null
  duplicate: boolean
}

export interface ReparseRequest {
  parse_mode?: ParseMode | null
}

export interface ChunkOut {
  id: string
  ordinal: number
  content: string
  location: Location
}

export interface SearchRequest {
  query: string
  knowledge_base_ids: string[]
  top_k?: number
  document_ids?: string[] | null
}

export interface SearchHit {
  chunk_id: string
  document_id: string
  knowledge_base_id: string
  filename: string
  content: string
  location: Location
  score: number
}

// ---- Tools ----

export interface ToolError {
  code: string
  message: string
  retryable: boolean
}

// ---- Conversations, runs, citations ----

export interface ConversationCreate {
  title?: string | null
}

export interface ConversationOut {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface MessageCreate {
  content: string
  knowledge_base_ids: string[]
}

export interface CitationOut {
  marker: string
  chunk_id: string | null
  /** Null when the source document has been deleted; the citation keeps a snapshot. */
  document_id: string | null
  filename: string
  location: Location | null
  location_label: string
  content: string
}

export type MessageRole = 'user' | 'assistant'
export type MessageStatus = 'streaming' | 'completed' | 'failed'

export interface MessageOut {
  id: string
  role: MessageRole
  content: string
  status: MessageStatus
  error: string | null
  created_at: string
  run_id: string | null
  citations: CitationOut[]
}

export interface ConversationDetail extends ConversationOut {
  messages: MessageOut[]
}

export type StopReason = 'answered' | 'max_steps' | 'model_error' | 'timeout' | 'interrupted'

export type ToolCallStatus = 'running' | 'success' | 'error'

/** Degradation applied to a tool call (docs/contracts.md §10.2). */
export type FallbackKind = 'bm25' | 'retrieved_snippets'

export interface ToolCallOut {
  id: string
  step: number
  tool_name: string
  arguments: Record<string, unknown>
  status: ToolCallStatus
  result_summary: string | null
  error: ToolError | null
  duration_ms: number | null
  created_at: string
  /** Automatic retry (contracts §10.1): attempt 2 links to the first call through retry_of. */
  attempt: number
  retry_of: string | null
  fallback: FallbackKind | null
}

export interface RunOut {
  id: string
  conversation_id: string
  user_message_id: string
  assistant_message_id: string | null
  knowledge_base_ids: string[]
  status: 'running' | 'completed' | 'failed'
  stop_reason: StopReason | null
  step_count: number
  model: string | null
  prompt_tokens: number | null
  completion_tokens: number | null
  error: string | null
  dropped_citations: string[]
  started_at: string
  finished_at: string | null
  tool_calls: ToolCallOut[]
  /** Any retry, fallback or model retry happened in this run (contracts §10.4). */
  degraded: boolean
  model_retries: number
}

// ---- SSE events (docs/contracts.md §4) ----

export interface RunStartedEvent {
  run_id: string
  conversation_id: string
  user_message_id: string
  assistant_message_id: string
}

export interface ToolCallEvent {
  call_id: string
  step: number
  name: string
  arguments: Record<string, unknown>
  /** Defaults to 1 in schemas.py; optional here so older streams still parse. */
  attempt?: number
  retry_of?: string | null
}

export interface ToolResultEvent {
  call_id: string
  status: 'success' | 'error'
  summary: string
  error?: ToolError | null
  duration_ms: number
  fallback?: FallbackKind | null
}

export interface TokenEvent {
  delta: string
}

export type CitationEvent = CitationOut

export interface ErrorEvent {
  code: string
  message: string
  retryable: boolean
}

export interface Usage {
  prompt_tokens: number | null
  completion_tokens: number | null
}

export interface DoneEvent {
  run_id: string
  stop_reason: StopReason
  step_count: number
  usage: Usage
  degraded?: boolean
}

export type ChatEvent =
  | { type: 'run_started'; data: RunStartedEvent }
  | { type: 'tool_call'; data: ToolCallEvent }
  | { type: 'tool_result'; data: ToolResultEvent }
  | { type: 'token'; data: TokenEvent }
  | { type: 'citation'; data: CitationEvent }
  | { type: 'error'; data: ErrorEvent }
  | { type: 'done'; data: DoneEvent }

export type ChatEventType = ChatEvent['type']

// ---- Errors (docs/contracts.md §2) ----

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    retryable: boolean
    details?: unknown[]
  }
}
