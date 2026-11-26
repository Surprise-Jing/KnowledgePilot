"""Registration, login and sessions (docs/contracts.md §15). These go through the real
password hashing; other tests open sessions directly (tests/fakes/helpers.py)."""

import io
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import users as users_cli
from app.main import create_app
from app.services import auth
from tests.fakes.helpers import make_settings

PASSWORD = "secret123"


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def register(client, username="alice", password=PASSWORD):
    return client.post("/api/auth/register", json={"username": username, "password": password})


def login(client, username="alice", password=PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def error_code(response):
    return response.json()["error"]["code"]


def row(settings, sql, params=()):
    with sqlite3.connect(settings.data_dir / "knowledge.db") as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(sql, params).fetchone()


# ---- registration ----


def test_register_logs_in_with_an_httponly_cookie_and_hashes_the_password(client, settings):
    response = register(client)
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "username", "created_at"} and body["username"] == "alice"
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith(f"{auth.SESSION_COOKIE}=")
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "secure" not in cookie
    assert client.get("/api/auth/me").json() == body
    stored = row(settings, "SELECT password_hash FROM users WHERE id=?", (body["id"],))[0]
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    token = client.cookies[auth.SESSION_COOKIE]
    assert row(settings, "SELECT count(*) FROM sessions WHERE token_hash=?", (token,))[0] == 0


def test_secure_cookie_can_be_enabled(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, cookie_secure=True))) as client:
        assert "secure" in register(client).headers["set-cookie"].lower()


def test_usernames_are_unique_ignoring_case(client):
    assert register(client, "Alice").status_code == 201
    client.cookies.clear()
    response = register(client, "alice")
    assert response.status_code == 409 and error_code(response) == "username_taken"


@pytest.mark.parametrize("username", ["ab", "a" * 33, "has space", "名字", "a/b"])
def test_invalid_usernames_are_rejected(client, username):
    response = register(client, username)
    assert response.status_code == 400 and error_code(response) == "invalid_username"


@pytest.mark.parametrize("password", ["short1", "lettersonly", "12345678", "a1" * 65])
def test_weak_passwords_are_rejected(client, password):
    response = register(client, password=password)
    assert response.status_code == 400 and error_code(response) == "weak_password"


def test_registration_can_be_closed(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, allow_registration=False))) as client:
        response = register(client)
        assert response.status_code == 403 and error_code(response) == "registration_closed"


# ---- login ----


def test_login_with_correct_password_ignores_username_case(client):
    user = register(client).json()
    client.cookies.clear()
    response = login(client, "ALICE")
    assert response.status_code == 200 and response.json() == user
    assert client.get("/api/auth/me").json()["id"] == user["id"]


def test_wrong_password_and_unknown_user_look_the_same(client):
    register(client)
    client.cookies.clear()
    wrong = login(client, password="wrong-pass1")
    unknown = login(client, "nobody")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert error_code(wrong) == "invalid_credentials"
    assert "set-cookie" not in wrong.headers


def test_user_without_a_password_cannot_log_in(client):
    with client.app.state.db.connect() as connection:
        connection.execute("INSERT INTO users(id, name, username) VALUES ('old', 'old', 'old')")
    response = login(client, "old", "anything1")
    assert response.status_code == 401 and error_code(response) == "invalid_credentials"


def test_repeated_failures_are_throttled_even_with_the_right_password(client):
    register(client)
    client.cookies.clear()
    for _ in range(5):
        assert login(client, password="wrong-pass1").status_code == 401
    response = login(client, "ALICE")
    assert response.status_code == 429 and error_code(response) == "too_many_attempts"
    assert response.json()["error"]["retryable"] is True
    # Other accounts are not affected.
    assert login(client, "bob").status_code == 401


def test_a_successful_login_resets_the_failure_count(client):
    register(client)
    for _ in range(4):
        login(client, password="wrong-pass1")
    assert login(client).status_code == 200
    for _ in range(4):
        login(client, password="wrong-pass1")
    assert login(client).status_code == 200


def test_logging_in_again_replaces_the_previous_session(client, settings):
    register(client)
    first = client.cookies[auth.SESSION_COOKIE]
    login(client)
    assert client.cookies[auth.SESSION_COOKIE] != first
    assert row(settings, "SELECT count(*) FROM sessions")[0] == 1
    response = client.get("/api/auth/me", headers={"Cookie": f"{auth.SESSION_COOKIE}={first}"})
    assert response.status_code == 401


# ---- sessions ----


