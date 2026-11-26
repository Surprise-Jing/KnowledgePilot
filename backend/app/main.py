"""Application factory: configuration, error handling, middleware and router wiring.
Owner: Tech Lead. Feature routes live in app/api/*."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, conversations, documents, folders, knowledge_bases, search
from app.config import Settings
from app.db import Database
from app.errors import AppError
from app.logging_setup import RequestContextMiddleware, configure_logging
from app.parsers import registry
from app.services import artifacts
from app.services.auth import LoginThrottle

logger = logging.getLogger(__name__)


def _error_response(error: AppError) -> JSONResponse:
    return JSONResponse(status_code=error.status, content={"error": error.to_dict()})


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    db = Database(settings.data_dir / "knowledge.db")
    upload_dir = settings.data_dir / "uploads"

    @asynccontextmanager
    async def lifespan(app):
        db.initialize()
        upload_dir.mkdir(parents=True, exist_ok=True)
        # Staging directories of parses interrupted by a restart (their documents were just
        # marked failed by initialize()).
        artifacts.cleanup_staging(app.state.artifacts_root)
        yield
        # Stop the parse worker process so it does not outlive the app.
        app.state.parsers.close()

    app = FastAPI(title="KnowledgePilot API", version="0.3.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.upload_dir = upload_dir
    # PDF/DOCX go to a lazily started worker process (docs/contracts.md §11); tests replace
    # build_registry with offline parsers.
    app.state.parsers = registry.build_registry(settings)
    app.state.artifacts_root = artifacts.artifacts_root(settings)
    # None selects the default OpenAI-compatible client; tests inject a scripted one
    # (docs/contracts.md §5.3).
    app.state.llm_factory = None
    app.state.login_throttle = LoginThrottle()
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        if exc.status >= 500:
            logger.warning("app error %s: %s", exc.code, exc.message)
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        details = [
            {"loc": list(error.get("loc", ())), "msg": error.get("msg"), "type": error.get("type")}
            for error in exc.errors()
        ]
        return _error_response(AppError(422, "invalid_argument", "请求参数不合法", details=details))

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return _error_response(AppError(500, "internal_error", "服务内部错误，请稍后重试"))

    @app.get("/health")
    def health():
        return {"status": "ok", "answer_mode": settings.answer_mode}

    for module in (auth, knowledge_bases, folders, documents, search, conversations):
        app.include_router(module.router)

    # Mounted last so API routes take precedence. Missing build: API-only mode (dev uses Vite).
    if (settings.frontend_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    else:
        logger.info("frontend build not found at %s; serving API only", settings.frontend_dir)

    return app


app = create_app()
