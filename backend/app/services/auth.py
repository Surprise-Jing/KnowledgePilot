"""Accounts, passwords and login sessions (docs/contracts.md §15).

Passwords are hashed with the standard library's scrypt, so no new dependency is needed.
Sessions are opaque random tokens; only their SHA-256 is stored, so a leaked database cannot
be replayed as a login. Hashing runs outside database connections to keep transactions short."""

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from functools import cache
from pathlib import Path
from uuid import uuid4

from app.db import Database
from app.errors import AppError
from app.services.documents import remove_stored_files

SESSION_COOKIE = "kp_session"
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{3,32}")
PASSWORD_MIN, PASSWORD_MAX = 8, 128

# scrypt cost: 2^14 * 8 * 128 bytes = 16 MiB of memory per hash, tens of milliseconds.
SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN = 2**14, 8, 1, 64

USER_COLUMNS = "id, username, created_at"


def _unauthenticated() -> AppError:
    return AppError(401, "unauthenticated", "请先登录")


def _invalid_credentials() -> AppError:
    return AppError(401, "invalid_credentials", "用户名或密码错误")


def validate_username(username: str) -> str:
    if not USERNAME_PATTERN.fullmatch(username):
        raise AppError(
            400, "invalid_username", "用户名只能包含字母、数字、下划线和连字符，长度 3–32 位"
        )
    return username


def validate_password(password: str) -> str:
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise AppError(400, "weak_password", f"密码长度需为 {PASSWORD_MIN}–{PASSWORD_MAX} 位")
    if not (any(c.isalpha() for c in password) and any(c.isdigit() for c in password)):
        raise AppError(400, "weak_password", "密码需同时包含字母和数字")
    return password


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except ValueError:
        # A malformed stored hash can never match; treat it like a wrong password.
        return False
    return hmac.compare_digest(actual, expected)


@cache
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginThrottle:
    """Per-username failure counter kept in process memory (no Redis): after `limit` failures
    within `window` seconds, further attempts are refused until the oldest failure expires.
    One instance lives on app.state, so tests get a fresh one per app."""

    def __init__(self, limit: int = 5, window: float = 15 * 60, max_keys: int = 10_000):
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        failures = self._failures.get(key, deque())
        while failures and now - failures[0] >= self.window:
            failures.popleft()
        return failures

    def check(self, username: str) -> None:
        key, now = username.lower(), time.monotonic()
        with self._lock:
            if len(self._recent(key, now)) >= self.limit:
                raise AppError(
                    429,
                    "too_many_attempts",
                    "登录失败次数过多，请 15 分钟后再试",
                    retryable=True,
                )

    def record_failure(self, username: str) -> None:
        key, now = username.lower(), time.monotonic()
        with self._lock:
            failures = self._recent(key, now)
            failures.append(now)
            self._failures[key] = failures
            if len(self._failures) > self.max_keys:
                # Bound memory under a flood of distinct usernames: drop idle entries.
                for stale in [k for k, v in self._failures.items() if not self._recent(k, now)]:
                    del self._failures[stale]

    def reset(self, username: str) -> None:
        with self._lock:
            self._failures.pop(username.lower(), None)


def get_user(connection, user_id: str) -> dict | None:
    row = connection.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def find_user(connection, username: str) -> dict | None:
    row = connection.execute(
        f"SELECT {USER_COLUMNS} FROM users WHERE username=? COLLATE NOCASE", (username,)
    ).fetchone()
    return dict(row) if row else None


def require_user_by_username(connection, username: str) -> dict:
    """For local tools (MCP, CLI) that name a user instead of logging in."""
    user = find_user(connection, username)
    if user is None:
        raise AppError(404, "user_not_found", f"用户 {username} 不存在，请先注册")
    return user


def list_users(connection) -> list[dict]:
    rows = connection.execute(
        f"SELECT {USER_COLUMNS}, password_hash IS NOT NULL AS has_password FROM users "
        "ORDER BY created_at, id"
    ).fetchall()
    return [dict(row) for row in rows]


def register(db: Database, username: str, password: str) -> dict:
    validate_username(username)
    validate_password(password)
    password_hash = hash_password(password)
    user_id = str(uuid4())
    try:
        with db.connect() as connection:
            connection.execute(
                "INSERT INTO users(id, name, username, password_hash) VALUES (?, ?, ?, ?)",
                (user_id, username, username, password_hash),
            )
            return get_user(connection, user_id)
    except sqlite3.IntegrityError as exc:
        # The case-insensitive unique index is the only constraint a new row can break, and
        # it also settles two concurrent registrations of the same name.
        raise AppError(409, "username_taken", "用户名已被占用") from exc


def authenticate(db: Database, throttle: LoginThrottle, username: str, password: str) -> dict:
    throttle.check(username)
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT {USER_COLUMNS}, password_hash FROM users WHERE username=? COLLATE NOCASE",
            (username,),
        ).fetchone()
    # Hash even when the user is missing or has no password, so timing does not reveal
    # which usernames exist.
    stored = row["password_hash"] if row and row["password_hash"] else _dummy_hash()
    if not verify_password(password, stored) or row is None or not row["password_hash"]:
        throttle.record_failure(username)
        raise _invalid_credentials()
    throttle.reset(username)
    return {key: row[key] for key in ("id", "username", "created_at")}


