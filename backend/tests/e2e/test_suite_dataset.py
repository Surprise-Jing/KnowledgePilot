"""Suite v2 dataset schema, validation and source verification; never download in tests."""

import copy
import json
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"
sys.path.insert(0, str(EVAL_DIR))

from suite import dataset


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


MANIFEST = {
    "kb_id": "KBT",
    "retrieved_on": "2026-11-24",
    "documents": [
        {
            "id": "rpt",
            "filename": "report.pdf",
            "title": "测试报告",
            "knowledge_base": "测试库",
            "folder": ["报告"],
            "url": "https://example.org/report.pdf",
            "source_type": "original_pdf",
            "license": "public",
            "sha256": "0" * 64,
            "bytes": 10,
            "pages": 3,
            "lines": None,
        },
        {
            "id": "notes",
            "filename": "notes.txt",
            "title": "测试笔记",
            "knowledge_base": "测试库",
            "folder": [],
            "url": "https://example.org/notes.html",
            "source_type": "html_body_as_txt",
            "license": "public",
            "sha256": "1" * 64,
            "bytes": 10,
            "pages": None,
            "lines": 10,
            "raw": {"url": "https://example.org/notes.html", "sha256": "2" * 64},
        },
    ],
}

EVIDENCE = {
    "rpt.p1": {
        "id": "rpt.p1",
        "document": "rpt",
        "locator": {"kind": "pdf_page", "page": 1},
        "label": "期初值",
        "value": "1.00",
        "unit": "CNY",
        "anchor": "期初值1.00元",
    },
    "rpt.p2": {
        "id": "rpt.p2",
        "document": "rpt",
        "locator": {"kind": "pdf_page", "page": 2},
        "label": "期末值",
        "value": "2.00",
        "unit": "CNY",
        "anchor": "期末值2.00元",
    },
    "notes.l3": {
        "id": "notes.l3",
        "document": "notes",
        "locator": {"kind": "txt_lines", "line": 3},
        "label": "结论",
        "value": "增长",
        "unit": None,
        "anchor": "全年保持增长",
    },
}


def claim(claim_id: str, evidence_id: str) -> dict:
    return {
        "id": claim_id,
        "label": claim_id,
        "evidence_groups": [[evidence_id]],
        "answer_keywords": [],
        "answer_numbers": [],
    }


def case(case_id: str, claims: list[dict], calculations: list | None = None) -> dict:
    return {
        "id": case_id,
        "origin": "drafted",
        "source_ref": None,
        "documents": ["rpt", "notes"],
        "forbidden_documents": [],
        "turns": [{"content": "问题"}],
        "claims": claims,
        "calculations": calculations or [],
        "expect": {
            "no_answer": False,
            "tools": [],
            "forbidden_tools": [],
            "stop_reason": "answered",
            "error_code": None,
        },
        "reference_answer": "参考答案",
        "semantic_rubric": ["数值正确"],
        "review": {"status": "pending", "note": ""},
    }


GROUP = {
    "id": "G01",
    "title": "测试组",
    "knowledge_bases": ["测试库"],
    "split": "dev",
    "dimensions": ["D1", "D2"],
    "compare": False,
    "demo_candidate": False,
    "overrides": {},
    "changelog": [],
    "cases": [
        case("G01-1", [claim("c1", "rpt.p1"), claim("c2", "notes.l3")]),
        case(
            "G01-2",
            [claim("c3", "rpt.p2")],
            [
                {
                    "id": "delta",
                    "operation": "subtract",
                    "operands": ["rpt.p2", "rpt.p1"],
                    "expected": "1.00",
                    "unit": "CNY",
                    "decimal_places": 2,
                }
            ],
        ),
    ],
}


def build(root: Path, group: dict | None = None, evidence: dict | None = None) -> Path:
    write_json(root / "kbs/KBT/manifest.json", MANIFEST)
    write_json(root / "kbs/KBT/evidence.json", evidence or EVIDENCE)
    write_json(root / "groups/G01.json", group or GROUP)
    (root / "judge").mkdir(parents=True, exist_ok=True)
    (root / "judge/PROMPT.md").write_text("评审说明", encoding="utf-8")
    return root


@pytest.fixture
def tmp_suite(tmp_path) -> Path:
    return build(tmp_path / "suite")


