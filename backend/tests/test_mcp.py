"""stdio MCP server (docs/contracts.md §6). Owner: E.

Every test spawns the real server (`python -m app.mcp_server`) as a subprocess and talks to it
with the official SDK client, so the transport, the stdout discipline and startup are covered.

Isolation from a developer's real `backend/.env`: the server runs with an empty temporary
directory as its cwd (pydantic-settings resolves `.env` relative to cwd, so none is found) and
`PYTHONPATH=backend` to import `app`. Passing `PK_*` overrides alone would still let unrelated
keys from a real `.env` (embedding endpoints, model keys) reach the server."""

import asyncio
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.tools.context import MarkerAllocator, ToolContext
from app.tools.registry import execute, openai_tool_specs
from tests.fakes.helpers import make_kb, upload

BACKEND = Path(__file__).resolve().parents[1]
DEMO_CLIENT = BACKEND.parent / "skills" / "knowledgepilot" / "scripts" / "demo_client.py"
SESSION_TIMEOUT = 60
PHRASE = "北极星计划的验收日期定在三月十五日"
OTHER_PHRASE = "鲍勃的私人笔记：火星基地预算"
MARKER = re.compile(r"c\d+")


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """Seed data through the HTTP app, exactly as a user of the web UI would."""
    data_dir = tmp_path_factory.mktemp("mcp-data")
    with TestClient(create_app(Settings(_env_file=None, data_dir=data_dir))) as client:
        kb_a = make_kb(client, "项目资料")
        kb_b = make_kb(client, "空库")
        doc_a = upload(client, kb_a, "plan.txt", f"项目周报。\n{PHRASE}。\n负责人是张三。")
        kb_other = make_kb(client, "别人的库", user="bob")
        doc_other = upload(client, kb_other, "bob.txt", OTHER_PHRASE, user="bob")
    return {
        "data_dir": data_dir,
        "kb_a": kb_a,
        "kb_b": kb_b,
        "doc_a": doc_a,
        "kb_other": kb_other,
        "doc_other": doc_other,
    }


@pytest.fixture
def server_cwd(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    return cwd


def server_env(data_dir: Path, user: str = "demo", **overrides: str) -> dict[str, str]:
    env = {
        "PYTHONPATH": str(BACKEND),
        "PK_DATA_DIR": str(data_dir),
        "PK_MCP_USER_ID": user,
        "PK_RETRIEVER": "cosine",
        "PK_LOG_LEVEL": "INFO",
    }
    env.update(overrides)
    return env


def params(cwd: Path, env: dict[str, str]) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable, args=["-m", "app.mcp_server"], cwd=str(cwd), env=env
    )


