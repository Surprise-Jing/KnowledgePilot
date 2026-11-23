"""Tool registry and the four knowledge tools (docs/contracts.md §5.1, §5.2). Owner: B."""

import asyncio
import json
import logging
import re
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import ToolResult
from app.tools import builtin
from app.tools.context import MarkerAllocator, ToolContext
from app.tools.registry import (
    MAX_RESULT_CHARS,
    execute,
    execute_async,
    list_tools,
    openai_tool_specs,
    serialize_result,
    summarize,
)
from tests.fakes.helpers import make_kb, make_settings, upload

CJK = re.compile(r"[一-鿿]")
# ~20 lines of ~60 chars: TXT chunks merge lines up to about 800 chars, so several chunks.
LONG_TEXT = "\n".join(
    f"第{i}行记录：" + ("北极星项目" if i == 15 else "常规内容") + "，" + "资料文本" * 12
    for i in range(1, 21)
)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def data(client):
    a, b = make_kb(client, "库A"), make_kb(client, "库B")
    other = make_kb(client, "别人的库", user="bob")
    return {
        "a": a,
        "b": b,
        "other": other,
        "doc_a": upload(client, a, "a.txt", "删除文档时同步清理索引。"),
        "long": upload(client, a, "long.txt", LONG_TEXT),
        "doc_b": upload(client, b, "b.txt", "库B 的独有内容：火星计划。"),
        "doc_other": upload(client, other, "o.txt", "别人的内容。", user="bob"),
    }


def make_ctx(client, settings, allowed, user="demo"):
    return ToolContext(
        db=client.app.state.db,
        settings=settings,
        user_id=user,
        allowed_kb_ids=allowed,
        run_id=None,
        markers=MarkerAllocator(),
    )


def error_code(result: ToolResult) -> str:
    assert result.ok is False and result.data is None
    return result.error.code


# ---- registry metadata ----


def test_registry_lists_exactly_the_contract_tools():
    assert {tool.name for tool in list_tools()} == {
        "list_knowledge_bases",
        "search_chunks",
        "list_files",
        "read_document",
    }
    required = {
        "list_knowledge_bases": [],
        "search_chunks": ["query"],
        "list_files": ["knowledge_base_id"],
        "read_document": ["document_id"],
    }
    specs = openai_tool_specs()
    assert len(specs) == 4
    for spec in specs:
        assert spec["type"] == "function"
        function = spec["function"]
        assert CJK.search(function["description"])
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        assert parameters["additionalProperties"] is False
        assert sorted(parameters.get("required", [])) == required[function["name"]]
        for prop in parameters["properties"].values():
            assert CJK.search(prop["description"])


def test_parameter_ranges_match_contract():
    specs = {
        spec["function"]["name"]: spec["function"]["parameters"] for spec in openai_tool_specs()
    }
    search = specs["search_chunks"]["properties"]
    assert (search["query"]["minLength"], search["query"]["maxLength"]) == (1, 500)
    assert (search["top_k"]["minimum"], search["top_k"]["maximum"]) == (1, 10)
    files = specs["list_files"]["properties"]
    assert (files["limit"]["minimum"], files["limit"]["maximum"]) == (1, 50)
    assert files["offset"]["minimum"] == 0
    read = specs["read_document"]["properties"]
    assert (read["max_chars"]["minimum"], read["max_chars"]["maximum"]) == (500, 4000)


# ---- structured argument errors ----


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("search_chunks", "{not json"),
        ("search_chunks", "[1, 2]"),
        ("search_chunks", "{}"),
        ("search_chunks", {"query": 3}),
        ("search_chunks", {"query": ""}),
        ("search_chunks", {"query": "x" * 501}),
        ("search_chunks", {"query": "x", "top_k": 11}),
        ("search_chunks", {"query": "x", "top_k": 0}),
        ("search_chunks", {"query": "x", "top_k": True}),
        ("search_chunks", {"query": "x", "top_k": 2.5}),
        ("search_chunks", {"query": "x", "knowledge_base_ids": "a"}),
        ("search_chunks", {"query": "x", "knowledge_base_ids": []}),
        ("search_chunks", {"query": "x", "extra": 1}),
        ("search_chunks", {"query": "   "}),
        ("list_files", {}),
        ("list_files", {"knowledge_base_id": "k", "limit": 51}),
        ("list_files", {"knowledge_base_id": "k", "offset": -1}),
        ("list_files", {"knowledge_base_id": "k", "recursive": "yes"}),
        ("read_document", {"document_id": "d", "max_chars": 4001}),
        ("read_document", {"document_id": "d", "max_chars": 100}),
        ("read_document", {"document_id": "d", "start_ordinal": -1}),
        ("list_knowledge_bases", {"unexpected": True}),
        ("no_such_tool", {}),
    ],
)
def test_invalid_arguments_are_structured(client, settings, data, name, raw):
    result = execute(make_ctx(client, settings, [data["a"]]), name, raw)
    assert error_code(result) == "invalid_argument"
    assert result.error.retryable is False
    assert CJK.search(result.error.message)


