"""Phase 0 platform behaviour: unified errors, request ids, schema v2. Owner: Tech Lead."""

import json
import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.logging_setup import JsonFormatter, request_id_var
from app.main import create_app
from app.schemas import FolderUpdate


@pytest.fixture
def settings(tmp_path):
    # No frontend build: test_unhandled_errors adds a route after startup, which a "/" static
    # mount would otherwise shadow.
    return Settings(_env_file=None, data_dir=tmp_path, frontend_dir=tmp_path / "no-frontend")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        yield client


def test_validation_errors_use_unified_structure(client):
    response = client.post("/api/knowledge-bases", json={"name": "  "})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_argument"
    assert error["retryable"] is False
    assert error["details"][0]["loc"] == ["body", "name"]


def test_app_errors_carry_retryable_and_details(client):
    error = client.get("/api/documents/missing").json()["error"]
    assert error == {
        "code": "document_not_found",
        "message": "文档不存在",
        "retryable": False,
        "details": [],
    }


def test_unhandled_errors_are_logged_and_reported(client, caplog):
    @client.app.get("/boom")
    def boom():
        raise RuntimeError("secret detail")

    with caplog.at_level(logging.ERROR):
        response = client.get("/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret detail" not in response.text
    assert any("unhandled error" in record.message for record in caplog.records)


def test_request_id_is_echoed_or_generated(client):
    assert client.get("/health", headers={"X-Request-Id": "abc"}).headers["x-request-id"] == "abc"
    assert len(client.get("/health").headers["x-request-id"]) == 32


def test_json_formatter_includes_correlation_ids():
    token = request_id_var.set("req-1")
    try:
        record = logging.LogRecord("t", logging.INFO, "", 0, "hello %s", ("x",), None)
        record.tool = "search_chunks"
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert payload["msg"] == "hello x"
    assert payload["request_id"] == "req-1"
    assert payload["tool"] == "search_chunks"


def test_schema_v2_columns_exist(settings):
    with TestClient(create_app(settings)):
        pass
    connection = sqlite3.connect(settings.data_dir / "knowledge.db")

    def columns(table):
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

    assert "result_json" in columns("tool_calls")
    assert "dropped_citations" in columns("runs")
    assert "location_json" in columns("citations")
    assert {"attempt", "retry_of", "fallback"} <= columns("tool_calls")
    assert {"degraded", "model_retries"} <= columns("runs")


def test_patch_models_distinguish_null_from_omitted():
    assert FolderUpdate.model_validate({"parent_id": None}).model_fields_set == {"parent_id"}
    with pytest.raises(ValueError):
        FolderUpdate.model_validate({})
    with pytest.raises(ValueError):
        FolderUpdate.model_validate({"name": None})


def test_frontend_build_is_served_without_shadowing_api(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>KP</title>", encoding="utf-8")
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", frontend_dir=dist)
    with TestClient(create_app(settings)) as client:
        assert "<title>KP</title>" in client.get("/").text
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/api/knowledge-bases").status_code == 200


def test_missing_frontend_build_means_api_only(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, frontend_dir=tmp_path / "none")
    with TestClient(create_app(settings)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/health").status_code == 200
