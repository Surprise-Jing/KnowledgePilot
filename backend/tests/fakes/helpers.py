"""Shared helpers for B's tests: isolated settings, data setup and an SSE frame parser."""

import json
from pathlib import Path

from app.config import Settings
from tests.fakes.llm import ScriptedLLM, scripted_factory


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Never reads a real .env; the model values are placeholders for the default factory."""
    values = {"llm_model": "test", "llm_api_key": "test", **overrides}
    return Settings(_env_file=None, data_dir=tmp_path, **values)


def headers(user: str = "demo") -> dict:
    return {"X-User-Id": user}


def make_kb(client, name: str = "库", user: str = "demo") -> str:
    response = client.post("/api/knowledge-bases", json={"name": name}, headers=headers(user))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client, kb_id: str, name: str, text: str, user: str = "demo") -> str:
    response = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        files={"file": (name, text.encode())},
        headers=headers(user),
    )
    assert response.status_code == 202, response.text
    document_id = response.json()["id"]
    # TestClient runs background tasks before returning, so parsing has finished.
    status = client.get(f"/api/documents/{document_id}", headers=headers(user)).json()["status"]
    assert status == "success"
    return document_id


def make_conversation(client, user: str = "demo", title: str | None = None) -> str:
    body = {} if title is None else {"title": title}
    response = client.post("/api/conversations", json=body, headers=headers(user))
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
        headers=headers(user),
    )
