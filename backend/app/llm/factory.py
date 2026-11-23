"""Default LLM factory used when `app.state.llm_factory` is None. Owner: B."""

from app.config import Settings
from app.errors import AppError
from app.llm.base import LLMClient
from app.llm.openai_compat import OpenAICompatClient


def default_llm_factory(settings: Settings) -> LLMClient:
    if not settings.llm_model or not settings.llm_api_key.get_secret_value():
        raise AppError(
            503,
            "model_not_configured",
            "未配置模型：请在 backend/.env 中设置 PK_LLM_MODEL 和 PK_LLM_API_KEY",
        )
    return OpenAICompatClient(settings)
