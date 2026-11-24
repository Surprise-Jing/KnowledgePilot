import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 4

# Schema v1. Statements run one by one inside the migration transaction
# (executescript would commit implicitly). The table design is documented in docs/data-model.md.
SCHEMA_V1 = [
    """CREATE TABLE users (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE knowledge_bases (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX idx_knowledge_bases_user ON knowledge_bases(user_id)",
    """CREATE TABLE folders (
        id TEXT PRIMARY KEY,
        knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
        parent_id TEXT REFERENCES folders(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (parent_id IS NULL OR parent_id <> id)
    )""",
    # Sibling names are unique; COALESCE makes root-level folders collide as well.
    """CREATE UNIQUE INDEX idx_folders_sibling_name
        ON folders(knowledge_base_id, COALESCE(parent_id, ''), name)""",
    "CREATE INDEX idx_folders_parent ON folders(parent_id)",
    """CREATE TABLE documents (
        id TEXT PRIMARY KEY,
        knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
        folder_id TEXT REFERENCES folders(id) ON DELETE CASCADE,
        filename TEXT NOT NULL,
        file_type TEXT NOT NULL CHECK (file_type IN ('txt','pdf','docx')),
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        sha256 TEXT NOT NULL,
        path TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('queued','parsing','success','failed')),
        error TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (knowledge_base_id, sha256)
    )""",
    "CREATE INDEX idx_documents_folder ON documents(knowledge_base_id, folder_id)",
    # Location columns are typed so tools can read by range: the unit of loc_start/loc_end
    # depends on loc_kind (line, page, paragraph or table row, all 1-based and inclusive).
    """CREATE TABLE chunks (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL,
        content TEXT NOT NULL,
        loc_kind TEXT NOT NULL
            CHECK (loc_kind IN ('txt_lines','pdf_page','docx_paragraphs','docx_table_rows')),
        loc_start INTEGER NOT NULL CHECK (loc_start >= 1),
        loc_end INTEGER NOT NULL,
        loc_table INTEGER,
        heading_path TEXT NOT NULL DEFAULT '[]',
        char_start INTEGER,
        char_end INTEGER,
        UNIQUE (document_id, ordinal),
        CHECK (loc_end >= loc_start),
        CHECK ((char_start IS NULL) = (char_end IS NULL)),
        CHECK (char_start IS NULL OR (char_start >= 0 AND char_end > char_start))
    )""",
    """CREATE TABLE conversations (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX idx_conversations_user ON conversations(user_id, updated_at)",
    """CREATE TABLE messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('user','assistant')),
        content TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK (status IN ('streaming','completed','failed')),
        error TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at)",
    # One run is one question: the Agent loop, its tool calls and its answer.
    """CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        user_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        assistant_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
        knowledge_base_ids TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
        stop_reason TEXT
            CHECK (stop_reason IN ('answered','max_steps','model_error','timeout','interrupted')),
        step_count INTEGER NOT NULL DEFAULT 0,
        model TEXT,
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        error TEXT,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TEXT
    )""",
    "CREATE INDEX idx_runs_conversation ON runs(conversation_id, started_at)",
    """CREATE TABLE tool_calls (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        step INTEGER NOT NULL CHECK (step >= 1),
        tool_name TEXT NOT NULL,
        arguments TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('running','success','error')),
        result_summary TEXT,
        error_code TEXT,
        error_message TEXT,
        retryable INTEGER CHECK (retryable IN (0, 1)),
        duration_ms INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX idx_tool_calls_run ON tool_calls(run_id, step)",
    # Citations keep a snapshot: reparsing regenerates chunk ids and deletion removes them,
    # but an old answer must still show what it quoted.
    """CREATE TABLE citations (
        id TEXT PRIMARY KEY,
        message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        marker TEXT NOT NULL,
        chunk_id TEXT REFERENCES chunks(id) ON DELETE SET NULL,
        document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
        filename TEXT NOT NULL,
        location_label TEXT NOT NULL,
        content TEXT NOT NULL,
        UNIQUE (message_id, marker)
    )""",
]

LEGACY_TABLES = ("chunks", "documents", "knowledge_bases")
LEGACY_USER_ID = "demo"
UPGRADE_ERROR = "数据模型已升级，请重新解析"


