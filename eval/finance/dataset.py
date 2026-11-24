"""Pinned official PDFs and auditable gold facts; no network during validation by default."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import httpx
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
CACHE = REPO / "eval/results/finance/sources"


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def load() -> tuple[dict, dict, list[dict]]:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    cases = [
        json.loads(line)
        for line in (ROOT / "cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return manifest, evidence, cases


def calculate(spec: dict, evidence: dict) -> Decimal:
    values = [Decimal(evidence[key]["value"]) for key in spec["operands"]]
    operation = spec["operation"]
    if operation in {"subtract", "net_flow"}:
        result = values[0] - values[1]
    elif operation == "relative_change_pct":
        result = (values[0] / values[1] - 1) * 100
    elif operation == "share_bridge":
        result = values[0] + values[1] - values[2] + values[3]
    elif operation == "sum":
        result = sum(values, Decimal(0))
    else:
        raise ValueError(f"未知计算方法：{operation}")
    return result.quantize(Decimal(1).scaleb(-spec["decimal_places"]), rounding=ROUND_HALF_UP)


def validate(manifest: dict, evidence: dict, cases: list[dict]) -> dict:
    """Fail on dangling evidence, scope leakage, or an incorrect calculation annotation."""
    documents = {doc["id"]: doc for doc in manifest["documents"]}
    if len(documents) != len(manifest["documents"]):
        raise ValueError("重复文档 ID")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("重复用例 ID")
    for key, item in evidence.items():
        doc = documents[item["document"]]
        if key != item["id"] or not 1 <= item["page"] <= doc["pages"] or not item["anchor"]:
            raise ValueError(f"证据标注无效：{key}")
    for case in cases:
        scope = set(case["documents"])
        if not scope or not scope <= documents.keys():
            raise ValueError(f"文档范围无效：{case['id']}")
        expected_kbs = {documents[key]["knowledge_base"] for key in scope}
        if set(case["knowledge_bases"]) != expected_kbs:
            raise ValueError(f"知识库范围不匹配：{case['id']}")
        if case["split"] not in {"dev", "test"} or not case["semantic_rubric"]:
            raise ValueError(f"缺少评审规则：{case['id']}")
        if len({atom["id"] for atom in case["claims"]}) != len(case["claims"]):
            raise ValueError(f"重复要点 ID：{case['id']}")
        if scope & set(case["forbidden_documents"]):
            raise ValueError(f"禁止文档与目标文档重叠：{case['id']}")
        for atom in case["claims"]:
            for group in atom["evidence_groups"]:
                if not group or any(evidence[key]["document"] not in scope for key in group):
                    raise ValueError(f"要点证据越界：{case['id']}/{atom['id']}")
                # Alternatives may change page/section, but must describe the same fact.
                facts = [evidence[key] for key in group]
                if any(
                    fact["document"] != facts[0]["document"] or fact["value"] != facts[0]["value"]
                    for fact in facts[1:]
                ):
                    raise ValueError(f"替代证据并非同一事实：{case['id']}/{atom['id']}")
        for spec in case["calculations"]:
            if any(evidence[key]["document"] not in scope for key in spec["operands"]):
                raise ValueError(f"计算操作数越界：{case['id']}")
            if calculate(spec, evidence) != Decimal(spec["expected"]):
                raise ValueError(f"计算标注错误：{case['id']}/{spec['id']}")
    return {"documents": len(documents), "evidence": len(evidence), "cases": len(cases)}


def source_path(document: dict, cache: Path = CACHE) -> Path:
    return cache / document["filename"]


def verify_pdf(document: dict, path: Path) -> list[str]:
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != document["sha256"]:
        raise ValueError(f"PDF 哈希不匹配：{document['filename']}")
    if len(content) != document["bytes"]:
        raise ValueError(f"PDF 文件大小不匹配：{document['filename']}")
    reader = PdfReader(path)
    if len(reader.pages) != document["pages"]:
        raise ValueError(f"PDF 页数不匹配：{document['filename']}")
    return [normalize(page.extract_text() or "") for page in reader.pages]


def fetch_sources(manifest: dict, cache: Path = CACHE) -> None:
    """Only an explicit CLI --fetch action downloads; fail rather than repin changed PDFs."""
    cache.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for document in manifest["documents"]:
            path = source_path(document, cache)
            if path.exists():
                verify_pdf(document, path)
                continue
            response = client.get(document["url"])
            response.raise_for_status()
            payload = response.content
            if not payload.startswith(b"%PDF"):
                raise ValueError(f"下载结果不是 PDF：{document['filename']}")
            if hashlib.sha256(payload).hexdigest() != document["sha256"]:
                raise ValueError(f"下载结果与固定哈希不同：{document['filename']}")
            temporary = path.with_suffix(".download")
            temporary.write_bytes(payload)
            try:
                verify_pdf(document, temporary)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)


def verify_sources(manifest: dict, evidence: dict, cache: Path = CACHE) -> dict:
    pages = {doc["id"]: verify_pdf(doc, source_path(doc, cache)) for doc in manifest["documents"]}
    for key, item in evidence.items():
        page = pages[item["document"]][item["page"] - 1]
        if normalize(item["anchor"]) not in page:
            raise ValueError(f"原报告找不到证据锚点：{key}")
        value = item["value"]
        if isinstance(value, (dict, list)):
            rows = value if isinstance(value, list) else [value]
            for row in rows:
                signature = "".join(
                    str(row[field])
                    for field in (
                        "rank",
                        "code",
                        "name",
                        "shares",
                        "fair_value",
                        "weight",
                    )
                )
                if signature not in page.replace(",", ""):
                    raise ValueError(f"持仓标注与原报告不匹配：{key}")
        elif isinstance(value, str):
            # A dash or explicit absence is annotated as zero for arithmetic, not a quote.
            absence = value == "0" and (
                "未持有债券" in item["anchor"] or key.endswith(".share_adjustment")
            )
            if not absence and value not in normalize(item["anchor"]).replace(",", ""):
                raise ValueError(f"事实值与证据锚点不匹配：{key}")
    return {"verified_pdfs": len(pages), "verified_anchors": len(evidence)}