def test_null_optional_arguments_are_treated_as_omitted(client, settings, data):
    ctx = make_ctx(client, settings, [data["a"]])
    result = execute(ctx, "list_files", {"knowledge_base_id": data["a"], "folder_id": None})
    assert result.ok and result.data["limit"] == 20
    assert error_code(execute(ctx, "search_chunks", {"query": None})) == "invalid_argument"


def test_empty_argument_string_means_no_arguments(client, settings, data):
    result = execute(make_ctx(client, settings, [data["a"]]), "list_knowledge_bases", "")
    assert result.ok


# ---- scope and ownership ----


def test_search_outside_selected_knowledge_bases_is_out_of_scope(client, settings, data):
    ctx = make_ctx(client, settings, [data["a"]])
    result = execute(ctx, "search_chunks", {"query": "火星", "knowledge_base_ids": [data["b"]]})
    assert error_code(result) == "out_of_scope"
    other = execute(ctx, "search_chunks", {"query": "内容", "knowledge_base_ids": [data["other"]]})
    assert error_code(other) == "out_of_scope"


def test_search_defaults_to_all_selected_knowledge_bases(client, settings, data):
    ctx = make_ctx(client, settings, [data["a"], data["b"]])
    hits = execute(ctx, "search_chunks", {"query": "火星计划"}).data["hits"]
    assert hits and hits[0]["knowledge_base_id"] == data["b"]
    only_a = execute(
        make_ctx(client, settings, [data["a"]]), "search_chunks", {"query": "火星计划"}
    )
    assert all(hit["knowledge_base_id"] == data["a"] for hit in only_a.data["hits"])


def test_read_document_scope_and_ownership(client, settings, data):
    ctx = make_ctx(client, settings, [data["a"]])
    assert error_code(execute(ctx, "read_document", {"document_id": data["doc_b"]})) == (
        "out_of_scope"
    )
    other = execute(ctx, "read_document", {"document_id": data["doc_other"]})
    assert error_code(other) == "document_not_found"
    missing = execute(ctx, "read_document", {"document_id": "missing"})
    assert error_code(missing) == "document_not_found"


def test_list_files_scope(client, settings, data):
    ctx = make_ctx(client, settings, [data["a"]])
    assert error_code(execute(ctx, "list_files", {"knowledge_base_id": data["b"]})) == (
        "out_of_scope"
    )
    assert error_code(execute(ctx, "list_files", {"knowledge_base_id": data["other"]})) == (
        "out_of_scope"
    )
    folder = execute(ctx, "list_files", {"knowledge_base_id": data["a"], "folder_id": "nope"})
    assert error_code(folder) == "folder_not_found"


def test_list_knowledge_bases_only_returns_allowed(client, settings, data):
    ctx = make_ctx(client, settings, [data["a"], "deleted-or-foreign"])
    result = execute(ctx, "list_knowledge_bases", {})
    assert result.data == {
        "knowledge_bases": [{"id": data["a"], "name": "库A", "document_count": 2}]
    }
    assert summarize("list_knowledge_bases", result) == "可用知识库 1 个"


# ---- results ----


def test_search_without_results_is_ok(client, settings, data):
    result = execute(make_ctx(client, settings, [data["a"]]), "search_chunks", {"query": "zzzqqq"})
    assert result.ok is True and result.data == {"hits": []}
    assert summarize("search_chunks", result) == "未找到相关片段"


def test_search_hit_shape(client, settings, data):
    result = execute(
        make_ctx(client, settings, [data["a"]]), "search_chunks", {"query": "删除文档"}
    )
    hit = result.data["hits"][0]
    assert set(hit) == {
        "marker",
        "chunk_id",
        "document_id",
        "knowledge_base_id",
        "filename",
        "location",
        "score",
        "content",
    }
    assert hit["marker"] == "c1"
    assert hit["location"]["label"] == "第 1 行"
    assert re.fullmatch(r"命中 \d+ 个片段（\d+ 个文件）", summarize("search_chunks", result))


def test_search_uses_context_settings(client, settings, data):
    """vector retrieval without an embedding service fails with its own code, which proves
    the tool passes ToolContext.settings instead of reading the environment."""
    vector = settings.model_copy(update={"retriever": "vector", "embedding_base_url": ""})
    ctx = make_ctx(client, vector, [data["a"]])
    result = execute(ctx, "search_chunks", {"query": "删除"})
    assert error_code(result) == "embedding_not_configured"


