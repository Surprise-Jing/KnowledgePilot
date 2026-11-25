"""Suite v2 runner with offline parsers and a scripted model; no network, no model keys."""

import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path

import httpx
import pytest

EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"
sys.path.insert(0, str(EVAL_DIR))

import build_corpus
from pypdf import PdfReader
from suite import dataset
from suite import run as suite_run

PDF_SOURCE = "Opening value 1.00 CNY\n=== page ===\nClosing value 2.00 CNY"
TXT_SOURCE = "标题\n第一段\n全年保持增长\n其他内容"
DOCX_SOURCE = "# 第一章 总则\n第一条 本法适用于证券发行。"


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_case(case_id, docs, evidence_id, question, before=None):
    turn = {"content": question}
    if before:
        turn["before"] = before
    return {
        "id": case_id,
        "origin": "drafted",
        "source_ref": None,
        "documents": docs,
        "forbidden_documents": [],
        "turns": [turn],
        "claims": [
            {"id": "k1", "label": "k1", "evidence_groups": [[evidence_id]],
             "answer_keywords": [["2.00"]], "answer_numbers": []}
        ],
        "calculations": [],
        "expect": {"no_answer": False, "tools": [], "forbidden_tools": [],
                   "stop_reason": "answered", "error_code": None},
        "reference_answer": "2.00",
        "semantic_rubric": ["数值正确"],
        "review": {"status": "approved", "note": ""},
    }  # fmt: skip


@pytest.fixture
def mini(tmp_path):
    """Two knowledge bases, three real files in a local cache, two groups."""
    payloads = {
        ("KBA", "rpt", "report.pdf", "基金"): build_corpus.render_pdf(PDF_SOURCE),
        ("KBA", "notes", "notes.txt", "基金"): TXT_SOURCE.encode(),
        ("KBB", "law", "law.docx", "法规"): build_corpus.render_docx(DOCX_SOURCE),
    }
    root, cache = tmp_path / "suite", tmp_path / "cache"
    manifests: dict[str, list] = {}
    for (kb_id, doc_id, filename, kb_name), payload in payloads.items():
        fmt = filename.rsplit(".", 1)[1]
        manifests.setdefault(kb_id, []).append(
            {"id": doc_id, "filename": filename, "title": doc_id, "knowledge_base": kb_name,
             "folder": ["目录"], "url": f"https://example.org/{filename}",
             "source_type": "original_" + fmt if fmt != "txt" else "html_body_as_txt",
             "raw": {"url": "https://example.org/x.html", "sha256": "0" * 64},
             "license": "public", "sha256": hashlib.sha256(payload).hexdigest(),
             "bytes": len(payload),
             "pages": len(PdfReader(BytesIO(payload)).pages) if fmt == "pdf" else None,
             "lines": len(TXT_SOURCE.splitlines()) if fmt == "txt" else None}
        )  # fmt: skip
        (cache / kb_id).mkdir(parents=True, exist_ok=True)
        (cache / kb_id / filename).write_bytes(payload)
    for kb_id, documents in manifests.items():
        write_json(root / f"kbs/{kb_id}/manifest.json",
                   {"kb_id": kb_id, "retrieved_on": "2026-11-24", "documents": documents})  # fmt: skip
    write_json(
        root / "kbs/KBA/evidence.json",
        {"rpt.close": {"id": "rpt.close", "document": "rpt",
                       "locator": {"kind": "pdf_page", "page": 2}, "label": "期末值",
                       "value": "2.00", "unit": "CNY", "anchor": "Closing value 2.00"}},
    )  # fmt: skip
    write_json(root / "kbs/KBB/evidence.json", {})
    base = {"knowledge_bases": ["基金"], "split": "dev", "dimensions": ["D1"], "compare": True,
            "demo_candidate": False, "overrides": {}, "changelog": []}  # fmt: skip
    write_json(root / "groups/G01.json", {**base, "id": "G01", "title": "共享",
               "cases": [make_case("G01-1", ["rpt"], "rpt.close", "What is the closing value?")]})  # fmt: skip
    write_json(root / "groups/G02.json", {**base, "id": "G02", "title": "删除",
               "cases": [make_case("G02-1", ["rpt"], "rpt.close", "Closing value?",
                                   before=[{"action": "delete_document", "document": "rpt"}])]})  # fmt: skip
    (root / "judge").mkdir()
    (root / "judge/PROMPT.md").write_text("评审", encoding="utf-8")
    suite = dataset.load(root)
    dataset.validate(suite)
    return suite, cache


def settings_for(data_dir: Path, **overrides):
    from app.config import Settings

    return Settings(_env_file=None, data_dir=data_dir, retriever="cosine",
                    frontend_dir=data_dir / "none", **overrides)  # fmt: skip


@pytest.fixture
def snapshot(mini, tmp_path):
    suite, cache = mini
    path = suite_run.build_snapshot(suite, settings_for, cache, tmp_path / "snap", {})
    return suite, path


