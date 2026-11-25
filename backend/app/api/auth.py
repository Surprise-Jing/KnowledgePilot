"""Register, login, logout, the current user and account management (docs/contracts.md §15). The session token
travels only in an HttpOnly cookie, never in a response body."""

from typing import Annotated

from fastapi import APIRouter, Cookie, Response

from app.deps import DB, AppSettings, ArtifactsRoot, LoginThrottleDep, User
from app.errors import AppError
from app.schemas import (
    Credentials,
    PasswordChange,
    PasswordConfirmation,
    UsernameChange,
    UserOut,
)
from app.services import auth as service

router = APIRouter(prefix="/api/auth", tags=["auth"])

SessionToken = Annotated[str | None, Cookie(alias=service.SESSION_COOKIE)]


def _set_session_cookie(response: Response, token: str, settings) -> None:
    response.set_cookie(
        service.SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_days * 24 * 3600,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def _start_session(response: Response, db, settings, user_id: str, previous: str | None):
    with db.connect() as connection:
        # Logging in again in the same browser replaces, rather than piles up, sessions.
        service.delete_session(connection, previous)
        token = service.create_session(connection, user_id, settings.session_ttl_days)
    _set_session_cookie(response, token, settings)


@router.post("/register", response_model=UserOut, status_code=201)
def register(
    payload: Credentials,
    response: Response,
    db: DB,
    settings: AppSettings,
    token: SessionToken = None,
):
    if not settings.allow_registration:
        raise AppError(403, "registration_closed", "当前未开放注册，请联系管理员")
    user = service.register(db, payload.username, payload.password)
    _start_session(response, db, settings, user["id"], token)
    return user


@router.post("/login", response_model=UserOut)
def login(
    payload: Credentials,
    response: Response,
    db: DB,
    settings: AppSettings,
    throttle: LoginThrottleDep,
    token: SessionToken = None,
):
    user = service.authenticate(db, throttle, payload.username, payload.password)
    _start_session(response, db, settings, user["id"], token)
    return user


def _clear_session_cookie(response: Response, settings) -> None:
    response.delete_cookie(
        service.SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


@router.post("/logout", status_code=204)
def logout(response: Response, db: DB, settings: AppSettings, token: SessionToken = None):
    with db.connect() as connection:
        service.delete_session(connection, token)
    _clear_session_cookie(response, settings)


@router.get("/me", response_model=UserOut)
def me(user_id: User, db: DB):
    with db.connect() as connection:
        user = service.get_user(connection, user_id)
    if user is None:
        # The session cascade-deletes with the user, so this only happens mid-deletion.
        raise AppError(401, "unauthenticated", "请先登录")
    return user


@router.patch("/me", response_model=UserOut)
def change_username(payload: UsernameChange, user_id: User, db: DB, throttle: LoginThrottleDep):
    return service.change_username(
        db, throttle, user_id, payload.current_password, payload.username
    )


@router.post("/password", status_code=204)
def change_password(
    payload: PasswordChange,
    user_id: User,
    db: DB,
    throttle: LoginThrottleDep,
    token: SessionToken = None,
):
    service.change_password(
        db,
        throttle,
        user_id,
        payload.current_password,
        payload.new_password,
        keep_token=token,
    )


@router.delete("/me", status_code=204)
def delete_account(
    payload: PasswordConfirmation,
    response: Response,
    user_id: User,
    db: DB,
    settings: AppSettings,
    throttle: LoginThrottleDep,
    artifacts_root: ArtifactsRoot,
):
    service.delete_account(db, throttle, user_id, payload.password, artifacts_root=artifacts_root)
    _clear_session_cookie(response, settings)