def test_minimal_suite_validates(tmp_suite):
    stats = dataset.validate(dataset.load(tmp_suite))
    assert stats == {
        "knowledge_bases": 1,
        "documents": 2,
        "evidence": 3,
        "groups": 1,
        "cases": 2,
        "claims": 3,
    }


def _missing_evidence(g, e):
    g["cases"][0]["claims"][0]["evidence_groups"].append(["missing.id"])


def _unknown_kb(g, e):
    g["knowledge_bases"] = ["不存在的库"]


def _bad_split(g, e):
    g["split"] = "x"


def _duplicate_case(g, e):
    g["cases"][1]["id"] = g["cases"][0]["id"]


def _bad_review(g, e):
    g["cases"][0]["review"]["status"] = "ok"


def _bad_dimension(g, e):
    g["dimensions"] = ["D10"]


def _wrong_locator_kind(g, e):
    e["notes.l3"]["locator"] = {"kind": "pdf_page", "page": 1}


def _page_out_of_range(g, e):
    e["rpt.p1"]["locator"]["page"] = 9


@pytest.mark.parametrize(
    "mutate,message",
    [
        (_missing_evidence, "missing.id"),
        (_unknown_kb, "知识库"),
        (_bad_split, "split"),
        (_duplicate_case, "重复用例"),
        (_bad_review, "review"),
        (_bad_dimension, "D10"),
        (_wrong_locator_kind, "notes.l3"),
        (_page_out_of_range, "rpt.p1"),
    ],
)
def test_invalid_annotations_rejected(tmp_path, mutate, message):
    group, evidence = copy.deepcopy(GROUP), copy.deepcopy(EVIDENCE)
    mutate(group, evidence)
    root = build(tmp_path / "suite", group, evidence)
    with pytest.raises(ValueError, match=message):
        dataset.validate(dataset.load(root))


def test_evidence_outside_group_knowledge_bases_rejected(tmp_path):
    group = copy.deepcopy(GROUP)
    group["cases"][0]["documents"] = ["rpt"]
    root = build(tmp_path / "suite", group)
    with pytest.raises(ValueError, match="证据越界"):
        dataset.validate(dataset.load(root))


def test_wrong_calculation_rejected(tmp_path):
    group = copy.deepcopy(GROUP)
    group["cases"][1]["calculations"][0]["expected"] = "3.00"
    root = build(tmp_path / "suite", group)
    with pytest.raises(ValueError, match="计算标注错误"):
        dataset.validate(dataset.load(root))


def test_long_anchor_rejected(tmp_path):
    evidence = copy.deepcopy(EVIDENCE)
    evidence["notes.l3"]["anchor"] = "长" * 121
    root = build(tmp_path / "suite", evidence=evidence)
    with pytest.raises(ValueError, match="锚点过长"):
        dataset.validate(dataset.load(root))


def test_no_answer_case_may_have_claims_without_evidence(tmp_path):
    group = copy.deepcopy(GROUP)
    group["cases"][0]["expect"]["no_answer"] = True
    group["cases"][0]["claims"] = [
        {**claim("c1", "rpt.p1"), "evidence_groups": []},
    ]
    root = build(tmp_path / "suite", group)
    assert dataset.validate(dataset.load(root))["claims"] == 2


def test_coverage_counts_and_constraints(tmp_suite):
    cov = dataset.coverage(dataset.load(tmp_suite))
    assert cov["dimension_groups"]["D1"] == 1
    assert cov["format_groups"] == {"pdf": 1, "docx": 0, "txt": 1}
    assert cov["partial_scope_groups"] == 0
    assert cov["multi_turn_groups"] == 0
    problems = " ".join(dataset.check_coverage(cov))
    assert "D1 至少 3 组" in problems
    assert "docx 至少 4 组" in problems


def test_fingerprint_changes_when_prompt_changes(tmp_suite):
    before = dataset.fingerprint(tmp_suite)
    (tmp_suite / "judge/PROMPT.md").write_text("新的评审说明", encoding="utf-8")
    assert dataset.fingerprint(tmp_suite) != before


# ---- Task 2: source fetching, conversion and anchor verification ----

import hashlib

import build_corpus
import httpx
from suite import sources

