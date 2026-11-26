"""In-session judging: packets, verdict validation, review selection, merge and audit."""

import json
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"
sys.path.insert(0, str(EVAL_DIR))

from suite import dataset
from suite.judge import audit, packets

SUITE = dataset.Suite(
    root=Path("."),
    documents={"rpt": {"id": "rpt", "filename": "r.pdf", "knowledge_base": "基金"}},
    evidence={
        "rpt.close": {"id": "rpt.close", "document": "rpt",
                      "locator": {"kind": "pdf_page", "page": 2}, "anchor": "期末值2.00元"}
    },
    groups=[
        {"id": "G01", "title": "t", "knowledge_bases": ["基金"], "split": "dev",
         "dimensions": ["D1", "D2"],
         "cases": [
             {"id": "G01-1", "turns": [{"content": "期末值是多少？"}],
              "claims": [
                  {"id": "c1", "label": "期末值", "evidence_groups": [["rpt.close"]],
                   "answer_keywords": [],
                   "answer_numbers": [{"value": "2.00", "unit": "CNY", "tolerance": "0"}]},
                  {"id": "c2", "label": "单位", "evidence_groups": [["rpt.close"]],
                   "answer_keywords": [["元"]], "answer_numbers": []},
              ],
              "expect": {"no_answer": False}, "reference_answer": "2.00 元",
              "semantic_rubric": ["数值正确"]},
             {"id": "G01-2", "turns": [{"content": "有没有逆变器品牌？"}], "claims": [],
              "expect": {"no_answer": True}, "reference_answer": "资料未提及",
              "semantic_rubric": ["明确说明未提及"]},
         ]},
        {"id": "G02", "title": "t2", "knowledge_bases": ["基金"], "split": "dev",
         "dimensions": ["D3"],
         "cases": [{"id": "G02-1", "turns": [{"content": "q"}],
                    "claims": [{"id": "c1", "label": "x", "evidence_groups": [["rpt.close"]],
                                "answer_keywords": [["x"]], "answer_numbers": []}],
                    "expect": {"no_answer": False}, "reference_answer": "x",
                    "semantic_rubric": ["x"]}]},
    ],
)  # fmt: skip


def result_case(case_id, claim_ids, secret_answer="期末值 2.00 元 [c1]"):
    return {
        "id": case_id,
        "rule_passed": True,
        "structural_checks": {"stop_reason": True},
        "tool_check": None,
        "claims": [{"id": c, "value_signal": True, "evidence_covered": True,
                    "rule_satisfied": True} for c in claim_ids],
        "turns": [{"question": "期末值是多少？", "answer": secret_answer,
                   "citations": [{"marker": "c1", "filename": "r.pdf",
                                  "location_label": "第 2 页", "content": "期末值2.00元"}]}],
    }  # fmt: skip


RESULT = {
    "groups": [
        {"id": "G01", "repeat_index": 0,
         "cases": [result_case("G01-1", ["c1", "c2"]), result_case("G01-2", [])]},
        {"id": "G02", "repeat_index": 0, "cases": [result_case("G02-1", ["c1"])]},
    ]
}  # fmt: skip


@pytest.fixture
def built(tmp_path):
    paths = packets.build_packets(RESULT, SUITE, tmp_path / "packets", "run.json")
    return {path.name: path for path in paths}


def verdict_file(path: Path, packet: Path, rows, stage="initial", model="claude-sonnet-5-5"):
    data = json.loads(packet.read_text("utf-8"))
    path.write_text(
        json.dumps({"group_id": data["group_id"], "repeat_index": data["repeat_index"],
                    "stage": stage, "judge_model": model, "verdicts": rows}),
        encoding="utf-8",
    )  # fmt: skip
    return path


def v(case_id, claim_id, correct="yes", citation="supported"):
    return {"case_id": case_id, "claim_id": claim_id, "correct": correct,
            "citation_supports": citation, "reason": "理由"}  # fmt: skip


