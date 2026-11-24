"""Conservative rule signals, never a substitute for claim-level semantic review."""

from __future__ import annotations

import re
from decimal import Decimal

from finance.dataset import normalize

NUMBER = re.compile(r"(?<![\d.])([+-]?\d+(?:\.\d+)?)(亿|万)?(个百分点|%|％|元|份)?")


def number_signal(answer: str, spec: dict) -> bool:
    """Keep signs, scales and explicit units; tables with units only in headers need review."""
    text = normalize(answer).replace(",", "").replace("，", "").replace("−", "-")
    expected = Decimal(spec["value"])
    tolerance = Decimal(spec["tolerance"])
    allowed = {
        "CNY": {"元"},
        "share": {"份"},
        "CNY/share": {"元"},
        "percent": {"%", "％"},
        "percent_of_NAV": {"%", "％"},
        "percent_of_total_assets": {"%", "％"},
        "percentage_point": {"个百分点"},
        "ratio": {None},
    }[spec["unit"]]
    for match in NUMBER.finditer(text):
        if match[3] not in allowed:
            continue
        multiplier = {None: 1, "万": 10_000, "亿": 100_000_000}[match[2]]
        actual = Decimal(match[1]) * multiplier
        if abs(actual - expected) <= tolerance:
            return True
        # Natural-language decreases omit a minus sign, but a positive return is not a loss.
        prefix = text[max(0, match.start() - 6) : match.start()]
        if (
            expected < 0
            and actual > 0
            and re.search(r"下降|减少|净赎回|降低|下跌|亏损", prefix)
            and abs(-actual - expected) <= tolerance
        ):
            return True
    return False


def evidence_matches(item: dict, citation: dict, documents: dict) -> bool:
    location = citation.get("location") or {}
    return (
        citation.get("filename") == documents[item["document"]]["filename"]
        and location.get("kind") == "pdf_page"
        and location.get("start", 0) <= item["page"] <= location.get("end", 0)
        # Docling serializes table cells with Markdown pipes. Match source text despite
        # that presentation delimiter; keep every fact, number, filename and page check.
        and normalize(item["anchor"]).replace("|", "")
        in normalize(citation.get("content", "")).replace("|", "")
    )


def judge(case: dict, turn: dict, evidence: dict, documents: dict) -> dict:
    answer = turn["answer"]
    citations = turn["citations"]
    allowed_files = {
        doc["filename"]
        for doc in documents.values()
        if doc["knowledge_base"] in case["knowledge_bases"]
    }
    leaked = sorted({item["filename"] for item in citations} - allowed_files)
    # Same marker may legitimately support several claims, but must be displayed only once.
    markers = [item["marker"] for item in citations]
    atoms = []
    for atom in case["claims"]:
        groups = atom["evidence_groups"]
        covered = [
            any(
                evidence_matches(evidence[key], citation, documents)
                for key in group
                for citation in citations
            )
            for group in groups
        ]
        text_signals = [
            any(normalize(word) in normalize(answer) for word in alternatives)
            for alternatives in atom["answer_keywords"]
        ] + [number_signal(answer, spec) for spec in atom["answer_numbers"]]
        atoms.append(
            {
                "id": atom["id"],
                "value_signal": all(text_signals),
                "required_evidence_groups": len(groups),
                "covered_evidence_groups": sum(covered),
                "evidence_covered": all(covered) if groups else None,
                "rule_satisfied": all(text_signals) and all(covered),
            }
        )
    evidence_atoms = [item for item in atoms if item["evidence_covered"] is not None]
    structural = {
        "normal_finish": turn.get("stop_reason") == "answered" and not turn.get("error_code"),
        "sse_order": turn.get("event_order_ok", False),
        "citation_scope": not leaked,
        "unique_markers": len(markers) == len(set(markers)),
    }
    return {
        "rule_passed": all(structural.values()) and all(a["rule_satisfied"] for a in atoms),
        "structural_checks": structural,
        "leaked_files": leaked,
        "claim_value_signal_rate": sum(a["value_signal"] for a in atoms) / len(atoms),
        "claim_evidence_coverage": (
            sum(a["evidence_covered"] for a in evidence_atoms) / len(evidence_atoms)
            if evidence_atoms
            else None
        ),
        "claims": atoms,
        "semantic_review": "pending",
        "final_passed": None,
    }