PDF_SOURCE = "Opening value 1.00 CNY\n=== page ===\nClosing value 2.00 CNY"
TXT_SOURCE = "标题\n第一段\n全年保持增长\n其他内容"
DOCX_SOURCE = (
    "# 第一章 总则\n第一条 本法适用于证券发行。\n# 第二章 信息披露\n第二条 应当真实准确完整。"
)


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def real_suite(tmp_path: Path, txt_line: int = 3, pdf_page: int = 2, heading: str | None = None):
    """A suite whose three documents exist in a local cache with their true hashes."""
    payloads = {
        "rpt.pdf": build_corpus.render_pdf(PDF_SOURCE),
        "notes.txt": TXT_SOURCE.encode(),
        "law.docx": build_corpus.render_docx(DOCX_SOURCE),
    }
    from io import BytesIO

    from pypdf import PdfReader

    documents = []
    for doc_id, (filename, payload) in zip(("rpt", "notes", "law"), payloads.items()):
        fmt = filename.rsplit(".", 1)[1]
        documents.append(
            {
                "id": doc_id,
                "filename": filename,
                "title": doc_id,
                "knowledge_base": "测试库",
                "folder": [],
                "url": f"https://example.org/{filename}",
                "source_type": {
                    "pdf": "original_pdf",
                    "txt": "html_body_as_txt",
                    "docx": "original_docx",
                }[fmt],
                "license": "public",
                "sha256": sha(payload),
                "bytes": len(payload),
                "pages": len(PdfReader(BytesIO(payload)).pages) if fmt == "pdf" else None,
                "lines": len(TXT_SOURCE.splitlines()) if fmt == "txt" else None,
            }
        )
    documents[1]["raw"] = {"url": "https://example.org/notes.html", "sha256": "2" * 64}
    evidence = {
        "rpt.close": {
            "id": "rpt.close",
            "document": "rpt",
            "locator": {"kind": "pdf_page", "page": pdf_page},
            "label": "期末值",
            "value": "2.00",
            "unit": "CNY",
            "anchor": "Closing value 2.00",
        },
        "notes.l3": {
            "id": "notes.l3",
            "document": "notes",
            "locator": {"kind": "txt_lines", "line": txt_line},
            "label": "结论",
            "value": "增长",
            "unit": None,
            "anchor": "全年保持增长",
        },
        "law.a2": {
            "id": "law.a2",
            "document": "law",
            "locator": {"kind": "docx", **({"heading": heading} if heading else {})},
            "label": "第二条",
            "value": "真实",
            "unit": None,
            "anchor": "第二条 应当真实准确完整",
        },
    }
    root = tmp_path / "suite"
    write_json(
        root / "kbs/KBT/manifest.json",
        {"kb_id": "KBT", "retrieved_on": "2026-11-24", "documents": documents},
    )
    write_json(root / "kbs/KBT/evidence.json", evidence)
    (root / "groups").mkdir(parents=True)
    cache = tmp_path / "cache"
    for filename, payload in payloads.items():
        (cache / "KBT").mkdir(parents=True, exist_ok=True)
        (cache / "KBT" / filename).write_bytes(payload)
    return dataset.load(root), cache


def mock_client(payload: bytes) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    )


def test_cached_sources_verify_every_anchor(tmp_path):
    suite, cache = real_suite(tmp_path, heading="第二章 信息披露")
    assert sources.verify(suite, cache) == {"verified_documents": 3, "verified_anchors": 3}


def test_fetch_rejects_changed_hash(tmp_path):
    suite, cache = real_suite(tmp_path)
    pdf = next(doc for doc in suite.documents.values() if doc["id"] == "rpt")
    sources.source_path(pdf, cache).unlink()
    with pytest.raises(ValueError, match="哈希"):
        sources.fetch(suite, cache, client=mock_client(b"%PDF changed"))
    assert not sources.source_path(pdf, cache).exists()
    assert not list((cache / "KBT").glob("*.download"))


def test_pin_records_missing_hash_and_never_overwrites_existing(tmp_path):
    suite, cache = real_suite(tmp_path)
    pdf = suite.documents["rpt"]
    payload = sources.source_path(pdf, cache).read_bytes()
    sources.source_path(pdf, cache).unlink()
    pdf.update(sha256="", bytes=None, pages=None)
    pinned = sources.fetch(suite, cache, client=mock_client(payload), pin=True)
    assert pinned["rpt"] == {"sha256": sha(payload), "bytes": len(payload), "pages": 2}
    suite.documents["rpt"]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="哈希"):
        sources.fetch(suite, cache, client=mock_client(payload), pin=True)


