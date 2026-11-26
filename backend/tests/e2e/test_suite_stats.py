"""Statistics and report tables for suite v2."""

import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"
sys.path.insert(0, str(EVAL_DIR))

from suite import dataset, report, stats


def test_wilson_all_pass_twenty():
    lo, hi = stats.wilson(20, 20)
    assert round(lo, 3) == 0.839 and hi == 1.0


def test_wilson_empty_is_full_range():
    assert stats.wilson(0, 0) == (0.0, 1.0)


def test_pass_hat_k():
    assert stats.pass_hat_k([[True] * 3, [True, False, True]]) == 0.5


def test_paired_bootstrap_identical_is_zero():
    assert stats.paired_bootstrap([1, 0, 1], [1, 0, 1]) == (0.0, 0.0, 0.0)


def test_paired_bootstrap_detects_consistent_gain():
    mean, lo, hi = stats.paired_bootstrap([0] * 30, [1] * 30)
    assert mean == 1.0 and lo == 1.0 and hi == 1.0


def test_paired_bootstrap_rejects_unpaired():
    with pytest.raises(ValueError, match="配对"):
        stats.paired_bootstrap([1, 0], [1])


def test_ndcg_perfect_ranking_is_one():
    assert stats.ndcg_at_k([[1, 1, 0]]) == 1.0
    assert stats.ndcg_at_k([[0, 1]]) == pytest.approx(0.6309, abs=1e-4)
    assert stats.ndcg_at_k([[0, 0]]) == 0.0


def test_recall_and_mrr():
    assert stats.mrr([1, 2, None]) == pytest.approx(0.5)
    assert stats.recall_at_k([1, 2, None], 1) == pytest.approx(1 / 3)
    assert stats.recall_at_k([1, 2, None], 5) == pytest.approx(2 / 3)


def test_percentile_linear():
    assert stats.percentile([1, 2, 3, 4], 50) == 2.5
    assert stats.percentile([], 50) is None


SUITE = dataset.Suite(
    root=Path("."),
    documents={
        "rpt": {"id": "rpt", "filename": "r.pdf", "knowledge_base": "基金"},
        "law": {"id": "law", "filename": "l.docx", "knowledge_base": "法规"},
    },
    groups=[
        {"id": "G01", "title": "收益矩阵", "knowledge_bases": ["基金"], "split": "dev",
         "dimensions": ["D1", "D2"], "cases": [{"id": "G01-1", "documents": ["rpt"],
                                                "turns": [{"content": "q"}]}]},
        {"id": "G02", "title": "条款", "knowledge_bases": ["法规"], "split": "test",
         "dimensions": ["D3"], "cases": [{"id": "G02-1", "documents": ["law"],
                                          "turns": [{"content": "q"}, {"content": "q2"}]}]},
    ],
)  # fmt: skip


def agent_case(case_id, passed, ttft=1.0):
    return {
        "id": case_id,
        "rule_passed": passed,
        "leaked_files": [],
        "tool_check": None,
        "no_answer_check": None,
        "claims": [{"id": "k1", "rule_satisfied": passed}],
        "turns": [{"ttft_s": ttft, "latency_s": ttft + 1, "prompt_tokens": 10,
                   "completion_tokens": 2, "tool_calls": [{"name": "search_chunks"}]}],
    }  # fmt: skip


AGENT = {
    "groups": [
        {"id": "G01", "repeat_index": 0, "cases": [agent_case("G01-1", True)]},
        {"id": "G01", "repeat_index": 1, "cases": [agent_case("G01-1", False)]},
        {"id": "G02", "repeat_index": 0, "cases": [agent_case("G02-1", True)]},
        {"id": "G02", "repeat_index": 1, "cases": [agent_case("G02-1", True)]},
    ]
}
RETRIEVAL = {
    "settings": {"retriever": "cosine"},
    "rows": [{"group": "G01", "id": "G01-1", "first_relevant_rank": 1, "relevance": [1, 0],
              "all_evidence_retrieved": True, "covered_groups": 1, "evidence_groups": 1}],
}  # fmt: skip


def test_report_tables_render_coverage_matrix():
    text = report.tables(SUITE, [AGENT], [RETRIEVAL], None)
    assert "| G01 | 收益矩阵 | dev | ✓ | ✓ |" in text
    assert "| G02 | 条款 | test |" in text


def test_report_tables_rule_and_pass_hat_k():
    text = report.tables(SUITE, [AGENT], [RETRIEVAL], None)
    # 3 of 4 case runs passed; G01-1 is not consistent across repeats, G02-1 is.
    assert "规则通过 | 3/4" in text
    assert "pass^2 | 0.500" in text
    assert "| cosine | 1 | 1.000 |" in text


def test_report_semantic_column_uses_final_verdicts():
    verdicts = {("G01-1", 0): True, ("G01-1", 1): True, ("G02-1", 0): False, ("G02-1", 1): True}
    text = report.tables(SUITE, [AGENT], [], verdicts)
    assert "语义通过 | 3/4" in text