def test_packets_one_per_group_repeat_with_evidence_and_pseudo_claim(built):
    assert sorted(built) == ["G01-r1.json", "G02-r1.json"]
    data = json.loads(built["G01-r1.json"].read_text("utf-8"))
    first, no_answer = data["cases"]
    assert first["claims"][0]["evidence_excerpts"][0]["anchor"] == "期末值2.00元"
    assert first["claims"][0]["reference"] == "2.00 CNY"
    assert first["citations"][0]["content"] == "期末值2.00元"
    assert no_answer["expect_no_answer"] is True
    assert [c["claim_id"] for c in no_answer["claims"]] == [packets.ANSWER_CLAIM]


def test_packets_carry_how_the_final_turn_ended(tmp_path):
    # Fault cases are judged on the error or degradation notice, which never reaches the
    # answer text: an empty answer must not read as "no notice".
    fault = result_case("G01-2", [], secret_answer="")
    fault["turns"][-1].update(stop_reason="timeout", error_code="model_timeout", degraded=False)
    result = {"groups": [{"id": "G01", "repeat_index": 0,
                          "cases": [result_case("G01-1", ["c1", "c2"]), fault]}]}  # fmt: skip
    path = packets.build_packets(result, SUITE, tmp_path / "packets", "run.json")[0]
    first, timed_out = json.loads(path.read_text("utf-8"))["cases"]
    assert timed_out["outcome"] == {
        "stop_reason": "timeout",
        "error_code": "model_timeout",
        "degraded": False,
    }
    assert first["outcome"] == {"stop_reason": None, "error_code": None, "degraded": None}


def test_packets_include_earlier_turns_of_a_conversation(tmp_path):
    case = result_case("G01-1", ["c1", "c2"], secret_answer="追问的回答")
    first = {"question": "第一问", "answer": "第一问的回答 [c1]",
             "citations": [{"marker": "c1", "filename": "r.pdf", "location_label": "第 2 页",
                            "content": "期末值2.00元"}]}  # fmt: skip
    case["turns"].insert(0, first)
    result = {"groups": [{"id": "G01", "repeat_index": 0,
                          "cases": [case, result_case("G01-2", [])]}]}  # fmt: skip
    path = packets.build_packets(result, SUITE, tmp_path / "packets", "run.json")[0]
    multi, single = json.loads(path.read_text("utf-8"))["cases"]
    assert multi["answer"] == "追问的回答"
    assert multi["earlier_turns"] == [
        {"question": "第一问", "answer": "第一问的回答 [c1]",
         "citations": [{"marker": "c1", "filename": "r.pdf", "location_label": "第 2 页",
                        "content": "期末值2.00元"}]}
    ]  # fmt: skip
    assert "earlier_turns" not in single


def test_verdicts_reject_missing_and_unknown_claims(built, tmp_path):
    packet = built["G01-r1.json"]
    path = verdict_file(tmp_path / "x.json", packet,
                        [v("G01-1", "c1"), v("G01-1", "zz"), v("G01-2", packets.ANSWER_CLAIM)])  # fmt: skip
    with pytest.raises(ValueError, match=r"缺少.*G01-1/c2.*多余.*G01-1/zz"):
        packets.load_verdicts(packet, path)


def test_verdicts_reject_bad_enum_and_wrong_group(built, tmp_path):
    packet = built["G01-r1.json"]
    rows = [v("G01-1", "c1", correct="maybe"), v("G01-1", "c2"), v("G01-2", packets.ANSWER_CLAIM)]
    with pytest.raises(ValueError, match="correct"):
        packets.load_verdicts(packet, verdict_file(tmp_path / "a.json", packet, rows))
    other = built["G02-r1.json"]
    ok_rows = [v("G01-1", "c1"), v("G01-1", "c2"), v("G01-2", packets.ANSWER_CLAIM)]
    wrong = verdict_file(tmp_path / "b.json", other, ok_rows)
    with pytest.raises(ValueError, match="组"):
        packets.load_verdicts(packet, wrong)


def test_needs_review_rules():
    rule_ok = {"value_signal": True, "rule_satisfied": True}
    rule_bad = {"value_signal": False, "rule_satisfied": False}
    assert packets.needs_review({"correct": "partial"}, rule_ok)
    assert packets.needs_review({"correct": "yes"}, rule_bad)
    assert packets.needs_review({"correct": "no"}, rule_ok)
    assert packets.needs_review(None, rule_ok)
    assert not packets.needs_review({"correct": "yes"}, rule_ok)
    assert not packets.needs_review({"correct": "no"}, rule_bad)


