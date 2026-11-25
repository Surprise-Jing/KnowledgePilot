"""Offline checks for the evaluation set and runner (eval/): case schema, metric functions on
fixed inputs, and that the corpus builds and parses with the expected locations. No network,
no model keys: the corpus test uses retriever=cosine and FastAPI's TestClient."""

import sys
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"
sys.path.insert(0, str(EVAL_DIR))

import build_corpus
import metrics
import run as eval_run

from tests.fakes.helpers import login

CATEGORY_COUNTS = {
    "单库事实": 4,
    "多库范围": 3,
    "多轮指代": 3,
    "工具选择": 3,
    "引用定位": 3,
    "无结果": 2,
    "异常": 2,
}
TOOLS = {"list_knowledge_bases", "search_chunks", "list_files", "read_document"}
EXPECT_KEYS = {
    "relevant",
    "answer_keywords",
    "expect_tools",
    "forbidden_tools",
    "forbidden_files",
    "forbidden_keywords",
    "expect_no_answer",
    "expect_stop_reason",
    "expect_error_code",
    "retrieval_query",
}
CASES = eval_run.load_cases()


# ---- case file schema ----


def test_case_counts_and_ids():
    assert len(CASES) == 20
    assert Counter(case["category"] for case in CASES) == CATEGORY_COUNTS
    ids = [case["id"] for case in CASES]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_case_fields(case):
    assert {"id", "category", "knowledge_bases", "turns", "expect", "judge"} <= case.keys()
    assert case["judge"].strip()
    assert case["knowledge_bases"]
    assert set(case["knowledge_bases"]) <= set(build_corpus.KNOWLEDGE_BASES)
    assert case["turns"] and all(turn["content"].strip() for turn in case["turns"])
    for turn in case["turns"]:
        for action in turn.get("before", []):
            assert action["action"] == "delete_document"
            assert action["file"] in {item.filename for item in build_corpus.CORPUS}
    expect = case["expect"]
    assert set(expect) <= EXPECT_KEYS, set(expect) - EXPECT_KEYS
    assert "relevant" in expect and "answer_keywords" in expect
    assert set(expect.get("expect_tools", [])) | set(expect.get("forbidden_tools", [])) <= TOOLS
    in_scope = {
        item.filename
        for item in build_corpus.CORPUS
        if item.knowledge_base in case["knowledge_bases"]
    }
    for spec in expect["relevant"]:
        assert spec["file"] in in_scope, "relevant files must be in the selected knowledge bases"
        assert spec.get("anchor"), "every relevant location carries an anchor phrase"
    for group in expect["answer_keywords"]:
        assert isinstance(group, list) and group
    assert set(expect.get("forbidden_files", [])) <= {item.filename for item in build_corpus.CORPUS}
    if expect.get("expect_no_answer"):
        assert not expect["relevant"]
    if case["category"] == "异常":
        assert expect.get("expect_stop_reason") or expect.get("expect_error_code")


def test_cases_cover_required_features():
    assert any(len(case["turns"]) > 1 for case in CASES)
    assert any(case.get("overrides", {}).get("llm_timeout") for case in CASES)
    assert any(
        action["action"] == "delete_document"
        for case in CASES
        for turn in case["turns"]
        for action in turn.get("before", [])
    )
    kinds = {spec.get("kind") for case in CASES for spec in case["expect"]["relevant"]}
    assert {"txt_lines", "pdf_page", "docx_paragraphs", "docx_table_rows"} <= kinds
    tools = {tool for case in CASES for tool in case["expect"].get("expect_tools", [])}
    assert {"list_files", "read_document"} <= tools


# ---- metric functions ----


def loc(kind="txt_lines", start=1, end=1, table=None, heading_path=()):
    return {
        "kind": kind,
        "start": start,
        "end": end,
        "table": table,
        "heading_path": list(heading_path),
    }


