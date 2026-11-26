"""Load and validate the suite: per-KB manifests and evidence ledgers plus 20 group files.

Validation never touches the network or the original documents; `sources.verify` checks
anchors against the cached originals separately."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from finance.dataset import calculate, normalize

SUITE = Path(__file__).resolve().parent
REPO = SUITE.parents[1]
CACHE = REPO / "eval/results/suite/sources"

DIMENSIONS = tuple(f"D{n}" for n in range(1, 10))
SPLITS = {"dev", "test"}
REVIEW_STATUSES = {"pending", "approved", "revised", "rejected"}
ORIGINS = {"drafted", "dataset", "finance-v1"}
MAX_ANCHOR_CHARS = 120
# Which locator kinds a document's format admits.
FORMAT_LOCATORS = {"pdf": "pdf_page", "txt": "txt_lines", "docx": "docx"}


@dataclass
class Suite:
    root: Path
    manifests: dict[str, dict] = field(default_factory=dict)
    documents: dict[str, dict] = field(default_factory=dict)
    evidence: dict[str, dict] = field(default_factory=dict)
    groups: list[dict] = field(default_factory=list)


def scope(group: dict, case: dict) -> list[str]:
    """Knowledge bases a case may use: its own narrower list, else the group's."""
    return case.get("knowledge_bases") or group["knowledge_bases"]


def doc_format(document: dict) -> str:
    return Path(document["filename"]).suffix.lower().lstrip(".")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load(root: Path = SUITE) -> Suite:
    suite = Suite(root=root)
    for manifest_path in sorted((root / "kbs").glob("*/manifest.json")):
        manifest = _read(manifest_path)
        kb_id = manifest["kb_id"]
        if kb_id in suite.manifests:
            raise ValueError(f"重复知识库清单：{kb_id}")
        suite.manifests[kb_id] = manifest
        for document in manifest["documents"]:
            if document["id"] in suite.documents:
                raise ValueError(f"重复文档 ID：{document['id']}")
            suite.documents[document["id"]] = {**document, "kb_id": kb_id}
        evidence_path = manifest_path.with_name("evidence.json")
        if evidence_path.exists():
            for key, item in _read(evidence_path).items():
                if key in suite.evidence:
                    raise ValueError(f"重复证据 ID：{key}")
                suite.evidence[key] = item
    suite.groups = [_read(path) for path in sorted((root / "groups").glob("G*.json"))]
    return suite


def _validate_evidence(suite: Suite) -> None:
    for key, item in suite.evidence.items():
        document = suite.documents.get(item.get("document"))
        if key != item.get("id") or document is None:
            raise ValueError(f"证据标注无效：{key}")
        locator = item.get("locator") or {}
        expected_kind = FORMAT_LOCATORS.get(doc_format(document))
        if locator.get("kind") != expected_kind:
            raise ValueError(f"证据位置类型与文档格式不符：{key}")
        if expected_kind == "pdf_page" and not 1 <= locator.get("page", 0) <= document["pages"]:
            raise ValueError(f"证据页码超出范围：{key}")
        if expected_kind == "txt_lines" and not 1 <= locator.get("line", 0) <= document["lines"]:
            raise ValueError(f"证据行号超出范围：{key}")
        anchor = normalize(item.get("anchor") or "")
        if not anchor:
            raise ValueError(f"证据锚点为空：{key}")
        # Long anchors are likely to straddle chunk boundaries and never match a citation.
        if len(anchor) > MAX_ANCHOR_CHARS:
            raise ValueError(f"证据锚点过长（>{MAX_ANCHOR_CHARS} 字符）：{key}")