def with_session(cwd: Path, env: dict[str, str], body):
    """Run `body(session)` against a freshly spawned server; server stderr goes to a file."""

    async def main(errlog):
        async with (
            stdio_client(params(cwd, env), errlog=errlog) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await body(session)

    with open(cwd / "server-stderr.log", "w") as errlog:
        return asyncio.run(asyncio.wait_for(main(errlog), SESSION_TIMEOUT))


def payload(result: types.CallToolResult) -> dict:
    assert len(result.content) == 1 and result.content[0].type == "text"
    return json.loads(result.content[0].text)


def error_of(result: types.CallToolResult) -> dict:
    assert result.isError is True
    error = payload(result)
    assert set(error) == {"code", "message", "retryable"}
    return error


# ---- tool surface ----


def test_list_tools_equals_registry(seeded, server_cwd):
    async def body(session):
        return (await session.list_tools()).tools

    tools = with_session(server_cwd, server_env(seeded["data_dir"]), body)
    specs = [spec["function"] for spec in openai_tool_specs()]
    assert [tool.name for tool in tools] == [spec["name"] for spec in specs]
    for tool, spec in zip(tools, specs, strict=True):
        assert tool.description == spec["description"]
        assert tool.inputSchema == spec["parameters"]


# ---- permissions ----


def test_list_knowledge_bases_returns_only_the_mcp_users(seeded, server_cwd):
    async def body(session):
        return payload(await session.call_tool("list_knowledge_bases", {}))

    demo = with_session(server_cwd, server_env(seeded["data_dir"]), body)
    assert {kb["id"] for kb in demo["knowledge_bases"]} == {seeded["kb_a"], seeded["kb_b"]}
    counts = {kb["id"]: kb["document_count"] for kb in demo["knowledge_bases"]}
    assert counts == {seeded["kb_a"]: 1, seeded["kb_b"]: 0}

    bob = with_session(server_cwd, server_env(seeded["data_dir"], user="bob"), body)
    assert [kb["id"] for kb in bob["knowledge_bases"]] == [seeded["kb_other"]]


def test_scope_is_read_at_call_time(seeded, server_cwd, tmp_path):
    """A knowledge base created while the server runs is visible on the next call."""
    data_dir = tmp_path / "live"

    def create_kb(name: str) -> str:
        with TestClient(create_app(Settings(_env_file=None, data_dir=data_dir))) as client:
            return make_kb(client, name)

    first = create_kb("先建的库")

    async def body(session):
        before = payload(await session.call_tool("list_knowledge_bases", {}))
        second = await asyncio.to_thread(create_kb, "后建的库")
        after = payload(await session.call_tool("list_knowledge_bases", {}))
        return before, second, after

    before, second, after = with_session(server_cwd, server_env(data_dir), body)
    assert [kb["id"] for kb in before["knowledge_bases"]] == [first]
    assert {kb["id"] for kb in after["knowledge_bases"]} == {first, second}


def test_other_users_document_is_not_found(seeded, server_cwd):
    async def body(session):
        return await session.call_tool("read_document", {"document_id": seeded["doc_other"]})

    error = error_of(with_session(server_cwd, server_env(seeded["data_dir"]), body))
    assert error["code"] == "document_not_found"
    assert error["retryable"] is False
    assert OTHER_PHRASE not in error["message"]


def test_other_users_knowledge_base_is_out_of_scope(seeded, server_cwd):
    async def body(session):
        return await session.call_tool(
            "search_chunks", {"query": "火星", "knowledge_base_ids": [seeded["kb_other"]]}
        )

    error = error_of(with_session(server_cwd, server_env(seeded["data_dir"]), body))
    assert error["code"] == "out_of_scope"


# ---- search and read ----


def test_search_then_read_with_stable_markers(seeded, server_cwd):
    async def body(session):
        search = await session.call_tool("search_chunks", {"query": "北极星计划 验收日期"})
        hits = payload(search)["hits"]
        read = await session.call_tool("read_document", {"document_id": hits[0]["document_id"]})
        again = await session.call_tool("search_chunks", {"query": "北极星计划 验收日期"})
        return search, read, again

    search, read, again = with_session(server_cwd, server_env(seeded["data_dir"]), body)
    assert search.isError is False
    hits = payload(search)["hits"]
    assert hits and PHRASE in hits[0]["content"]
    assert hits[0]["document_id"] == seeded["doc_a"]
    assert hits[0]["knowledge_base_id"] == seeded["kb_a"]
    assert MARKER.fullmatch(hits[0]["marker"])
    assert search.structuredContent == payload(search)
    # Bob's content never shows up for the demo user.
    assert all(OTHER_PHRASE not in hit["content"] for hit in hits)

    document = payload(read)
    assert read.isError is False
    assert document["document_id"] == seeded["doc_a"]
    assert PHRASE in document["content"]
    # One MarkerAllocator per MCP session: the same chunk keeps its marker across calls.
    by_ordinal = {chunk["marker"] for chunk in document["chunks"]}
    assert hits[0]["marker"] in by_ordinal
    assert f"[{hits[0]['marker']}]" in document["content"]
    assert payload(again)["hits"][0]["marker"] == hits[0]["marker"]


def test_markers_restart_in_a_new_session(seeded, server_cwd):
    async def body(session):
        await session.call_tool("read_document", {"document_id": seeded["doc_a"]})
        return payload(await session.call_tool("search_chunks", {"query": "北极星计划"}))

    first = with_session(server_cwd, server_env(seeded["data_dir"]), body)
    second = with_session(server_cwd, server_env(seeded["data_dir"]), body)
    assert first["hits"][0]["marker"] == second["hits"][0]["marker"] == "c1"


# ---- errors ----


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("search_chunks", {}),
        ("search_chunks", {"query": ""}),
        ("search_chunks", {"query": "北极星", "top_k": 99}),
        ("search_chunks", {"query": "北极星", "unknown": 1}),
        ("read_document", {"document_id": "x", "max_chars": 10}),
        ("no_such_tool", {}),
    ],
)
def test_invalid_arguments_use_the_registry_error(seeded, server_cwd, name, arguments):
    async def body(session):
        return await session.call_tool(name, arguments)

    error = error_of(with_session(server_cwd, server_env(seeded["data_dir"]), body))
    assert error["code"] == "invalid_argument"
    assert error["retryable"] is False
    # Chinese message from the registry, not the SDK's English jsonschema message.
    assert re.search(r"[一-鿿]", error["message"])