def test_location_matching():
    spec = {"file": "a.txt", "kind": "txt_lines", "start": 9, "end": 9}
    assert metrics.location_matches(spec, "a.txt", loc(start=1, end=33))
    assert metrics.location_matches(spec, "a.txt", loc(start=9, end=12))
    assert not metrics.location_matches(spec, "a.txt", loc(start=10, end=20))
    assert not metrics.location_matches(spec, "b.txt", loc(start=1, end=33))
    assert not metrics.location_matches(spec, "a.txt", None)
    assert metrics.location_matches({"file": "a.txt"}, "a.txt", None)
    table = {"file": "h.docx", "kind": "docx_table_rows", "table": 1, "start": 2, "end": 2}
    assert metrics.location_matches(table, "h.docx", loc("docx_table_rows", 1, 4, table=1))
    assert not metrics.location_matches(table, "h.docx", loc("docx_table_rows", 1, 4, table=2))
    assert not metrics.location_matches(table, "h.docx", loc("docx_paragraphs", 1, 4))
    heading = {"file": "h.docx", "heading": "第二章"}
    assert metrics.location_matches(
        heading, "h.docx", loc("docx_paragraphs", heading_path=["手册", "第二章"])
    )
    assert not metrics.location_matches(
        heading, "h.docx", loc("docx_paragraphs", heading_path=["手册"])
    )


def test_ranks_hit_at_k_and_mrr():
    specs = [{"file": "a.txt", "start": 5, "end": 5}]
    hits = [
        {"filename": "b.txt", "location": loc(start=1, end=10)},
        {"filename": "a.txt", "location": loc(start=20, end=30)},
        {"filename": "a.txt", "location": loc(start=1, end=10)},
    ]
    assert metrics.first_relevant_rank(hits, specs) == 3
    assert metrics.first_relevant_rank(hits[:2], specs) is None
    result = metrics.retrieval_metrics([1, 3, None, 2])
    assert result == {
        "hit@1": 0.25,
        "hit@3": 0.75,
        "hit@5": 0.75,
        "mrr": pytest.approx((1 + 1 / 3 + 1 / 2) / 4),
        "queries": 4,
    }
    assert metrics.retrieval_metrics([])["mrr"] is None


def test_percentile_and_keywords():
    assert metrics.percentile([], 50) is None
    assert metrics.percentile([3.0], 95) == 3.0
    assert metrics.percentile([1, 2, 3, 4], 50) == 2.5
    assert metrics.percentile([1, 2, 3, 4, 5], 95) == pytest.approx(4.8)
    answer = "上限为 20MB；总成本 USD 168,000，快捷键 Ctrl＋K"
    assert metrics.keyword_hits(answer, [["20 MB"], ["168000"], ["7.8"]]) == [True, True, False]
    assert metrics.says_no_answer("所选知识库中没有找到相关信息")
    assert metrics.says_no_answer("资料中未提及品牌")
    assert not metrics.says_no_answer("回收期约 7.8 年 [c1]")


def turn(answer="", stop="answered", tools=(), citations=(), error=None, covered=None):
    return {
        "answer": answer,
        "stop_reason": stop,
        "error_code": error,
        "tool_calls": [
            {"name": name, "status": "success", "error_code": code} for name, code in tools
        ],
        "citations": [{"filename": f, "location": location} for f, location in citations],
        "retrieval_covered": covered,
    }


FACT = {
    "category": "单库事实",
    "expect": {
        "relevant": [{"file": "a.txt", "start": 9, "end": 9}],
        "answer_keywords": [["20 MB"]],
        "forbidden_files": ["b.txt"],
    },
}


def test_judge_pass_and_failure_types():
    good = turn(
        "上限 20 MB [c1]",
        tools=[("search_chunks", None)],
        citations=[("a.txt", loc(1, 1, 33))],
        covered=True,
    )
    verdict = metrics.judge_case(FACT, [good])
    assert verdict["passed"] and verdict["failure_type"] is None
    assert verdict["keyword_hit_rate"] == 1 and verdict["citations_correct"] == 1

    missed = turn("没有找到", tools=[("search_chunks", None)], covered=False)
    assert metrics.judge_case(FACT, [missed])["failure_type"] == "检索未命中"

    wrong_cite = turn("上限 20 MB", citations=[("a.txt", loc(1, 20, 30))], covered=True)
    assert metrics.judge_case(FACT, [wrong_cite])["failure_type"] == "引用错误"

    leaked = turn(
        "上限 20 MB", citations=[("a.txt", loc(1, 1, 33)), ("b.txt", loc())], covered=True
    )
    result = metrics.judge_case(FACT, [leaked])
    assert result["checks"]["citation_scope"] is False and result["failure_type"] == "引用错误"

    wrong_answer = turn("上限 10 MB", citations=[("a.txt", loc(1, 1, 33))], covered=True)
    assert metrics.judge_case(FACT, [wrong_answer])["failure_type"] == "回答错误"

    tool_case = {
        "category": "工具选择",
        "expect": {"relevant": [], "answer_keywords": [], "expect_tools": ["list_files"]},
    }
    assert (
        metrics.judge_case(tool_case, [turn("文件有…", tools=[("search_chunks", None)])])[
            "failure_type"
        ]
        == "工具选择错误"
    )

    timeout_case = {
        "category": "异常",
        "expect": {
            "relevant": [],
            "answer_keywords": [],
            "expect_stop_reason": "timeout",
            "expect_error_code": "model_timeout",
        },
    }
    assert metrics.judge_case(timeout_case, [turn(stop="timeout", error="model_timeout")])["passed"]
    failed = metrics.judge_case(timeout_case, [turn("答案")])
    assert not failed["passed"] and failed["failure_type"] == "异常处理"