def _validate_case(suite: Suite, group: dict, case: dict, kb_names: set[str]) -> None:
    where = f"{group['id']}/{case['id']}"
    if case.get("origin") not in ORIGINS:
        raise ValueError(f"用例来源无效：{where}")
    if (case.get("review") or {}).get("status") not in REVIEW_STATUSES:
        raise ValueError(f"review 状态无效：{where}")
    if not case.get("turns") or not all(turn.get("content") for turn in case["turns"]):
        raise ValueError(f"缺少问题：{where}")
    if not case.get("semantic_rubric") or not case.get("reference_answer"):
        raise ValueError(f"缺少评审规则或参考答案：{where}")
    if not set(scope(group, case)) <= kb_names:
        raise ValueError(f"用例知识库超出组范围：{where}")
    kb_names = set(scope(group, case))
    targets = set(case.get("documents") or [])
    unknown = targets - suite.documents.keys()
    if unknown:
        raise ValueError(f"未知文档：{where} {sorted(unknown)}")
    if any(suite.documents[key]["knowledge_base"] not in kb_names for key in targets):
        raise ValueError(f"目标文档不在组的知识库内：{where}")
    if targets & set(case.get("forbidden_documents") or []):
        raise ValueError(f"禁止文档与目标文档重叠：{where}")
    for turn in case["turns"]:
        for action in turn.get("before", []):
            if action.get("action") != "delete_document" or action.get("document") not in targets:
                raise ValueError(f"轮前动作无效：{where}")
    expect = case.get("expect") or {}
    if not case.get("claims") and not expect.get("no_answer") and not expect.get("error_code"):
        raise ValueError(f"缺少要点：{where}")
    if len({atom["id"] for atom in case["claims"]}) != len(case["claims"]):
        raise ValueError(f"重复要点 ID：{where}")
    for atom in case["claims"]:
        groups = atom.get("evidence_groups")
        # An empty list is a text-only signal (e.g. "says the scope lacks it"); absent is a slip.
        if groups is None:
            raise ValueError(f"要点缺少证据：{where}/{atom['id']}")
        for alternatives in groups:
            if not alternatives:
                raise ValueError(f"空证据组：{where}/{atom['id']}")
            for key in alternatives:
                if key not in suite.evidence:
                    raise ValueError(f"证据不存在：{where}/{atom['id']} → {key}")
                if suite.evidence[key]["document"] not in targets:
                    raise ValueError(f"要点证据越界：{where}/{atom['id']} → {key}")
            # Alternatives may sit on different pages, but must describe the same fact.
            facts = [suite.evidence[key] for key in alternatives]
            if any(
                fact["document"] != facts[0]["document"] or fact["value"] != facts[0]["value"]
                for fact in facts[1:]
            ):
                raise ValueError(f"替代证据并非同一事实：{where}/{atom['id']}")
    for spec in case.get("calculations") or []:
        if any(suite.evidence[key]["document"] not in targets for key in spec["operands"]):
            raise ValueError(f"计算操作数越界：{where}/{spec['id']}")
        if calculate(spec, suite.evidence) != Decimal(spec["expected"]):
            raise ValueError(f"计算标注错误：{where}/{spec['id']}")


def validate(suite: Suite) -> dict:
    """Raise ValueError (Chinese message naming the group/case/claim) on any defect."""
    _validate_evidence(suite)
    all_kbs = {doc["knowledge_base"] for doc in suite.documents.values()}
    group_ids, case_ids = set(), set()
    for group in suite.groups:
        if group["id"] in group_ids:
            raise ValueError(f"重复组 ID：{group['id']}")
        group_ids.add(group["id"])
        kb_names = set(group.get("knowledge_bases") or [])
        if not kb_names or not kb_names <= all_kbs:
            raise ValueError(f"组的知识库无效：{group['id']} {sorted(kb_names - all_kbs)}")
        if group.get("split") not in SPLITS:
            raise ValueError(f"split 无效：{group['id']}")
        unknown = set(group.get("dimensions") or []) - set(DIMENSIONS)
        if not group.get("dimensions") or unknown:
            raise ValueError(f"维度无效：{group['id']} {sorted(unknown)}")
        if not group.get("cases"):
            raise ValueError(f"组内没有用例：{group['id']}")
        for case in group["cases"]:
            if case["id"] in case_ids:
                raise ValueError(f"重复用例 ID：{case['id']}")
            case_ids.add(case["id"])
            _validate_case(suite, group, case, kb_names)
    return {
        "knowledge_bases": len(all_kbs),
        "documents": len(suite.documents),
        "evidence": len(suite.evidence),
        "groups": len(suite.groups),
        "cases": len(case_ids),
        "claims": sum(len(case["claims"]) for group in suite.groups for case in group["cases"]),
    }