def test_html_to_text_keeps_text_drops_scripts():
    html = "<html><body><nav>导航</nav><p>一、综合</p><script>x()</script><p>GDP  增长 5.0%</p></body></html>"
    assert sources.html_to_text(html) == "一、综合\nGDP 增长 5.0%"


def test_html_body_fetch_checks_raw_and_converted_hashes(tmp_path):
    suite, cache = real_suite(tmp_path)
    notes = suite.documents["notes"]
    html = ("<p>" + "</p><p>".join(TXT_SOURCE.splitlines()) + "</p>").encode()
    notes["raw"]["sha256"] = sha(html)
    sources.source_path(notes, cache).unlink()
    sources.fetch(suite, cache, client=mock_client(html))
    assert sources.source_path(notes, cache).read_text(encoding="utf-8") == TXT_SOURCE


def test_txt_anchor_must_be_on_its_line(tmp_path):
    suite, cache = real_suite(tmp_path, txt_line=4)
    with pytest.raises(ValueError, match="notes.l3"):
        sources.verify(suite, cache)


def test_pdf_anchor_on_wrong_page_rejected(tmp_path):
    suite, cache = real_suite(tmp_path, pdf_page=1)
    with pytest.raises(ValueError, match="rpt.close"):
        sources.verify(suite, cache)


def test_docx_heading_required_when_given(tmp_path):
    suite, cache = real_suite(tmp_path, heading="第九章 不存在")
    with pytest.raises(ValueError, match="law.a2"):
        sources.verify(suite, cache)


def test_missing_source_fails_without_network(tmp_path):
    suite, _cache = real_suite(tmp_path)
    with pytest.raises(ValueError, match="缺少原文"):
        sources.verify(suite, tmp_path / "empty-cache")


def test_html_to_text_can_limit_to_first_container():
    html = (
        "<div class='menu'>首页</div><div class='a trs_editor_view b'><div><p>一、综合</p></div>"
        "<br><p>GDP 增长</p></div><div class='trs_editor_view'><p>移动版副本</p></div>"
    )
    assert sources.html_to_text(html, container_class="trs_editor_view") == "一、综合\nGDP 增长"
    with pytest.raises(ValueError, match="正文容器"):
        sources.html_to_text("<p>x</p>", container_class="trs_editor_view")


def test_pin_records_raw_html_hash_when_missing(tmp_path):
    suite, cache = real_suite(tmp_path)
    notes = suite.documents["notes"]
    html = ("<p>" + "</p><p>".join(TXT_SOURCE.splitlines()) + "</p>").encode()
    sources.source_path(notes, cache).unlink()
    notes["raw"]["sha256"] = ""
    notes.update(sha256="", bytes=None, lines=None)
    pinned = sources.fetch(suite, cache, client=mock_client(html), pin=True)
    assert pinned["notes"]["raw"]["sha256"] == sha(html)
    assert pinned["notes"]["lines"] == 4


# ---- Task 8: KB6 multi-hop knowledge base ----

import importlib.util


def _kb6_build():
    path = EVAL_DIR / "suite/kbs/KB6/build.py"
    spec = importlib.util.spec_from_file_location("kb6_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOTPOT_ROWS = [
    {
        "row": {
            "id": "h1",
            "question": "Were A and B the same nationality?",
            "answer": "yes",
            "type": "comparison",
            "level": "hard",
            "supporting_facts": {"title": ["Alpha Person", "Beta Person"], "sent_id": [0, 1]},
            "context": {
                "title": ["Alpha Person", "Beta Person", "Gamma"],
                "sentences": [
                    ["Alpha is American. ", "Born 1960."],
                    ["Beta Person is a film.", " Beta is American."],
                    ["Gamma is a town."],
                ],
            },
        }
    },
    {
        "row": {
            "id": "h2",
            "question": "Where was Alpha born?",
            "answer": "Ohio",
            "type": "bridge",
            "level": "medium",
            "supporting_facts": {"title": ["Alpha Person"], "sent_id": [1]},
            "context": {
                "title": ["Alpha Person", "Delta/Echo"],
                "sentences": [["Alpha is American. ", "Born 1960."], ["Delta text."]],
            },
        }
    },
]
MUSIQUE_ROWS = [
    {"row": {"id": "2hop__1_2", "question": "Who founded the firm?", "answer": "Carol",
             "answer_aliases": [], "answerable": True,
             "paragraphs": [{"idx": 0, "title": "Gamma", "paragraph_text": "Gamma hosts the firm.",
                             "is_supporting": True},
                            {"idx": 1, "title": "Firm", "paragraph_text": "Carol founded the firm.",
                             "is_supporting": True},
                            {"idx": 2, "title": "Other", "paragraph_text": "Unrelated.",
                             "is_supporting": False}]}},
    {"row": {"id": "3hop1__9_8_7", "question": "Unanswerable?", "answer": "X", "answer_aliases": [],
             "answerable": False,
             "paragraphs": [{"idx": 0, "title": "Other", "paragraph_text": "Unrelated.",
                             "is_supporting": False}]}},
]  # fmt: skip