def test_judge_uses_last_turn_and_no_answer():
    case = {
        "category": "无结果",
        "expect": {"relevant": [], "answer_keywords": [], "expect_no_answer": True},
    }
    assert metrics.judge_case(case, [turn("编造的答案"), turn("资料中没有提到这一点")])["passed"]
    assert not metrics.judge_case(case, [turn("资料中没有提到"), turn("是 Outlook")])["passed"]


# ---- corpus builds and parses with the expected locations ----


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eval")
    settings = Settings(
        _env_file=None, data_dir=tmp / "data", retriever="cosine", frontend_dir=tmp / "none"
    )
    with TestClient(create_app(settings)) as client:
        login(client, "eval")
        ids = eval_run.setup_corpus(client, tmp / "corpus")
        yield client, ids


def test_corpus_parses_with_expected_location_kinds(corpus):
    client, ids = corpus
    assert set(ids.knowledge_bases) == set(build_corpus.KNOWLEDGE_BASES)
    expected = {
        ".txt": {"txt_lines"},
        ".pdf": {"pdf_page"},
        ".docx": {"docx_paragraphs", "docx_table_rows"},
    }
    for filename, document_id in ids.documents.items():
        document = client.get(f"/api/documents/{document_id}").json()
        assert document["status"] == "success", (filename, document["error"])
        chunks = client.get(f"/api/documents/{document_id}/chunks").json()
        kinds = {chunk["location"]["kind"] for chunk in chunks}
        assert kinds == expected[Path(filename).suffix], filename
    pdf = client.get(f"/api/documents/{ids.documents['solar_pilot_report.pdf']}/chunks").json()
    assert [chunk["location"]["start"] for chunk in pdf] == [1, 2, 3, 4, 5, 6]
    handbook = client.get(f"/api/documents/{ids.documents['qinghe_handbook.docx']}/chunks").json()
    assert any("第二章 远程办公" in chunk["location"]["heading_path"] for chunk in handbook)


def test_corpus_folders_are_nested(corpus):
    client, ids = corpus
    kb_id = ids.knowledge_bases["工作资料"]
    folders = client.get(f"/api/knowledge-bases/{kb_id}/folders").json()
    by_name = {folder["name"]: folder for folder in folders}
    assert by_name["人事"]["parent_id"] == by_name["制度"]["id"]


@pytest.mark.parametrize(
    ("case_id", "spec"),
    [(case["id"], spec) for case in CASES for spec in case["expect"]["relevant"]],
)
def test_relevant_locations_contain_their_anchor(corpus, case_id, spec):
    client, ids = corpus
    chunks = client.get(f"/api/documents/{ids.documents[spec['file']]}/chunks").json()
    matching = [c for c in chunks if metrics.location_matches(spec, spec["file"], c["location"])]
    assert matching, f"{case_id}: no chunk at {spec}"
    assert any(spec["anchor"] in chunk["content"] for chunk in matching), case_id


def test_retrieval_mode_offline(corpus):
    client, ids = corpus
    result = eval_run.evaluate_retrieval(client, ids, CASES, "cosine", top_k=10)
    summary = result["summary"]
    assert summary["queries"] == sum(eval_run.retrieval_query(case) is not None for case in CASES)
    assert 0 <= summary["mrr"] <= summary["hit@5"] <= 1
    assert summary["hit@1"] <= summary["hit@3"] <= summary["hit@5"]
    markdown = eval_run.retrieval_markdown(
        {"top_k": 10, "ingested_with": "cosine", "strategies": [result]}
    )
    assert "| cosine |" in markdown
