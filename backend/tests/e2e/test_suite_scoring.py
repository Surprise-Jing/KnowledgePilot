"""Location-aware rule signals for suite v2: never a substitute for semantic review."""

import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"
sys.path.insert(0, str(EVAL_DIR))

from suite import scoring

DOCUMENTS = {
    "rpt": {"id": "rpt", "filename": "report.pdf", "knowledge_base": "基金"},
    "notes": {"id": "notes", "filename": "notes.txt", "knowledge_base": "基金"},
    "law": {"id": "law", "filename": "law.docx", "knowledge_base": "法规"},
    "film": {"id": "film", "filename": "Inception.txt", "knowledge_base": "多跳"},
}
EVIDENCE = {
    "rpt.close": {"document": "rpt", "locator": {"kind": "pdf_page", "page": 4},
                  "anchor": "期末值2.00元"},
    "notes.l12": {"document": "notes", "locator": {"kind": "txt_lines", "line": 12},
                  "anchor": "全年保持增长"},
    "law.a2": {"document": "law", "locator": {"kind": "docx", "heading": "第五章"},
               "anchor": "应当真实准确完整"},
    "film.dir": {"document": "film", "locator": {"kind": "txt_lines", "line": 2},
                 "anchor": "directed by Christopher Nolan"},
}  # fmt: skip
ITEM_FOR_KIND = {"pdf": "rpt.close", "txt": "notes.l12", "docx": "law.a2"}


def cite(evidence_id: str, location: dict, content: str | None = None, marker: str = "c1"):
    item = EVIDENCE[evidence_id]
    return {
        "marker": marker,
        "filename": DOCUMENTS[item["document"]]["filename"],
        "location": location,
        "content": content if content is not None else f"前文 {item['anchor']} 后文",
    }


def turn(answer: str, citations: list, **extra) -> dict:
    return {
        "answer": answer,
        "citations": citations,
        "stop_reason": "answered",
        "error_code": None,
        "event_order_ok": True,
        "tool_calls": [],
        "degraded": False,
        **extra,
    }


def make_case(evidence_id: str | None = "film.dir", keywords=None, **expect) -> dict:
    claims = []
    if evidence_id:
        claims.append(
            {
                "id": "k1",
                "label": "要点",
                "evidence_groups": [[evidence_id]],
                "answer_keywords": keywords or [],
                "answer_numbers": [],
            }
        )
    return {
        "id": "G00-1",
        "claims": claims,
        "expect": {
            "no_answer": False,
            "tools": [],
            "forbidden_tools": [],
            "stop_reason": "answered",
            "error_code": None,
            **expect,
        },
    }


GROUP = {"id": "G00", "knowledge_bases": ["多跳", "基金"]}


@pytest.mark.parametrize(
    "kind,location,ok",
    [
        ("pdf", {"kind": "pdf_page", "start": 3, "end": 4}, True),
        ("pdf", {"kind": "pdf_page", "start": 5, "end": 5}, False),
        ("txt", {"kind": "txt_lines", "start": 1, "end": 33}, True),
        ("txt", {"kind": "txt_lines", "start": 13, "end": 40}, False),
        ("docx", {"kind": "docx_paragraphs", "start": 7, "end": 9,
                  "heading_path": ["证券法", "第五章"]}, True),
        ("docx", {"kind": "docx_table_rows", "start": 1, "end": 3, "table": 1,
                  "heading_path": ["第五章"]}, True),
        ("docx", {"kind": "docx_paragraphs", "start": 7, "end": 9,
                  "heading_path": ["第六章"]}, False),
        ("docx", {"kind": "pdf_page", "start": 1, "end": 1, "heading_path": ["第五章"]}, False),
    ],
)  # fmt: skip
def test_evidence_matches_each_location_kind(kind, location, ok):
    key = ITEM_FOR_KIND[kind]
    assert scoring.evidence_matches(EVIDENCE[key], cite(key, location), DOCUMENTS) is ok


def test_anchor_absent_from_cited_text_fails_even_at_right_location():
    location = {"kind": "pdf_page", "start": 4, "end": 4}
    citation = cite("rpt.close", location, content="本页讨论其他指标")
    assert not scoring.evidence_matches(EVIDENCE["rpt.close"], citation, DOCUMENTS)