def test_requests_need_a_session(client):
    for method, path in [
        ("get", "/api/auth/me"),
        ("get", "/api/knowledge-bases"),
        ("get", "/api/conversations"),
        ("post", "/api/search"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 401, path
        assert error_code(response) == "unauthenticated"
    assert client.get("/health").status_code == 200


def test_forged_tokens_and_user_headers_are_rejected(client):
    register(client)
    client.cookies.clear()
    for headers in ({"X-User-Id": "alice"}, {"Cookie": f"{auth.SESSION_COOKIE}=forged"}):
        assert client.get("/api/knowledge-bases", headers=headers).status_code == 401


def test_logout_revokes_the_session_and_clears_the_cookie(client):
    register(client)
    token = client.cookies[auth.SESSION_COOKIE]
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    assert f'{auth.SESSION_COOKIE}=""' in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()
    stale = {"Cookie": f"{auth.SESSION_COOKIE}={token}"}
    assert client.get("/api/auth/me", headers=stale).status_code == 401
    # Idempotent without a session.
    client.cookies.clear()
    assert client.post("/api/auth/logout").status_code == 204


def test_expired_sessions_are_rejected_and_cleaned_up_on_next_login(client, settings):
    register(client)
    with client.app.state.db.connect() as connection:
        connection.execute("UPDATE sessions SET expires_at = datetime('now', '-1 minute')")
    response = client.get("/api/auth/me")
    assert response.status_code == 401 and error_code(response) == "unauthenticated"
    client.cookies.clear()
    login(client)
    assert row(settings, "SELECT count(*) FROM sessions")[0] == 1


def test_session_lifetime_follows_the_setting(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, session_ttl_days=2))) as client:
        response = register(client)
        assert f"max-age={2 * 24 * 3600}" in response.headers["set-cookie"].lower()
        with client.app.state.db.connect() as connection:
            days = connection.execute(
                "SELECT julianday(expires_at) - julianday(created_at) FROM sessions"
            ).fetchone()[0]
        assert round(days) == 2


def test_registered_users_only_see_their_own_data(client):
    register(client, "alice")
    kb = client.post("/api/knowledge-bases", json={"name": "alice 的库"}).json()["id"]
    client.cookies.clear()
    register(client, "bob")
    assert client.get("/api/knowledge-bases").json() == []
    assert client.patch(f"/api/knowledge-bases/{kb}", json={"name": "x"}).status_code == 404


# ---- password hashing ----


def test_password_hashes_are_salted_and_malformed_hashes_never_match():
    first, second = auth.hash_password(PASSWORD), auth.hash_password(PASSWORD)
    assert first != second
    assert auth.verify_password(PASSWORD, first) and not auth.verify_password("other1234", first)
    for broken in ["", "plain", "scrypt$x$8$1$AAAA$AAAA", "bcrypt$1$2$3$4$5"]:
        assert not auth.verify_password(PASSWORD, broken)


# ---- migration from schema v6 ----


def test_v7_migration_gives_existing_users_a_username_and_no_password(settings):
    with TestClient(create_app(settings)):
        pass
    with sqlite3.connect(settings.data_dir / "knowledge.db") as connection:
        # Rewind to v6: the users table had no login columns and ids could differ in case.
        connection.execute("DROP TABLE sessions")
        connection.execute("DROP INDEX idx_users_username")
        connection.execute("ALTER TABLE users DROP COLUMN username")
        connection.execute("ALTER TABLE users DROP COLUMN password_hash")
        for user_id in ["demo", "Bob", "bob"]:
            connection.execute("INSERT INTO users(id, name) VALUES (?, ?)", (user_id, user_id))
        connection.execute(
            "INSERT INTO knowledge_bases(id, user_id, name) VALUES ('kb', 'demo', '库')"
        )
        connection.execute("PRAGMA user_version=6")
    with TestClient(create_app(settings)) as client:
        with client.app.state.db.connect() as connection:
            rows = connection.execute(
                "SELECT id, username, password_hash FROM users ORDER BY id"
            ).fetchall()
            assert [tuple(r) for r in rows] == [
                ("Bob", "Bob", None),
                ("bob", None, None),
                ("demo", "demo", None),
            ]
            assert connection.execute("SELECT user_id FROM knowledge_bases").fetchone()[0] == "demo"
        # The case-insensitive index now protects existing names too.
        assert register(client, "DEMO").status_code == 409


# ---- administration CLI ----


@pytest.fixture
def cli(settings, monkeypatch):
    monkeypatch.setenv("PK_DATA_DIR", str(settings.data_dir))

    def run(*argv, stdin=""):
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        return users_cli.main(list(argv))

    return run


def test_cli_sets_a_password_for_an_existing_user_and_revokes_sessions(client, cli, capsys):
    with client.app.state.db.connect() as connection:
        connection.execute("INSERT INTO users(id, name, username) VALUES ('demo', 'demo', 'demo')")
        auth.create_session(connection, "demo", ttl_days=1)
    assert cli("set-password", "demo", "--password-stdin", stdin="newpass42\n") == 0
    with client.app.state.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    assert login(client, "demo", "newpass42").status_code == 200
    assert cli("set-password", "demo", "--password-stdin", stdin="weak\n") == 1
    assert cli("set-password", "ghost", "--password-stdin", stdin="newpass42\n") == 1
    cli("list")
    assert "demo\tdemo\t已设密码" in capsys.readouterr().out


