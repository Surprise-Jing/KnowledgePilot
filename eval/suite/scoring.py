"""Conservative rule signals for every location kind; never a substitute for semantic review."""

from __future__ import annotations

import metrics
from finance.dataset import normalize
from finance.scoring import number_signal


def _anchor_in(item: dict, content: str) -> bool:
    # Docling serializes table cells with Markdown pipes; match source text despite them.
    return normalize(item["anchor"]).replace("|", "") in normalize(content).replace("|", "")


def evidence_matches(item: dict, citation: dict, documents: dict) -> bool:
    location = citation.get("location") or {}
    if citation.get("filename") != documents[item["document"]]["filename"]:
        return False
    locator, kind = item["locator"], location.get("kind", "")
    if locator["kind"] == "pdf_page":
        placed = kind == "pdf_page" and location["start"] <= locator["page"] <= location["end"]
    elif locator["kind"] == "txt_lines":
        placed = kind == "txt_lines" and location["start"] <= locator["line"] <= location["end"]
    else:
        # Product DOCX ordinals come from Docling text items, so only the heading is checked.
        placed = kind.startswith("docx_") and (
            "heading" not in locator or locator["heading"] in location.get("heading_path", [])
        )
    return placed and _anchor_in(item, citation.get("content") or "")


def judge(group: dict, case: dict, turns: list[dict], evidence: dict, documents: dict) -> dict:
    final = turns[-1]
    answer, citations, expect = final["answer"], final["citations"], case["expect"]
    allowed = {
        doc["filename"]
        for doc in documents.values()
        if doc["knowledge_base"] in (case.get("knowledge_bases") or group["knowledge_bases"])
    }
    leaked = sorted({item["filename"] for item in citations} - allowed)
    markers = [item["marker"] for item in citations]
    claims = []
    for atom in case["claims"]:
        groups = atom["evidence_groups"]
        covered = [
            any(
                evidence_matches(evidence[key], citation, documents)
                for key in alternatives
                for citation in citations
            )
            for alternatives in groups
        ]
        signals = metrics.keyword_hits(answer, atom["answer_keywords"]) + [
            number_signal(answer, spec) for spec in atom["answer_numbers"]
        ]
        claims.append(
            {
                "id": atom["id"],
                "value_signal": all(signals),
                "required_evidence_groups": len(groups),
                "covered_evidence_groups": sum(covered),
                "evidence_covered": all(covered) if groups else None,
                "rule_satisfied": all(signals) and all(covered),
            }
        )
    structural = {
        "stop_reason": final.get("stop_reason") == expect.get("stop_reason", "answered"),
        "error_code": final.get("error_code") == expect.get("error_code"),
        "sse_order": bool(final.get("event_order_ok")),
        "citation_scope": not leaked,
        "unique_markers": len(markers) == len(set(markers)),
    }
    if expect.get("degraded"):
        structural["degraded"] = bool(final.get("degraded"))
    called = {call["name"] for turn in turns for call in turn.get("tool_calls", [])}
    tool_check = None
    if expect.get("tools") or expect.get("forbidden_tools"):
        tool_check = set(expect.get("tools", [])) <= called and not (
            called & set(expect.get("forbidden_tools", []))
        )
    no_answer_check = metrics.says_no_answer(answer) if expect.get("no_answer") else None
    evidence_claims = [c for c in claims if c["evidence_covered"] is not None]
    return {
        "rule_passed": all(structural.values())
        and all(c["rule_satisfied"] for c in claims)
        and tool_check is not False
        and no_answer_check is not False,
        "structural_checks": structural,
        "leaked_files": leaked,
        "claims": claims,
        "claim_value_signal_rate": (
            sum(c["value_signal"] for c in claims) / len(claims) if claims else None
        ),
        "claim_evidence_coverage": (
            sum(c["evidence_covered"] for c in evidence_claims) / len(evidence_claims)
            if evidence_claims
            else None
        ),
        "tool_check": tool_check,
        "no_answer_check": no_answer_check,
    }
