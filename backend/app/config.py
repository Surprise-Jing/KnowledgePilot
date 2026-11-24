from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PK_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    # Built frontend (npm run build). Served at "/" when index.html exists.
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    # Used when a request carries no X-User-Id header (local single-user demo).
    default_user_id: str = "demo"
    answer_mode: Literal["extractive", "openai"] = "extractive"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout: float = Field(default=45, gt=0)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    log_level: str = "INFO"
    # Set False when the model service does not support streamed tool calls.
    llm_stream: bool = True
    # Agent loop (docs/contracts.md §5).
    agent_max_steps: int = Field(default=6, ge=1, le=20)
    tool_timeout: float = Field(default=15, gt=0)
    context_budget_chars: int = Field(default=24000, ge=2000)
    # Retrieval strategy, switchable for evaluation (docs/tasks.md Round 2).
    retriever: Literal["cosine", "bm25", "vector", "hybrid"] = "cosine"
    embedding_base_url: str = ""
    embedding_model: str = "bge-m3"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_dimensions: int = Field(default=1024, ge=1, le=65536)
    embedding_timeout: float = Field(default=45, gt=0)
    # Query embedding during search. Must stay below tool_timeout so a hanging embedding
    # service surfaces as embedding_timeout (BM25 fallback) instead of tool_timeout.
    embedding_query_timeout: float = Field(default=8, gt=0)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    # Change this when a provider replaces weights behind the same model name.
    embedding_revision: str = "1"
    retrieval_candidates: int = Field(default=50, ge=10, le=1000)
    # User identity for the stdio MCP server, which has no HTTP headers.
    mcp_user_id: str = "demo"
