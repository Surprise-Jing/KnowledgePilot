"""Real-disclosure annotations and adversarial scoring checks; never download in tests."""

import copy
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"
sys.path.insert(0, str(EVAL_DIR))

from finance import dataset, scoring
from finance.run import required_groups

MANIFEST, EVIDENCE, CASES = dataset.load()
DOCUMENTS = {doc["id"]: doc for doc in MANIFEST["documents"]}


def citation(key, marker="c1"):
    item = EVIDENCE[key]
    return {
        "marker": marker,
        "filename": DOCUMENTS[item["document"]]["filename"],
        "location": {"kind": "pdf_page", "start": item["page"], "end": item["page"]},
        "content": item["anchor"],
    }


def turn(answer, citations):
    return {
        "answer": answer,
        "citations": citations,
        "stop_reason": "answered",
        "error_code": None,
        "event_order_ok": True,
    }


def test_dataset_has_balanced_tasks_and_fixed_original_sources():
    assert dataset.validate(MANIFEST, EVIDENCE, CASES) == {
        "documents": 4,
        "evidence": 142,
        "cases": 24,
    }
    assert Counter(case["category"] for case in CASES) == {
        "查询分解": 6,
        "多文档对比": 6,
        "引用聚合": 6,
        "口径与边界": 6,
    }
    assert Counter(case["split"] for case in CASES) == {"dev": 16, "test": 8}
    assert sum(len(case["claims"]) for case in CASES) == 137
    assert sum(doc["pages"] for doc in DOCUMENTS.values()) == 58
    assert all(len(case["documents"]) >= 2 for case in CASES)
    assert all(case["review_required"] for case in CASES)
    for doc in DOCUMENTS.values():
        assert doc["url"].startswith("https://cdn.efunds.com.cn/owch/data/bulletin/")
        assert len(doc["sha256"]) == 64
        assert doc["publication_date"] > doc["report_period_end"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_calculations_recompute_from_atomic_disclosures(case):
    for spec in case["calculations"]:
        assert dataset.calculate(spec, EVIDENCE) == Decimal(spec["expected"])
        assert spec["id"] in {atom["id"] for atom in case["claims"]}


def test_annotations_reject_scope_leakage_and_wrong_arithmetic():
    cases = copy.deepcopy(CASES)
    cases[-2]["claims"][0]["evidence_groups"] = [["c300_2025q1.return"]]
    with pytest.raises(ValueError, match="越界"):
        dataset.validate(MANIFEST, EVIDENCE, cases)
    cases = copy.deepcopy(CASES)
    cases[1]["calculations"][0]["expected"] = "999"
    with pytest.raises(ValueError, match="计算标注错误"):
        dataset.validate(MANIFEST, EVIDENCE, cases)


def test_true_cross_document_overlap_uses_stock_codes():
    def stocks(key):
        return {row["code"] for row in EVIDENCE[key]["value"]}

    overlap = stocks("s50_2025q1.top10") & stocks("c300_2025q1.top10")
    assert overlap == {"600519", "601318", "600036", "600900", "601166", "601899"}


def test_share_consolidation_is_not_a_redemption():
    case = next(case for case in CASES if case["id"] == "BD02")
    assert dataset.calculate(case["calculations"][0], EVIDENCE) == Decimal(1439606830)
    assert Decimal(EVIDENCE["s50_2025q1.subscriptions"]["value"]) - Decimal(
        EVIDENCE["s50_2025q1.redemptions"]["value"]
    ) == Decimal(320400000)


@pytest.mark.parametrize(
    ("answer", "value", "unit", "expected"),
    [
        ("季度收益+0.55%", "-0.55", "percent", False),
        ("季度收益-0.55%", "-0.55", "percent", True),
        ("下降0.29个百分点", "-0.29", "percentage_point", True),
        ("变化0.29%", "0.29", "percentage_point", False),
        ("净申购3.204亿份", "320400000", "share", True),
        ("规模320400000元", "320400000", "share", False),
        ("申购1,104,900,000份", "1104900000", "share", True),
        ("数值120.55%", "20.55", "percent", False),
    ],
)
def test_numeric_signal_preserves_sign_scale_and_unit(answer, value, unit, expected):
    assert (
        scoring.number_signal(answer, {"value": value, "unit": unit, "tolerance": "0.005"})
        is expected
    )


def test_repeated_text_requires_four_independent_document_sources():
    case = next(case for case in CASES if case["id"] == "AG01")
    keys = [group[0] for group in required_groups(case)]
    partial = scoring.judge(
        case, turn("四份报告均采用完全复制法", [citation(keys[0])]), EVIDENCE, DOCUMENTS
    )
    assert partial["claim_evidence_coverage"] == 0
    assert not partial["rule_passed"]
    full = scoring.judge(
        case,
        turn("四份报告均采用完全复制法", [citation(key, f"c{i}") for i, key in enumerate(keys)]),
        EVIDENCE,
        DOCUMENTS,
    )
    assert full["rule_passed"]
    assert full["final_passed"] is None and full["semantic_review"] == "pending"


def test_citation_page_alone_does_not_prove_a_fact():
    key = "s50_2025q1.return"
    cite = citation(key)
    cite["content"] = "本页还有其他指标，但没有命中本题所需的证据行"
    assert not scoring.evidence_matches(EVIDENCE[key], cite, DOCUMENTS)


def test_markdown_table_delimiters_do_not_hide_a_correct_original_evidence_row():
    key = "s50_2025q1.active_return"
    cite = citation(key)
    cite["content"] = "|过去三个 月|-0.55%|0.90%|-0.71%|0.89%|0.16%|0.01%|"
    assert scoring.evidence_matches(EVIDENCE[key], cite, DOCUMENTS)
    cite["content"] = cite["content"].replace("0.16%", "9.99%")
    assert not scoring.evidence_matches(EVIDENCE[key], cite, DOCUMENTS)
    cite = citation(key)
    cite["filename"] = DOCUMENTS["s50_2024q4"]["filename"]
    assert not scoring.evidence_matches(EVIDENCE[key], cite, DOCUMENTS)


def test_table_and_narrative_are_alternative_valid_evidence():
    case = {
        "knowledge_bases": [DOCUMENTS["s50_2025q1"]["knowledge_base"]],
        "claims": [
            {
                "id": "return",
                "evidence_groups": [["s50_2025q1.return", "s50_2025q1.performance_return"]],
                "answer_keywords": [],
                "answer_numbers": [{"value": "-0.55", "unit": "percent", "tolerance": "0.005"}],
            }
        ],
    }
    result = scoring.judge(
        case,
        turn("一季度净值增长率为-0.55%", [citation("s50_2025q1.performance_return")]),
        EVIDENCE,
        DOCUMENTS,
    )
    assert result["rule_passed"]


def test_out_of_scope_source_and_duplicate_marker_are_rejected():
    case = next(case for case in CASES if case["id"] == "BD05")
    result = scoring.judge(
        case,
        turn(
            "510100收益为-0.55%，缺少510310依据",
            [citation("s50_2025q1.return"), citation("c300_2025q1.return")],
        ),
        EVIDENCE,
        DOCUMENTS,
    )
    assert not result["structural_checks"]["citation_scope"]
    assert not result["structural_checks"]["unique_markers"]


def test_pdf_tampering_fails_before_parsing(tmp_path):
    path = tmp_path / "changed.pdf"
    path.write_bytes(b"%PDF changed bytes")
    with pytest.raises(ValueError, match="哈希不匹配"):
        dataset.verify_pdf(MANIFEST["documents"][0], path)


def test_missing_sources_fail_without_network(tmp_path):
    with pytest.raises(FileNotFoundError):
        dataset.verify_sources(MANIFEST, EVIDENCE, tmp_path)


@pytest.mark.skipif(
    not all(dataset.source_path(doc).exists() for doc in MANIFEST["documents"]),
    reason="原始第三方PDF未缓存；显式运行 finance/run.py --fetch --verify-sources 验证",
)
def test_cached_real_pdfs_match_every_gold_anchor_and_reference_chunks(tmp_path):
    from app.parsers.base import ParseRequest
    from tests.fakes.parsers import LegacyPdfDocxParser

    assert dataset.verify_sources(MANIFEST, EVIDENCE) == {
        "verified_pdfs": 4,
        "verified_anchors": 142,
    }
    for doc in MANIFEST["documents"]:
        # Gold source validation stays independent of downloaded Docling models. Actual
        # application ingestion is exercised below through the injected parser interface.
        output_dir = tmp_path / doc["id"]
        output_dir.mkdir()
        chunks = (
            LegacyPdfDocxParser()
            .parse(ParseRequest(dataset.source_path(doc), "pdf", "standard", output_dir))
            .chunks
        )
        for item in EVIDENCE.values():
            if item["document"] != doc["id"]:
                continue
            assert any(
                chunk.start == item["page"]
                and dataset.normalize(item["anchor"]) in dataset.normalize(chunk.content)
                for chunk in chunks
            ), item["id"]


def test_original_twenty_cases_are_unchanged():
    cases = [json.loads(line) for line in (EVAL_DIR / "cases.jsonl").read_text().splitlines()]
    assert len(cases) == 20
    assert cases[0]["id"] == "F1" and cases[-1]["id"] == "E2"


def test_cli_accepts_repo_relative_config_after_uv_changes_directory(tmp_path, monkeypatch):
    from finance.run import main

    config = tmp_path / "backend" / "test.env"
    config.parent.mkdir()
    config.write_text("PK_LLM_MODEL=test\n", encoding="utf-8")
    monkeypatch.setattr(dataset, "REPO", tmp_path)
    monkeypatch.chdir(config.parent)
    assert main(["--env-file", "backend/test.env"]) == 0


@pytest.mark.skipif(
    not all(dataset.source_path(doc).exists() for doc in MANIFEST["documents"]),
    reason="需先显式下载真实PDF；测试不访问网络",
)
def test_finance_runner_upload_search_and_sse_with_injected_test_model(tmp_path):
    from fastapi.testclient import TestClient
    from finance.run import agent_case, retrieval_case, setup_corpus

    from app.config import Settings
    from app.main import create_app
    from tests.fakes.llm import ScriptedLLM, scripted_factory, text, tool_call

    settings = Settings(
        _env_file=None, data_dir=tmp_path, retriever="cosine", frontend_dir=tmp_path / "none"
    )
    with TestClient(create_app(settings)) as client:
        ids = setup_corpus(client, MANIFEST, dataset.CACHE)
        retrieval = retrieval_case(client, ids, CASES[0], EVIDENCE, DOCUMENTS, 10)
        assert retrieval["evidence_groups"] == len(required_groups(CASES[0]))
        assert retrieval["covered_groups"] > 0
        document_id = ids.documents[DOCUMENTS["s50_2025q1"]["filename"]]
        chunks = client.get(
            f"/api/documents/{document_id}/chunks", headers={"X-User-Id": "eval"}
        ).json()
        evidence = EVIDENCE["s50_2025q1.performance_return"]
        ordinal = next(
            chunk["ordinal"]
            for chunk in chunks
            if dataset.normalize(evidence["anchor"]) in dataset.normalize(chunk["content"])
        )
        llm = ScriptedLLM(
            [
                [
                    tool_call(
                        "read_document",
                        {"document_id": document_id, "start_ordinal": ordinal, "max_chars": 500},
                    )
                ],
                text("510100在2025年一季度净值增长率为-0.55%。[c1]"),
            ]
        )
        client.app.state.llm_factory = scripted_factory(llm)
        case = {
            "id": "runner-check",
            "category": "测试",
            "knowledge_bases": [DOCUMENTS["s50_2025q1"]["knowledge_base"]],
            "turns": [{"content": "510100在2025年一季度的收益是多少？"}],
            "claims": [
                {
                    "id": "quarter_return",
                    "evidence_groups": [[evidence["id"]]],
                    "answer_keywords": [],
                    "answer_numbers": [{"value": "-0.55", "unit": "percent", "tolerance": "0.005"}],
                }
            ],
            "semantic_rubric": ["核查基金、期间与收益口径"],
            "reference_answer": "-0.55%",
        }
        result = agent_case(client, ids, case, EVIDENCE, DOCUMENTS)
        assert result["rule_passed"]
        assert result["turns"][0]["citations"][0]["content"]
        assert result["final_passed"] is None
        assert llm.remaining == 0