def test_merge_prefers_human_then_review():
    initial = [{**v("G01-1", "c1", "partial"), "group_id": "G01", "repeat_index": 0}]
    review = [{**v("G01-1", "c1", "no"), "group_id": "G01", "repeat_index": 0}]
    human = [{"case_id": "G01-1", "claim_id": "c1", "repeat_index": 0,
              "correct": "yes", "citation_supports": "supported"}]  # fmt: skip
    merged = packets.merge(initial, review, None)[0]
    assert merged["final"]["correct"] == "no" and merged["source"] == "review"
    merged = packets.merge(initial, review, human)[0]
    assert merged["final"]["correct"] == "yes" and merged["source"] == "human"
    assert merged["initial"]["correct"] == "partial"


def test_semantic_pass_requires_supported_citation():
    yes = {"final": {"correct": "yes", "citation_supports": "supported"}, "requires_evidence": True}
    uncited = {"final": {"correct": "yes", "citation_supports": "no_citation"},
               "requires_evidence": True}  # fmt: skip
    no_evidence_needed = {**uncited, "requires_evidence": False}
    assert packets.case_semantic_pass([yes], True)
    assert not packets.case_semantic_pass([yes, uncited], True)
    assert packets.case_semantic_pass([no_evidence_needed], True)
    assert not packets.case_semantic_pass([yes], False)


def merged_rows():
    rows = []
    for group, cases in (("G01", [("G01-1", ["c1", "c2"]), ("G01-2", [packets.ANSWER_CLAIM])]),
                         ("G02", [("G02-1", ["c1"])])):  # fmt: skip
        for case_id, claim_ids in cases:
            for claim_id in claim_ids:
                rows.append(
                    {"group_id": group, "repeat_index": 0, "case_id": case_id,
                     "claim_id": claim_id, "source": "initial", "flagged": False,
                     "final": {"correct": "yes", "citation_supports": "supported"},
                     "question": "q", "answer": "a", "reference": "r"}
                )  # fmt: skip
    return rows


def test_sample_covers_every_group_dimension_and_forced_items():
    rows = merged_rows()
    rows[1]["flagged"] = True
    picked = audit.sample(rows, SUITE, rate=0.2)
    keys = {(r["case_id"], r["claim_id"]) for r in picked}
    assert ("G01-1", "c2") in keys
    assert {r["group_id"] for r in picked} == {"G01", "G02"}
    assert picked == audit.sample(rows, SUITE, rate=0.2)


def test_sheet_roundtrip_and_blank_human_columns_rejected(tmp_path):
    rows = audit.sample(merged_rows(), SUITE, rate=1.0)
    path = tmp_path / "audit.csv"
    audit.write_sheet(rows, path)
    with pytest.raises(ValueError, match="第 2 行"):
        audit.read_sheet(path)
    text = path.read_text("utf-8-sig").splitlines()
    header = text[0].split(",")
    filled = [text[0]]
    for line in text[1:]:
        cells = line.split(",")
        cells[header.index("human_correct")] = "yes"
        cells[header.index("human_citation")] = "supported"
        filled.append(",".join(cells))
    path.write_text("\n".join(filled) + "\n", encoding="utf-8-sig")
    assert len(audit.read_sheet(path)) == len(rows)


def test_agreement_kappa_perfect_and_disagreement():
    perfect = [{"judge_correct": a, "human_correct": a, "judge_citation": "supported",
                "human_citation": "supported"} for a in ("yes", "no", "partial", "yes")]  # fmt: skip
    assert audit.agreement(perfect)["correct"] == {"accuracy": 1.0, "kappa": 1.0, "n": 4}
    flipped = [{**row, "human_correct": {"yes": "no", "no": "yes", "partial": "partial"}[
        row["judge_correct"]]} for row in perfect]  # fmt: skip
    assert audit.agreement(flipped)["correct"]["kappa"] < 0


def test_packets_contain_no_secrets(built):
    text = "".join(path.read_text("utf-8") for path in built.values())
    assert "api_key" not in text and "base_url" not in text
