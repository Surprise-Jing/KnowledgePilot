"""Shared FastAPI dependencies. Routers read settings and the database from app.state, so
create_app(settings) in tests gets an isolated instance."""

from pathlib import Path
from typing import Annotated

from fastapi import Depends, Header, Request

from app.config import Settings
from app.db import Database
from app.services.access import ensure_user


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_upload_dir(request: Request) -> Path:
    return request.app.state.upload_dir


def current_user(
    request: Request,
    x_user_id: Annotated[
        str | None, Header(description="用户标识；缺省时使用 PK_DEFAULT_USER_ID")
    ] = None,
) -> str:
    settings = get_settings(request)
    with get_db(request).connect() as connection:
        return ensure_user(connection, x_user_id or settings.default_user_id)


AppSettings = Annotated[Settings, Depends(get_settings)]
DB = Annotated[Database, Depends(get_db)]
UploadDir = Annotated[Path, Depends(get_upload_dir)]
User = Annotated[str, Depends(current_user)]