def test_mcp_result_equals_in_app_execution_under_hybrid(seeded, server_cwd):
    """`hybrid` without an embedding endpoint fails inside the tool. Whatever the registry does
    with that (error, or the §10.2 BM25 degradation), MCP must return exactly the same."""
    settings = Settings(_env_file=None, data_dir=seeded["data_dir"], retriever="hybrid")
    ctx = ToolContext(
        db=Database(seeded["data_dir"] / "knowledge.db"),
        settings=settings,
        user_id="demo",
        allowed_kb_ids=[seeded["kb_a"], seeded["kb_b"]],
        run_id=None,
        markers=MarkerAllocator(),
    )
    arguments = {"query": "北极星计划 验收日期"}
    expected = execute(ctx, "search_chunks", arguments)

    async def body(session):
        return await session.call_tool("search_chunks", arguments)

    env = server_env(seeded["data_dir"], PK_RETRIEVER="hybrid", PK_EMBEDDING_BASE_URL="")
    result = with_session(server_cwd, env, body)
    assert result.isError is (not expected.ok)
    if expected.ok:
        assert payload(result) == expected.data
        assert payload(result)["degraded"]["kind"] == "bm25"
    else:
        assert payload(result) == expected.error.model_dump()


# ---- transport and startup ----


def test_stdout_carries_only_protocol_messages(seeded, server_cwd):
    """Talk raw JSON-RPC: every stdout line must parse, while the logs land on stderr."""
    stderr_path = server_cwd / "raw-stderr.log"
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": types.LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "search_chunks", "arguments": {"query": "北极星计划"}},
        },
    ]
    with open(stderr_path, "w") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.mcp_server"],
            cwd=server_cwd,
            env=server_env(seeded["data_dir"]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
        )
        try:
            responses = []
            for request in requests:
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                process.stdin.flush()
                if "id" in request:
                    responses.append(json.loads(process.stdout.readline()))
            process.stdin.close()
            rest = process.stdout.read()
            assert process.wait(timeout=SESSION_TIMEOUT) == 0
        finally:
            if process.poll() is None:
                process.kill()
    assert rest == ""
    assert [response["id"] for response in responses] == [1, 2]
    assert all(response["jsonrpc"] == "2.0" for response in responses)
    assert responses[0]["result"]["serverInfo"]["name"] == "knowledgepilot"
    assert responses[1]["result"]["isError"] is False
    logs = [json.loads(line) for line in stderr_path.read_text().splitlines() if line]
    assert any(log["msg"] == "mcp tool call" and log["tool"] == "search_chunks" for log in logs)
    # Document text never reaches the logs.
    assert PHRASE not in stderr_path.read_text()


def run_server_expecting_failure(cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "app.mcp_server"],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
        capture_output=True,
        text=True,
        timeout=SESSION_TIMEOUT,
    )


def test_invalid_user_id_fails_at_startup(tmp_path, server_cwd):
    completed = run_server_expecting_failure(server_cwd, server_env(tmp_path, user="bad id!"))
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "启动失败" in completed.stderr and "用户标识" in completed.stderr


def test_newer_database_fails_at_startup(tmp_path, server_cwd):
    connection = sqlite3.connect(tmp_path / "knowledge.db")
    connection.execute("PRAGMA user_version = 999")
    connection.close()
    completed = run_server_expecting_failure(server_cwd, server_env(tmp_path))
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "启动失败" in completed.stderr and "999" in completed.stderr


# ---- skill demo ----


def test_demo_client_runs_end_to_end(seeded, server_cwd):
    completed = subprocess.run(
        [
            sys.executable,
            str(DEMO_CLIENT),
            "--query",
            "北极星计划 验收日期",
            "--user",
            "demo",
            "--data-dir",
            str(seeded["data_dir"]),
            "--server-cwd",
            str(server_cwd),
        ],
        env={"PK_RETRIEVER": "cosine"},
        check=False,
        capture_output=True,
        text=True,
        timeout=SESSION_TIMEOUT,
    )
    assert completed.returncode == 0, completed.stderr
    out = completed.stdout
    for section in (
        "### list_tools",
        "### call_tool list_knowledge_bases 响应",
        "### call_tool search_chunks 响应",
        "### call_tool read_document 响应",
    ):
        assert section in out
    assert PHRASE in out
    assert '"isError": true' not in out
    assert OTHER_PHRASE not in out