def _table_exists(connection, name):
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _migrate_v1(connection):
    """Create schema v1. A database from the day-one prototype (no user_version) is rebuilt:
    knowledge bases move to the demo user, documents are kept with their files and marked
    for reparsing because the old text locations cannot be converted to typed columns."""
    legacy = _table_exists(connection, "documents")
    if legacy:
        connection.execute("DROP INDEX IF EXISTS idx_documents_kb")
        for table in LEGACY_TABLES:
            connection.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}")
    for statement in SCHEMA_V1:
        connection.execute(statement)
    if not legacy:
        return
    connection.execute(
        "INSERT INTO users(id, name) VALUES (?, ?)", (LEGACY_USER_ID, LEGACY_USER_ID)
    )
    connection.execute(
        """INSERT INTO knowledge_bases(id, user_id, name, created_at, updated_at)
        SELECT id, ?, name, created_at, created_at FROM legacy_knowledge_bases""",
        (LEGACY_USER_ID,),
    )
    for row in connection.execute("SELECT * FROM legacy_documents").fetchall():
        path = Path(row["path"])
        connection.execute(
            """INSERT INTO documents
            (id, knowledge_base_id, filename, file_type, size_bytes, sha256, path, status, error,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?)""",
            (
                row["id"],
                row["knowledge_base_id"],
                row["filename"],
                path.suffix.lower().lstrip("."),
                path.stat().st_size if path.exists() else 0,
                row["sha256"],
                row["path"],
                UPGRADE_ERROR if path.exists() else "原文件缺失，请删除后重新上传",
                row["created_at"],
                row["created_at"],
            ),
        )
    for table in LEGACY_TABLES:
        connection.execute(f"DROP TABLE legacy_{table}")


def _migrate_v2(connection):
    """Agent trace support. Only ADD COLUMN, so existing rows and constraints are untouched:
    - tool_calls.result_json: the truncated structured tool result, for trace and follow-ups
    - runs.dropped_citations: markers the model cited that no tool returned in this run
    - citations.location_json: the structured Location, so the UI can render it like search hits
    """
    connection.execute("ALTER TABLE tool_calls ADD COLUMN result_json TEXT")
    connection.execute("ALTER TABLE runs ADD COLUMN dropped_citations TEXT NOT NULL DEFAULT '[]'")
    connection.execute("ALTER TABLE citations ADD COLUMN location_json TEXT")


def _migrate_v3(connection):
    connection.execute("""CREATE TABLE chunk_embeddings (
        chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
        profile TEXT NOT NULL,
        dimensions INTEGER NOT NULL CHECK (dimensions > 0),
        vector BLOB NOT NULL,
        CHECK (length(vector) = dimensions * 4)
    )""")


def _migrate_v4(connection):
    """Tool failure degradation trace (docs/contracts.md §10). ADD COLUMN only:
    - tool_calls.attempt / retry_of: automatic retries are separate rows linked to the first
    - tool_calls.fallback: the degradation applied to this call (e.g. "bm25")
    - runs.degraded / model_retries: whether any degradation happened in the run
    """
    connection.execute("ALTER TABLE tool_calls ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1")
    connection.execute("ALTER TABLE tool_calls ADD COLUMN retry_of TEXT")
    connection.execute("ALTER TABLE tool_calls ADD COLUMN fallback TEXT")
    connection.execute("ALTER TABLE runs ADD COLUMN degraded INTEGER NOT NULL DEFAULT 0")
    connection.execute("ALTER TABLE runs ADD COLUMN model_retries INTEGER NOT NULL DEFAULT 0")


MIGRATIONS = {1: _migrate_v1, 2: _migrate_v2, 3: _migrate_v3, 4: _migrate_v4}


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def migrate(self):
        """Create or upgrade the schema only. Safe for secondary processes such as the MCP
        server, which must not reset work the web app is doing."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def initialize(self):
        """Migrate, then recover work interrupted by a restart. Only the process that owns
        background parsing and Agent runs (the web app) may call this."""
        self.migrate()
        with self.connect() as connection:
            # Background jobs run in process. Work interrupted by a restart is made retryable
            # instead of staying in an intermediate state forever.
            connection.execute("""
                UPDATE documents SET status='failed', error='服务中断，解析未完成，请重试',
                    updated_at=CURRENT_TIMESTAMP
                WHERE status IN ('queued','parsing')
            """)
            connection.execute("""
                UPDATE runs SET status='failed', stop_reason='interrupted',
                    error='服务中断，回答未完成', finished_at=CURRENT_TIMESTAMP
                WHERE status='running'
            """)
            connection.execute("""
                UPDATE messages SET status='failed', error='服务中断，回答未完成'
                WHERE status='streaming'
            """)

    def _migrate(self):
        # Autocommit mode so the migration controls its own transaction; foreign keys are off
        # while tables are rebuilt and checked before commit.
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = OFF")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"数据库版本 {version} 高于程序支持的版本 {SCHEMA_VERSION}，请升级程序"
                )
            for target in range(version + 1, SCHEMA_VERSION + 1):
                connection.execute("BEGIN IMMEDIATE")
                try:
                    MIGRATIONS[target](connection)
                    if connection.execute("PRAGMA foreign_key_check").fetchone():
                        raise RuntimeError(f"迁移到版本 {target} 后外键校验失败")
                    connection.execute(f"PRAGMA user_version = {target}")
                    connection.execute("COMMIT")
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
        finally:
            connection.close()