@pytest.fixture
def kb6_raw(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "hotpot-0.json").write_text(json.dumps({"rows": HOTPOT_ROWS}), encoding="utf-8")
    (raw / "musique-0.json").write_text(json.dumps({"rows": MUSIQUE_ROWS}), encoding="utf-8")
    return {"hotpot": [raw / "hotpot-0.json"], "musique": [raw / "musique-0.json"]}


def _file_lines(out: Path, docs: list[dict], doc_id: str) -> list[str]:
    doc = next(d for d in docs if d["id"] == doc_id)
    return (out / doc["filename"]).read_text("utf-8").splitlines()


def test_kb6_merges_entries_by_category_and_maps_supporting_lines(kb6_raw, tmp_path):
    build = _kb6_build()
    out = tmp_path / "out"
    docs, questions = build.build(kb6_raw, out, n_hotpot=2, n_musique=2, seed=1)
    h2 = next(q for q in questions if q["source_id"] == "h2")
    [support] = h2["supporting"]
    lines = _file_lines(out, docs, support["document"])
    assert lines[support["line"] - 1] == "Born 1960." == support["anchor"]
    # The entry keeps its title line right above its own sentences.
    start = lines.index("【Alpha Person】")
    assert lines[start + 1 : start + 3] == ["Alpha is American.", "Born 1960."]
    m1 = next(q for q in questions if q["source_id"] == "2hop__1_2")
    texts = [_file_lines(out, docs, s["document"])[s["line"] - 1] for s in m1["supporting"]]
    assert texts == ["Gamma hosts the firm.", "Carol founded the firm."]
    unanswerable = next(q for q in questions if q["source_id"] == "3hop1__9_8_7")
    assert unanswerable["answerable"] is False and unanswerable["supporting"] == []
    assert len(docs) < 6  # six titles, merged into fewer files
    for doc in docs:
        payload = (out / doc["filename"]).read_bytes()
        assert doc["sha256"] == sha(payload) and doc["lines"] == len(payload.decode().splitlines())
        assert doc["source_type"] == "dataset_paragraphs_as_txt"
        assert doc["folder"] == [doc["filename"].split("-")[0]]


def test_kb6_supporting_entries_of_one_question_never_share_a_file(tmp_path):
    build = _kb6_build()
    films = {"title": ["Film One", "Film Two", "Film Three"],
             "sentences": [["Film One is a 1999 film."], ["Film Two is a 2001 film."],
                           ["Film Three is a 2003 film."]]}  # fmt: skip
    rows = [{"row": {"id": "c1", "question": "Which film came first?", "answer": "Film One",
                     "type": "comparison", "level": "easy", "context": films,
                     "supporting_facts": {"title": ["Film One", "Film Two"], "sent_id": [0, 0]}}}]  # fmt: skip
    raw = tmp_path / "h.json"
    raw.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    docs, questions = build.build({"hotpot": [raw], "musique": []}, tmp_path / "o", 1, 0, 1)
    [question] = questions
    assert len({s["document"] for s in question["supporting"]}) == 2
    assert {d["folder"][0] for d in docs} == {"电影"}
    # The non-supporting film still shares a file with one of them.
    assert len(docs) == 2


def test_kb6_respects_the_size_cap(kb6_raw, tmp_path):
    build = _kb6_build()
    docs, _ = build.build(kb6_raw, tmp_path / "o", 2, 2, 1, max_chars=40)
    for doc in docs:
        entries = (tmp_path / "o" / doc["filename"]).read_text("utf-8").count("【")
        assert doc["bytes"] <= 40 * 3 or entries == 1