def test_cli_deletes_a_user_with_their_data_and_files(client, cli, settings):
    register(client, "bob")
    kb = client.post("/api/knowledge-bases", json={"name": "库"}).json()["id"]
    document = client.post(
        f"/api/knowledge-bases/{kb}/documents", files={"file": ("a.txt", b"hello world")}
    ).json()
    path = row(settings, "SELECT path FROM documents WHERE id=?", (document["id"],))[0]
    client.post("/api/conversations", json={})
    assert cli("delete", "bob") == 2  # needs --yes
    assert cli("delete", "BOB", "--yes") == 0
    for table in ("users", "knowledge_bases", "documents", "conversations", "sessions"):
        assert row(settings, f"SELECT count(*) FROM {table}")[0] == 0, table
    assert not (settings.data_dir / "uploads").joinpath(path).exists()
    assert client.get("/api/auth/me").status_code == 401
    assert cli("delete", "bob", "--yes") == 0


# ---- account management ----


def other_session(client, username="alice", password=PASSWORD) -> dict:
    """Log in from a second "device" without touching the client's own cookie."""
    saved = dict(client.cookies)
    client.cookies.clear()
    token = login(client, username, password).cookies[auth.SESSION_COOKIE]
    client.cookies.clear()
    for name, value in saved.items():
        client.cookies.set(name, value)
    return {"Cookie": f"{auth.SESSION_COOKIE}={token}"}


def change_password(client, current=PASSWORD, new="newpass42"):
    return client.post(
        "/api/auth/password", json={"current_password": current, "new_password": new}
    )


def test_change_password_keeps_this_session_and_signs_out_others(client):
    register(client)
    elsewhere = other_session(client)
    assert change_password(client).status_code == 204
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/auth/me", headers=elsewhere).status_code == 401
    client.cookies.clear()
    assert login(client).status_code == 401
    assert login(client, password="newpass42").status_code == 200


def test_change_password_checks_the_current_and_the_new_password(client):
    register(client)
    wrong = change_password(client, current="wrong-pass1")
    assert wrong.status_code == 403 and error_code(wrong) == "wrong_password"
    weak = change_password(client, new="short")
    assert weak.status_code == 400 and error_code(weak) == "weak_password"
    same = change_password(client, new=PASSWORD)
    assert same.status_code == 400 and error_code(same) == "weak_password"
    # A wrong current password is not a lost session: the client stays signed in.
    assert client.get("/api/auth/me").status_code == 200


def test_wrong_current_passwords_are_throttled_like_logins(client):
    register(client)
    for _ in range(5):
        assert change_password(client, current="wrong-pass1").status_code == 403
    response = change_password(client)
    assert response.status_code == 429 and error_code(response) == "too_many_attempts"


def test_change_username_keeps_the_account_and_its_data(client):
    user = register(client).json()
    kb = client.post("/api/knowledge-bases", json={"name": "库"}).json()["id"]
    response = client.patch(
        "/api/auth/me", json={"username": "  alice2 ", "current_password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json() == {**user, "username": "alice2"}
    assert [item["id"] for item in client.get("/api/knowledge-bases").json()] == [kb]
    client.cookies.clear()
    assert login(client, "alice").status_code == 401
    assert login(client, "alice2").status_code == 200


def test_change_username_validates_and_rejects_taken_names(client):
    register(client, "bob")
    client.cookies.clear()
    register(client, "alice")

    def rename(username, password=PASSWORD):
        return client.patch(
            "/api/auth/me", json={"username": username, "current_password": password}
        )

    assert error_code(rename("BOB")) == "username_taken"
    assert error_code(rename("x")) == "invalid_username"
    assert error_code(rename("alice3", "wrong-pass1")) == "wrong_password"
    # Changing only the case of one's own name is allowed.
    assert rename("Alice").json()["username"] == "Alice"


def test_delete_account_needs_the_password_and_removes_everything(client, settings):
    register(client, "bob")
    kb = client.post("/api/knowledge-bases", json={"name": "库"}).json()["id"]
    client.post(f"/api/knowledge-bases/{kb}/documents", files={"file": ("a.txt", b"hello")})
    elsewhere = other_session(client, "bob")
    wrong = client.request("DELETE", "/api/auth/me", json={"password": "wrong-pass1"})
    assert wrong.status_code == 403 and error_code(wrong) == "wrong_password"
    response = client.request("DELETE", "/api/auth/me", json={"password": PASSWORD})
    assert response.status_code == 204
    assert "max-age=0" in response.headers["set-cookie"].lower()
    for table in ("users", "knowledge_bases", "documents", "sessions"):
        assert row(settings, f"SELECT count(*) FROM {table}")[0] == 0, table
    assert not list((settings.data_dir / "uploads").iterdir())
    assert client.get("/api/auth/me", headers=elsewhere).status_code == 401
    # The name is free again.
    assert register(client, "bob").status_code == 201


def test_account_operations_need_a_session(client):
    for method, path, body in [
        ("PATCH", "/api/auth/me", {"username": "x12", "current_password": PASSWORD}),
        ("POST", "/api/auth/password", {"current_password": PASSWORD, "new_password": "a1b2c3d4"}),
        ("DELETE", "/api/auth/me", {"password": PASSWORD}),
    ]:
        response = client.request(method, path, json=body)
        assert response.status_code == 401 and error_code(response) == "unauthenticated", path
