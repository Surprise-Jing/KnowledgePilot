"""Shared FastAPI dependencies. Routers read settings and the database from app.state, so
create_app(settings) in tests gets an isolated instance."""

from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, Request

from app.config import Settings
from app.db import Database
from app.parsers.registry import ParserRegistry
from app.services import auth


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_upload_dir(request: Request) -> Path:
    return request.app.state.upload_dir


def get_artifacts_root(request: Request) -> Path:
    return request.app.state.artifacts_root


def get_parsers(request: Request) -> ParserRegistry:
    return request.app.state.parsers


def get_login_throttle(request: Request) -> auth.LoginThrottle:
    return request.app.state.login_throttle


def current_user(
    request: Request,
    session: Annotated[
        str | None, Cookie(alias=auth.SESSION_COOKIE, description="登录会话（HttpOnly Cookie）")
    ] = None,
) -> str:
    """The logged-in user's id; 401 `unauthenticated` without a live session."""
    with get_db(request).connect() as connection:
        return auth.resolve_session(connection, session)


AppSettings = Annotated[Settings, Depends(get_settings)]
DB = Annotated[Database, Depends(get_db)]
UploadDir = Annotated[Path, Depends(get_upload_dir)]
Parsers = Annotated[ParserRegistry, Depends(get_parsers)]
ArtifactsRoot = Annotated[Path, Depends(get_artifacts_root)]
User = Annotated[str, Depends(current_user)]
LoginThrottleDep = Annotated[auth.LoginThrottle, Depends(get_login_throttle)]