@pytest.mark.parametrize(
    "first_line,category",
    [
        ("Inception is a 2010 science fiction film directed by Christopher Nolan.", "电影"),
        ("Friends is an American television sitcom.", "电视"),
        ("Thriller is the sixth studio album by Michael Jackson.", "音乐"),
        ("Ed Wood (October 10, 1924 – December 10, 1978) was an American filmmaker.", "人物"),
        ("Scott Derrickson (born July 16, 1966) is an American director.", "人物"),
        ("Woodson is a town in Pulaski County, Arkansas.", "地点"),
        ("Stanford University is a private research university.", "机构"),
        ("The Battle of Hastings was fought in 1066.", "事件"),
        ("Photosynthesis converts light into chemical energy.", "其他"),
        ("Robert Caesar Childers (1838 – 25 July 1876) was a British Orientalist scholar.", "人物"),
        ("Lionel Ngakane (17 July 1928 – 26 November 2003) was a South African filmmaker.", "人物"),
        ("John Smith was an English footballer who played as a striker.", "人物"),
        ("Three Tall Women is a play by Edward Albee.", "作品"),
        ("Chakan: The Forever Man is a Sega Mega Drive video game.", "作品"),
        ("Francesca Schiavone was the defending champion, but lost in the final.", "体育"),
    ],
)
def test_kb6_categorize(first_line, category):
    assert _kb6_build().categorize(first_line) == category


def test_kb6_build_is_deterministic(kb6_raw, tmp_path):
    build = _kb6_build()
    first = build.build(kb6_raw, tmp_path / "a", n_hotpot=1, n_musique=1, seed=7)
    second = build.build(kb6_raw, tmp_path / "b", n_hotpot=1, n_musique=1, seed=7)
    assert first == second


def test_kb6_build_keeps_one_paragraph_per_line_despite_unicode_separators(tmp_path):
    build = _kb6_build()
    rows = [{"row": {**MUSIQUE_ROWS[0]["row"], "paragraphs": [
        {"idx": 0, "title": "Sep", "paragraph_text": "Part one\u2028part two\x85end.",
         "is_supporting": True}]}}]  # fmt: skip
    raw = tmp_path / "m.json"
    raw.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    docs, questions = build.build({"hotpot": [], "musique": [raw]}, tmp_path / "o", 0, 1, 1)
    text = (tmp_path / "o" / docs[0]["filename"]).read_text("utf-8")
    assert text.splitlines() == ["【Sep】", "Part one part two end."]
    assert docs[0]["lines"] == 2 and questions[0]["supporting"][0]["line"] == 2


# ---- Task 9: finance import ----


def test_finance_import_preserves_every_claim_and_value():
    from finance import dataset as finance

    _, f_evidence, f_cases = finance.load()
    evidence, cases = dataset.import_finance(EVAL_DIR / "finance")
    assert len(cases) == 24
    assert sum(len(c["claims"]) for c in cases) == 137
    assert sum(len(c["calculations"]) for c in cases) == 20
    assert set(evidence) == set(f_evidence)
    for key, item in f_evidence.items():
        assert evidence[key]["value"] == item["value"]
        assert evidence[key]["locator"] == {"kind": "pdf_page", "page": item["page"]}
        assert evidence[key]["anchor"] == item["anchor"]
    original = {case["id"]: case for case in f_cases}
    for case in cases:
        assert case["origin"] == "finance-v1" and case["review"]["status"] == "approved"
        assert case["claims"] == original[case["id"]]["claims"]
        assert case["turns"] == original[case["id"]]["turns"]
        assert case["knowledge_bases"] == original[case["id"]]["knowledge_bases"]


def test_d9_needs_only_two_groups():
    cov = {"dimension_groups": {d: (2 if d == "D9" else 3) for d in dataset.DIMENSIONS},
           "format_groups": {"pdf": 4, "docx": 4, "txt": 4},
           "partial_scope_groups": 3, "multi_turn_groups": 4}  # fmt: skip
    assert dataset.check_coverage(cov) == []


# ---- Task 11: review sheet ----


def test_review_render_lists_every_case_and_evidence(tmp_suite):
    from suite import review

    text = review.render(dataset.load(tmp_suite))
    for case_id in ("G01-1", "G01-2"):
        assert f"### {case_id}" in text
    assert "期初值1.00元" in text and "report.pdf 第 1 页" in text
    assert "notes.txt 第 3 行" in text
    assert "subtract" in text and "审阅状态：pending" in text
    assert "审阅意见：" in text