def test_markdown_pipes_do_not_hide_anchor():
    location = {"kind": "pdf_page", "start": 4, "end": 4}
    citation = cite("rpt.close", location, content="| 期末值 | 2.00元 |")
    assert scoring.evidence_matches(EVIDENCE["rpt.close"], citation, DOCUMENTS)


def test_english_keywords_ignore_case():
    location = {"kind": "txt_lines", "start": 1, "end": 3}
    result = scoring.judge(
        GROUP,
        make_case(keywords=[["christopher nolan"]]),
        [turn("The film was directed by Christopher Nolan [c1].", [cite("film.dir", location)])],
        EVIDENCE,
        DOCUMENTS,
    )
    assert result["claims"][0]["value_signal"] is True
    assert result["claims"][0]["evidence_covered"] is True
    assert result["rule_passed"] is True


def test_no_answer_case_requires_explicit_statement():
    case = make_case(evidence_id=None, no_answer=True)
    said = scoring.judge(GROUP, case, [turn("资料中没有找到相关信息。", [])], EVIDENCE, DOCUMENTS)
    made_up = scoring.judge(GROUP, case, [turn("答案是 42。", [])], EVIDENCE, DOCUMENTS)
    assert said["no_answer_check"] is True and said["rule_passed"] is True
    assert made_up["no_answer_check"] is False and made_up["rule_passed"] is False


def test_forbidden_tool_fails_tool_check():
    case = make_case(evidence_id=None, no_answer=True, tools=["list_files"],
                     forbidden_tools=["read_document"])  # fmt: skip
    calls = [{"name": "list_files"}, {"name": "read_document"}]
    result = scoring.judge(
        GROUP, case, [turn("没有找到。", [], tool_calls=calls)], EVIDENCE, DOCUMENTS
    )
    assert result["tool_check"] is False and result["rule_passed"] is False


def test_tools_from_earlier_turns_count():
    case = make_case(evidence_id=None, no_answer=True, tools=["list_files"])
    turns = [turn("先列文件", [], tool_calls=[{"name": "list_files"}]), turn("没有找到。", [])]
    assert scoring.judge(GROUP, case, turns, EVIDENCE, DOCUMENTS)["tool_check"] is True


def test_fault_case_checks_stop_reason_and_error_code():
    case = make_case(evidence_id=None, stop_reason="timeout", error_code="model_timeout")
    ok = turn("", [], stop_reason="timeout", error_code="model_timeout")
    wrong = turn("", [], stop_reason="answered")
    assert scoring.judge(GROUP, case, [ok], EVIDENCE, DOCUMENTS)["rule_passed"] is True
    assert scoring.judge(GROUP, case, [wrong], EVIDENCE, DOCUMENTS)["rule_passed"] is False


def test_degraded_expectation_checked():
    case = make_case(evidence_id=None, no_answer=True, degraded=True)
    plain = scoring.judge(GROUP, case, [turn("没有找到。", [])], EVIDENCE, DOCUMENTS)
    degraded = scoring.judge(
        GROUP, case, [turn("没有找到。", [], degraded=True)], EVIDENCE, DOCUMENTS
    )
    assert plain["structural_checks"]["degraded"] is False
    assert degraded["structural_checks"]["degraded"] is True


def test_citation_from_unselected_knowledge_base_leaks():
    location = {"kind": "docx_paragraphs", "start": 1, "end": 2, "heading_path": ["第五章"]}
    result = scoring.judge(
        GROUP,
        make_case(),
        [turn("Christopher Nolan [c1]", [cite("law.a2", location)])],
        EVIDENCE,
        DOCUMENTS,
    )
    assert result["leaked_files"] == ["law.docx"]
    assert result["rule_passed"] is False


def test_case_level_knowledge_bases_narrow_the_allowed_scope():
    location = {"kind": "txt_lines", "start": 1, "end": 20}
    case = {**make_case(), "knowledge_bases": ["多跳"]}
    result = scoring.judge(
        GROUP, case, [turn("增长 [c1]", [cite("notes.l12", location)])], EVIDENCE, DOCUMENTS
    )
    assert result["leaked_files"] == ["notes.txt"]