def create_session(connection, user_id: str, ttl_days: int) -> str:
    token = secrets.token_urlsafe(32)
    connection.execute(
        "DELETE FROM sessions WHERE user_id=? AND expires_at <= CURRENT_TIMESTAMP", (user_id,)
    )
    connection.execute(
        """INSERT INTO sessions(id, user_id, token_hash, expires_at)
        VALUES (?, ?, ?, datetime('now', ?))""",
        (str(uuid4()), user_id, _token_hash(token), f"+{ttl_days} days"),
    )
    return token


def resolve_session(connection, token: str | None) -> str:
    """Return the user id of a live session, or raise `unauthenticated`."""
    if not token:
        raise _unauthenticated()
    row = connection.execute(
        "SELECT user_id FROM sessions WHERE token_hash=? AND expires_at > CURRENT_TIMESTAMP",
        (_token_hash(token),),
    ).fetchone()
    if row is None:
        raise _unauthenticated()
    return row["user_id"]


def delete_session(connection, token: str | None) -> None:
    if token:
        connection.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


def revoke_other_sessions(connection, user_id: str, keep_token: str | None) -> int:
    """End every session of the user except the one making the request (password change)."""
    cursor = connection.execute(
        "DELETE FROM sessions WHERE user_id=? AND token_hash IS NOT ?",
        (user_id, _token_hash(keep_token) if keep_token else None),
    )
    return cursor.rowcount


def verify_current_password(
    db: Database, throttle: LoginThrottle, user_id: str, password: str
) -> dict:
    """Re-check the password of a logged-in user before a sensitive change. Failures count
    toward the same per-username limit as logins, so a stolen session cannot be used to
    guess the password faster."""
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT {USER_COLUMNS}, password_hash FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if row is None:
        raise _unauthenticated()
    key = row["username"] or user_id
    throttle.check(key)
    if not row["password_hash"] or not verify_password(password, row["password_hash"]):
        throttle.record_failure(key)
        raise AppError(403, "wrong_password", "当前密码不正确")
    throttle.reset(key)
    return {name: row[name] for name in ("id", "username", "created_at")}


def change_password(
    db: Database,
    throttle: LoginThrottle,
    user_id: str,
    current: str,
    new: str,
    *,
    keep_token: str | None,
) -> None:
    """Replace the password and sign out every other session; the current one stays."""
    verify_current_password(db, throttle, user_id, current)
    validate_password(new)
    if new == current:
        raise AppError(400, "weak_password", "新密码不能与当前密码相同")
    password_hash = hash_password(new)
    with db.connect() as connection:
        connection.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
        revoke_other_sessions(connection, user_id, keep_token)


def change_username(
    db: Database, throttle: LoginThrottle, user_id: str, current: str, username: str
) -> dict:
    """Rename the account. The id, and so every resource the user owns, stays the same."""
    verify_current_password(db, throttle, user_id, current)
    validate_username(username)
    try:
        with db.connect() as connection:
            connection.execute(
                "UPDATE users SET username=?, name=? WHERE id=?", (username, username, user_id)
            )
            return get_user(connection, user_id)
    except sqlite3.IntegrityError as exc:
        raise AppError(409, "username_taken", "用户名已被占用") from exc


def set_password(db: Database, username: str, password: str) -> dict:
    """Set or reset a password (CLI). Existing sessions are revoked."""
    validate_password(password)
    password_hash = hash_password(password)
    with db.connect() as connection:
        user = require_user_by_username(connection, username)
        connection.execute(
            "UPDATE users SET password_hash=? WHERE id=?", (password_hash, user["id"])
        )
        connection.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        return user


def delete_account(
    db: Database, throttle: LoginThrottle, user_id: str, password: str, *, artifacts_root: Path
) -> None:
    """Self-service account deletion, confirmed with the password."""
    verify_current_password(db, throttle, user_id, password)
    _delete_user_where(db, "id=?", user_id, artifacts_root)


def delete_user(db: Database, username: str, *, artifacts_root: Path) -> bool:
    """Delete a user by name (CLI). Returns False when the user does not exist."""
    return _delete_user_where(db, "username=? COLLATE NOCASE", username, artifacts_root)


def _delete_user_where(db: Database, condition: str, value: str, artifacts_root: Path) -> bool:
    """Delete a user and everything they own. Knowledge bases, conversations and sessions go
    by cascade; stored files are removed after the delete commits, as for knowledge bases."""
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute(f"SELECT id FROM users WHERE {condition}", (value,)).fetchone()
        if user is None:
            return False
        files = [
            (row["id"], row["path"])
            for row in connection.execute(
                """SELECT d.id, d.path FROM documents d
                JOIN knowledge_bases k ON k.id = d.knowledge_base_id WHERE k.user_id=?""",
                (user["id"],),
            )
        ]
        connection.execute("DELETE FROM users WHERE id=?", (user["id"],))
    remove_stored_files(files, artifacts_root)
    return True