def test_pin_uses_a_manually_downloaded_file_without_network(tmp_path):
    suite, cache = real_suite(tmp_path)
    law = suite.documents["law"]
    payload = sources.source_path(law, cache).read_bytes()
    law.update(sha256="", bytes=None)

    def refuse(request):
        raise AssertionError("must not download")

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    pinned = sources.fetch(suite, cache, client=client, pin=True)
    assert pinned["law"] == {"sha256": sha(payload), "bytes": len(payload)}


def test_export_materials_mirrors_knowledge_bases_and_folders(tmp_path):
    suite, cache = real_suite(tmp_path)
    suite.documents["rpt"]["folder"] = ["2025", "Q3"]
    (cache / "KBT" / "stale.txt").write_text("not in the manifest", encoding="utf-8")
    counts = sources.export_materials(suite, tmp_path / "materials", cache)
    assert counts == {"测试库": 3}
    exported = tmp_path / "materials" / "测试库"
    assert (exported / "2025" / "Q3" / "rpt.pdf").read_bytes() == sources.source_path(
        suite.documents["rpt"], cache
    ).read_bytes()
    assert (exported / "notes.txt").exists() and (exported / "law.docx").exists()
    assert not list(exported.rglob("stale.txt"))


# ---- dimension statistics report ----


def test_dimension_report_counts_groups_cases_claims_and_features(tmp_path):
    from suite import report

    group = copy.deepcopy(GROUP)
    group["cases"][1]["turns"].append({"content": "追问"})
    group["cases"][0]["expect"]["tools"] = ["list_files"]
    root = build(tmp_path / "suite", group)
    text = report.dimension_report(dataset.load(root), extra_questions=[{"answerable": True}] * 3)
    assert "| D1 | 检索准确 | 1 | 2 | 3 | 2 | 0 | G01 |" in text
    assert "| D9 | 异常与降级 | 0 | 0 | 0 | 0 | 0 | — |" in text
    assert "| 多轮追问 | 1 |" in text
    assert "| 含计算 | 1 |" in text
    assert "| 期望特定工具 | 1 |" in text
    assert "| 测试库 | 2 | 2 | 3 | 3 |" in text
    assert "| pdf | 1 | 2 |" in text and "| docx | 0 | 0 |" in text
    assert "检索对比扩展查询：3" in text
    # Same scope as the group and no forbidden documents: not a scope-isolation case.
    assert "| 限定范围或禁用文档 | 0 |" in text
    assert "| 明确无答案 | 0 |" in text and "| 部分缺失需说明 | 0 |" in text


def test_dimension_report_partial_missing_and_narrow_scope(tmp_path):
    from suite import report

    group = copy.deepcopy(GROUP)
    group["cases"][0]["claims"].append(
        {"id": "gap", "label": "未披露", "evidence_groups": [],
         "answer_keywords": [["未披露", "没有提及"]], "answer_numbers": []}
    )  # fmt: skip
    group["cases"][1]["forbidden_documents"] = ["notes"]
    group["cases"][1]["documents"] = ["rpt"]
    root = build(tmp_path / "suite", group)
    text = report.dimension_report(dataset.load(root))
    assert "| 部分缺失需说明 | 1 | G01-1 |" in text
    assert "| 限定范围或禁用文档 | 1 | G01-2 |" in text


def test_real_kb6_hops_never_share_a_file():
    root = EVAL_DIR / "suite/kbs/KB6"
    lines = (root / "questions.jsonl").read_text(encoding="utf-8").splitlines()
    questions = [json.loads(line) for line in lines if line]
    entries = {
        doc["id"]: set(doc["entries"])
        for doc in json.loads((root / "manifest.json").read_text(encoding="utf-8"))["documents"]
    }
    for question in questions:
        support = question["supporting"]
        assert len({s["document"] for s in support}) == len({s["entry"] for s in support})
        assert all(s["entry"] in entries[s["document"]] for s in support)


def test_real_suite_validates_and_meets_coverage():
    suite = dataset.load()
    stats = dataset.validate(suite)
    assert stats["groups"] == 20
    assert dataset.check_coverage(dataset.coverage(suite)) == []