def test_snapshot_key_changes_with_embedding_config(tmp_path):
    a = suite_run.snapshot_key("f", settings_for(tmp_path, embedding_model="a"))
    b = suite_run.snapshot_key("f", settings_for(tmp_path, embedding_model="b"))
    assert a != b
    assert a == suite_run.snapshot_key("f", settings_for(tmp_path, embedding_model="a"))
    assert a != suite_run.snapshot_key("g", settings_for(tmp_path, embedding_model="a"))


def test_wait_parsed_reports_pending_count_on_timeout():
    client = httpx.Client(
        base_url="http://test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "parsing"})),
    )
    with pytest.raises(RuntimeError, match="仍有 2 份"):
        suite_run.wait_parsed(client, {"a": "1", "b": "2"}, timeout_s=0, poll_s=0)


def test_wait_parsed_fails_fast_on_failed_document():
    client = httpx.Client(
        base_url="http://test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "failed"})),
    )
    with pytest.raises(RuntimeError, match="解析失败"):
        suite_run.wait_parsed(client, {"a": "1"}, timeout_s=60, poll_s=0)


def test_snapshot_copy_isolates_deletions(snapshot, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    suite, path = snapshot
    hits = []
    for name, delete in (("a", True), ("b", False)):
        ids = suite_run.copy_snapshot(path, tmp_path / name)
        with TestClient(create_app(settings_for(tmp_path / name))) as client:
            if delete:
                response = client.delete(
                    f"/api/documents/{ids.documents['rpt']}", headers={"X-User-Id": "eval"}
                )
                assert response.status_code == 204
            rows = suite_run.retrieval_rows(client, ids, suite, suite.groups[:1], 10)
            hits.append(rows[0]["all_evidence_retrieved"])
    assert hits == [False, True]


def test_retrieval_rows_cover_evidence_groups(snapshot, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    suite, path = snapshot
    ids = suite_run.copy_snapshot(path, tmp_path / "data")
    extra = [{"id": "q1", "question": "全年保持增长", "knowledge_base": "基金",
              "supporting": [{"document": "notes", "line": 3, "anchor": "全年保持增长"}]}]  # fmt: skip
    with TestClient(create_app(settings_for(tmp_path / "data"))) as client:
        rows = suite_run.retrieval_rows(client, ids, suite, suite.groups, 10, extra)
    by_id = {row["id"]: row for row in rows}
    assert by_id["G01-1"]["evidence_groups"] == 1
    assert by_id["G01-1"]["covered_groups"] == 1
    assert by_id["G01-1"]["first_relevant_rank"] is not None
    assert by_id["q1"]["all_evidence_retrieved"] is True


def test_agent_group_with_scripted_llm_produces_judged_cases(snapshot, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.fakes.llm import ScriptedLLM, scripted_factory, text, tool_call

    suite, path = snapshot
    ids = suite_run.copy_snapshot(path, tmp_path / "data")
    llm = ScriptedLLM(
        [
            [tool_call("search_chunks", {"query": "Closing value 2.00"})],
            text("The closing value is 2.00 CNY [c1]."),
        ]
    )
    with TestClient(create_app(settings_for(tmp_path / "data"))) as client:
        client.app.state.llm_factory = scripted_factory(llm)
        row = suite_run.agent_group(client, client, ids, suite.groups[0], suite)
    case = row["cases"][0]
    assert row["id"] == "G01" and case["id"] == "G01-1"
    assert case["turns"][0]["citations"][0]["content"]
    assert case["claims"][0]["value_signal"] is True
    assert case["claims"][0]["evidence_covered"] is True
    assert case["rule_passed"] is True


def test_override_and_deleting_groups_get_their_own_app():
    shared = {"id": "G01", "overrides": {}, "cases": [{"turns": [{"content": "q"}]}]}
    timeout = {"id": "G19", "overrides": {"llm_timeout": 0.001},
               "cases": [{"turns": [{"content": "q"}]}]}  # fmt: skip
    deleting = {"id": "G20", "overrides": {},
                "cases": [{"turns": [{"content": "q", "before": [{"action": "x"}]}]}]}  # fmt: skip
    plans = suite_run.plan_instances([shared, timeout, deleting])
    assert [(o, [g["id"] for g in gs]) for o, gs in plans] == [
        ({}, ["G01"]),
        ({"llm_timeout": 0.001}, ["G19"]),
        ({}, ["G20"]),
    ]


def test_results_contain_no_secrets(tmp_path):
    from pydantic import SecretStr

    settings = settings_for(
        tmp_path,
        llm_api_key=SecretStr("sk-secret-value"),
        llm_base_url="https://llm.example.org/v1",
        llm_model="m",
        embedding_base_url="https://emb.example.org/v1",
        embedding_api_key=SecretStr("emb-secret"),
    )
    dumped = json.dumps(suite_run.describe_settings(settings))
    for secret in ("sk-secret-value", "emb-secret", "llm.example.org", "emb.example.org"):
        assert secret not in dumped