def coverage(suite: Suite) -> dict:
    all_kbs = {doc["knowledge_base"] for doc in suite.documents.values()}
    dims = {dim: 0 for dim in DIMENSIONS}
    formats = {"pdf": 0, "docx": 0, "txt": 0}
    partial = multi_turn = 0
    for group in suite.groups:
        for dim in set(group["dimensions"]):
            dims[dim] += 1
        group_formats = {
            doc_format(suite.documents[key]) for case in group["cases"] for key in case["documents"]
        }
        for fmt in group_formats & formats.keys():
            formats[fmt] += 1
        partial += set(group["knowledge_bases"]) < all_kbs
        multi_turn += any(len(case["turns"]) > 1 for case in group["cases"])
    return {
        "dimension_groups": dims,
        "format_groups": formats,
        "partial_scope_groups": partial,
        "multi_turn_groups": multi_turn,
    }


def check_coverage(cov: dict) -> list[str]:
    """Constraints from the spec §5; an empty list means all are met."""
    # Spec §5 assigns exactly two groups (G19–G20) to fault injection.
    minimum = {"D9": 2}
    problems = [
        f"{dim} 至少 {minimum.get(dim, 3)} 组（现有 {n}）"
        for dim, n in cov["dimension_groups"].items()
        if n < minimum.get(dim, 3)
    ]
    problems += [f"{fmt} 至少 4 组（现有 {n}）" for fmt, n in cov["format_groups"].items() if n < 4]
    if cov["partial_scope_groups"] < 3:
        problems.append(f"只选部分知识库的组至少 3 组（现有 {cov['partial_scope_groups']}）")
    if cov["multi_turn_groups"] < 4:
        problems.append(f"含多轮的组至少 4 组（现有 {cov['multi_turn_groups']}）")
    return problems


def fingerprint(root: Path = SUITE) -> str:
    """Hash of everything that defines the gold standard and the judging instructions."""
    paths = sorted(
        [*root.glob("kbs/*/manifest.json"), *root.glob("kbs/*/evidence.json")]
        + [*root.glob("kbs/*/questions.jsonl"), *root.glob("groups/G*.json")]
        + [root / "judge/PROMPT.md"]
    )
    digest = hashlib.sha256()
    for path in paths:
        if path.exists():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def ingest_fingerprint(root: Path = SUITE) -> str:
    """Hash of what ingestion reads: the document manifests. Questions, evidence and the judge
    prompt change scoring only, so they must not invalidate a parsed snapshot."""
    digest = hashlib.sha256()
    for path in sorted(root.glob("kbs/*/manifest.json")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def import_finance(finance_root: Path) -> tuple[dict, list[dict]]:
    """Convert the hand-annotated E Fund benchmark into suite evidence and cases, unchanged
    in substance: same claims, values, anchors, calculations and questions."""
    evidence = json.loads((finance_root / "evidence.json").read_text(encoding="utf-8"))
    lines = (finance_root / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    converted = {
        key: {
            **{k: v for k, v in item.items() if k != "page"},
            "locator": {"kind": "pdf_page", "page": item["page"]},
        }
        for key, item in evidence.items()
    }
    cases = []
    for line in filter(str.strip, lines):
        case = json.loads(line)
        cases.append(
            {
                "id": case["id"],
                "origin": "finance-v1",
                "source_ref": f"eval/finance/cases.jsonl#{case['id']}",
                "knowledge_bases": case["knowledge_bases"],
                "documents": case["documents"],
                "forbidden_documents": case["forbidden_documents"],
                "turns": case["turns"],
                "claims": case["claims"],
                "calculations": case["calculations"],
                "expect": {
                    "no_answer": False,
                    "tools": [],
                    "forbidden_tools": [],
                    "stop_reason": "answered",
                    "error_code": None,
                },
                "reference_answer": case["reference_answer"],
                "semantic_rubric": case["semantic_rubric"],
                "review": {"status": "approved", "note": "沿用 eval/finance 人工标注"},
                "finance_category": case["category"],
                "finance_split": case["split"],
            }
        )
    return converted, cases
