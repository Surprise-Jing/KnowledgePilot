"""Shared helpers for B's tests: isolated settings, data setup and an SSE frame parser."""

import json
from pathlib import Path

import httpx

from app.config import Settings
from app.services import auth
from tests.fakes.llm import ScriptedLLM, scripted_factory


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Never reads a real .env; the model values are placeholders for the default factory."""
    values = {"llm_model": "test", "llm_api_key": "test", **overrides}
    return Settings(_env_file=None, data_dir=tmp_path, **values)


EMBEDDING_URL = "https://embedding.example/v1"


def hybrid_settings(tmp_path: Path, **overrides) -> Settings:
    """BM25 + vector retrieval against the offline provider double below."""
    values = {
        "retriever": "hybrid",
        "embedding_base_url": EMBEDDING_URL,
        "embedding_dimensions": 3,
        **overrides,
    }
    return make_settings(tmp_path, **values)


class FakeEmbeddings:
    """Offline stand-in for the embedding HTTP service, patched in at `httpx.post` as used by
    app.services.embeddings. Set `failure` to an exception to make the provider fail."""

    def __init__(self) -> None:
        self.failure: Exception | None = None
        self.calls = 0

    def post(self, url: str, **kwargs) -> httpx.Response:
        assert url == EMBEDDING_URL + "/embeddings"
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        texts = kwargs["json"]["input"]
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "data": [
                    {"index": index, "embedding": [1.0, float(len(text) % 7), 0.5]}
                    for index, text in enumerate(texts)
                ]
            },
        )


def install_embeddings(monkeypatch) -> FakeEmbeddings:
    from app.services import embeddings

    fake = FakeEmbeddings()
    monkeypatch.setattr(embeddings.httpx, "post", fake.post)
    return fake


def _session_token(client, user: str) -> str:
    """A real session for `user` (created without a password if new), cached per app. Going
    through the session table keeps every test on the production authentication path while
    skipping the deliberately slow password hash."""
    state = client.app.state
    if not hasattr(state, "test_sessions"):
        state.test_sessions = {}
    tokens = state.test_sessions
    if user not in tokens:
        with client.app.state.db.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO users(id, name, username) VALUES (?, ?, ?)",
                (user, user, user),
            )
            tokens[user] = auth.create_session(connection, user, ttl_days=1)
    return tokens[user]


def login(client, user: str = "demo"):
    """Make `user` the client's default identity (its session cookie). Call after entering
    the TestClient context, which runs the migrations."""
    client.cookies.set(auth.SESSION_COOKIE, _session_token(client, user))
    return client


def headers(client, user: str = "demo") -> dict:
    """Per-request identity; an explicit Cookie header takes precedence over the client's."""
    return {"Cookie": f"{auth.SESSION_COOKIE}={_session_token(client, user)}"}


def make_kb(client, name: str = "库", user: str = "demo") -> str:
    response = client.post(
        "/api/knowledge-bases", json={"name": name}, headers=headers(client, user)
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client, kb_id: str, name: str, text: str, user: str = "demo") -> str:
    response = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        files={"file": (name, text.encode())},
        headers=headers(client, user),
    )
    assert response.status_code == 202, response.text
    document_id = response.json()["id"]
    # TestClient runs background tasks before returning, so parsing has finished.
    status = client.get(f"/api/documents/{document_id}", headers=headers(client, user)).json()[
        "status"
    ]
    assert status == "success"
    return document_id


def make_conversation(client, user: str = "demo", title: str | None = None) -> str:
    body = {} if title is None else {"title": title}
    response = client.post("/api/conversations", json=body, headers=headers(client, user))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def install_llm(client, llm: ScriptedLLM) -> ScriptedLLM:
    client.app.state.llm_factory = scripted_factory(llm)
    return llm


def read_sse(response) -> list[tuple[str, dict]]:
    """Split frames on blank lines, skip comment lines, check ids increase from 1 and parse
    the single-line JSON data."""
    events, expected_id = [], 1
    for block in response.text.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if not lines:
            continue
        fields = {}
        for line in lines:
            name, _, value = line.partition(": ")
            assert name not in fields, f"duplicate field {name} in {block!r}"
            fields[name] = value
        assert int(fields["id"]) == expected_id
        expected_id += 1
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def ask(client, conversation_id: str, content: str, kb_ids: list[str], user: str = "demo"):
    return client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": content, "knowledge_base_ids": kb_ids},
        headers=headers(client, user),
    )