def test_markers_are_stable_across_search_and_read(client, settings, data):
    ctx = make_ctx(client, settings, [data["a"]])
    hits = execute(ctx, "search_chunks", {"query": "北极星项目", "top_k": 1}).data["hits"]
    assert [hit["marker"] for hit in hits] == ["c1"]
    searched_chunk = hits[0]["chunk_id"]
    read = execute(ctx, "read_document", {"document_id": data["long"], "max_chars": 4000}).data
    markers = {chunk["marker"] for chunk in read["chunks"]}
    assert "c1" in markers
    assert len(read["chunks"]) > 1
    # New chunks get the next numbers in order of first appearance.
    new = [chunk["marker"] for chunk in read["chunks"] if chunk["marker"] != "c1"]
    assert new == [f"c{i}" for i in range(2, 2 + len(new))]
    assert ctx.markers.source("c1").chunk_id == searched_chunk
    again = execute(ctx, "search_chunks", {"query": "北极星项目", "top_k": 1}).data["hits"]
    assert again[0]["marker"] == "c1"


def test_read_document_content_has_marker_prefixes_and_paging(client, settings, data):
    ctx = make_ctx(client, settings, [data["a"]])
    first = execute(ctx, "read_document", {"document_id": data["long"], "max_chars": 500}).data
    assert set(first) == {
        "document_id",
        "filename",
        "content",
        "chunks",
        "truncated",
        "next_ordinal",
        "total_chunks",
    }
    assert first["truncated"] is True and first["next_ordinal"] == 1
    assert first["content"].startswith("[c1] 第1行记录")
    for chunk in first["chunks"]:
        assert f"[{chunk['marker']}] " in first["content"]
        assert set(chunk) == {"marker", "ordinal", "location"}
    rest = execute(
        ctx,
        "read_document",
        {"document_id": data["long"], "start_ordinal": first["next_ordinal"], "max_chars": 4000},
    ).data
    assert rest["chunks"][0]["ordinal"] == 1
    assert "未读完" in summarize("read_document", ToolResult(ok=True, data=first))


def test_read_document_not_ready(client, settings, data):
    with client.app.state.db.connect() as connection:
        connection.execute("UPDATE documents SET status='queued' WHERE id=?", (data["doc_a"],))
    result = execute(
        make_ctx(client, settings, [data["a"]]), "read_document", {"document_id": data["doc_a"]}
    )
    assert error_code(result) == "document_not_ready"
    assert result.error.retryable is True


def test_list_files_returns_listing(client, settings, data):
    result = execute(
        make_ctx(client, settings, [data["a"]]), "list_files", {"knowledge_base_id": data["a"]}
    )
    assert result.data["total"] == 2
    assert [entry["filename"] for entry in result.data["files"]] == ["a.txt", "long.txt"]
    assert summarize("list_files", result) == "列出 2 个文件（共 2 个）"


def test_unexpected_exceptions_become_internal_error(client, settings, data, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(builtin, "search", boom)
    with caplog.at_level(logging.ERROR):
        result = execute(make_ctx(client, settings, [data["a"]]), "search_chunks", {"query": "x"})
    assert error_code(result) == "internal_error"
    assert result.error.retryable is True
    assert "secret detail" not in result.error.message
    assert any("tool search_chunks failed" in record.message for record in caplog.records)


def test_execute_async_times_out(client, settings, data, monkeypatch):
    def slow(*args, **kwargs):
        time.sleep(0.5)
        return []

    monkeypatch.setattr(builtin, "search", slow)
    ctx = make_ctx(client, settings, [data["a"]])
    result = asyncio.run(execute_async(ctx, "search_chunks", {"query": "x"}, timeout=0.05))
    assert error_code(result) == "tool_timeout"
    assert result.error.retryable is True


def test_serialized_results_stay_valid_json_within_limit():
    big = ToolResult(
        ok=True,
        data={"hits": [{"marker": f"c{i}", "content": "很长的片段\n" * 500} for i in range(5)]},
    )
    text = serialize_result(big)
    assert len(text) <= MAX_RESULT_CHARS
    parsed = json.loads(text)
    assert parsed["ok"] is True and len(parsed["data"]["hits"]) == 5
    assert "note" in parsed
    small = ToolResult(ok=False, error={"code": "out_of_scope", "message": "超出范围"})
    assert json.loads(serialize_result(small)) == {
        "ok": False,
        "error": {"code": "out_of_scope", "message": "超出范围", "retryable": False},
    }
