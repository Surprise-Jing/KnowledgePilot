"""Pure metric and judging functions for eval/run.py. No I/O, no network; covered by
backend/tests/e2e/test_eval.py. Definitions are documented in docs/eval-report.md §2."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import Any

FAILURE_TYPES = ("检索未命中", "工具选择错误", "引用错误", "回答错误", "异常处理")

# Phrases that state the material does not contain the answer. Matched after normalize().
NO_ANSWER_PHRASES = (
    "没有找到",
    "未找到",
    "找不到",
    "没有查到",
    "未查到",
    "没有提到",
    "未提到",
    "没有提及",
    "未提及",
    "没有涉及",
    "未涉及",
    "没有相关",
    "无相关",
    "没有关于",
    "没有包含",
    "不包含",
    "未包含",
    "没有说明",
    "未说明",
    "没有记载",
    "未记载",
    "没有明确",
    "未明确",
    "没有具体",
    "并未",
    "无法确定",
    "无法回答",
    "无法找到",
    "无法读取",
    "无法获取",
    "无法确认",
    "已被删除",
    "已删除",
    "不存在",
    "没有该",
    "没有这",
    "没有此",
    "没有任何",
)


def normalize(text: str) -> str:
    """Lower-case and drop whitespace, thousands separators and full-width variants so that
    "20 MB" matches "20MB" and "USD 168,000" matches "168000"."""
    text = text.lower().replace("＋", "+").replace("％", "%")
    return re.sub(r"[\s,，]", "", text)


def keyword_hits(answer: str, groups: Sequence[Sequence[str]]) -> list[bool]:
    """Each group is a list of alternatives; a group is hit when any alternative occurs."""
    normalized = normalize(answer)
    return [any(normalize(option) in normalized for option in group) for group in groups]


def says_no_answer(answer: str) -> bool:
    normalized = normalize(answer)
    return any(phrase in normalized for phrase in NO_ANSWER_PHRASES)


def location_matches(spec: dict, filename: str | None, location: dict | None) -> bool:
    """True when a hit or citation (filename + Location dict) falls inside a `relevant` spec.

    A spec names a `file` and optionally narrows it with `kind`, `table`, a 1-based inclusive
    `start`/`end` range (overlap is enough) and a `heading` that must be in heading_path."""
    if filename != spec["file"]:
        return False
    narrowing = {"kind", "table", "start", "end", "heading"} & spec.keys()
    if location is None:
        return not narrowing
    if "kind" in spec and location.get("kind") != spec["kind"]:
        return False
    if "table" in spec and location.get("table") != spec["table"]:
        return False
    if "start" in spec or "end" in spec:
        start = spec.get("start", spec.get("end"))
        end = spec.get("end", start)
        if location["start"] > end or start > location["end"]:
            return False
    return "heading" not in spec or spec["heading"] in location.get("heading_path", [])


def is_relevant(item: dict, specs: Iterable[dict]) -> bool:
    """`item` is a SearchHit or citation dict with `filename` and `location`."""
    return any(location_matches(spec, item.get("filename"), item.get("location")) for spec in specs)


def first_relevant_rank(hits: Sequence[dict], specs: Sequence[dict]) -> int | None:
    """1-based rank of the first relevant hit, or None when no hit is relevant."""
    for rank, hit in enumerate(hits, 1):
        if is_relevant(hit, specs):
            return rank
    return None


def retrieval_metrics(ranks: Sequence[int | None], ks: Sequence[int] = (1, 3, 5)) -> dict:
    """Hit@k (share of queries with a relevant hit within the top k) and MRR (mean of
    1/rank of the first relevant hit, 0 when none was returned)."""
    count = len(ranks)
    if not count:
        return {**{f"hit@{k}": None for k in ks}, "mrr": None, "queries": 0}
    result: dict[str, Any] = {
        f"hit@{k}": sum(1 for rank in ranks if rank is not None and rank <= k) / count for k in ks
    }
    result["mrr"] = sum(1 / rank for rank in ranks if rank is not None) / count
    result["queries"] = count
    return result


def percentile(values: Sequence[float], p: float) -> float | None:
    """Linear-interpolation percentile (same as numpy's default); None for no values."""
    data = sorted(values)
    if not data:
        return None
    position = (len(data) - 1) * p / 100
    low, high = math.floor(position), math.ceil(position)
    return data[low] + (data[high] - data[low]) * (position - low)


def judge_case(case: dict, turns: Sequence[dict]) -> dict:
    """Judge a case from its turn results (the last turn carries the expectations).

    Each turn dict has: `answer`, `stop_reason`, `error_code`, `tool_calls`
    ([{name, status, error_code}]), `citations` ([{filename, location}]) and
    `retrieval_covered` (True / False / None when no content tool ran).
    Returns checks, pass/fail, failure type and human-readable reasons."""
    expect = case["expect"]
    final = turns[-1]
    relevant = expect.get("relevant", [])
    no_answer = expect.get("expect_no_answer", False)
    expected_stop = expect.get("expect_stop_reason", "answered")
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    def check(name: str, ok: bool, reason: str) -> None:
        checks[name] = ok
        if not ok:
            reasons.append(reason)

    check(
        "stop_reason",
        final["stop_reason"] == expected_stop,
        f"stop_reason={final['stop_reason']}，期望 {expected_stop}",
    )
    observed_codes = {final.get("error_code")} | {
        call.get("error_code") for call in final["tool_calls"]
    }
    observed_codes.discard(None)
    if expect.get("expect_error_code"):
        code = expect["expect_error_code"]
        check("error_code", code in observed_codes, f"未出现错误码 {code}（实际 {observed_codes}）")
    called = [call["name"] for call in final["tool_calls"]]
    for tool in expect.get("expect_tools", []):
        check(f"tool:{tool}", tool in called, f"未调用 {tool}（实际 {called}）")
    for tool in expect.get("forbidden_tools", []):
        check(f"no_tool:{tool}", tool not in called, f"调用了禁止的 {tool}")

    keyword_rate = None
    correct_citations = 0
    if expected_stop == "answered":
        answer = final["answer"]
        groups = expect.get("answer_keywords", [])
        if groups:
            hits = keyword_hits(answer, groups)
            keyword_rate = sum(hits) / len(hits)
            missing = [group[0] for group, hit in zip(groups, hits, strict=True) if not hit]
            check("keywords", all(hits), f"回答缺少要点 {missing}")
        bad = [
            word
            for word in expect.get("forbidden_keywords", [])
            if normalize(word) in normalize(answer)
        ]
        if expect.get("forbidden_keywords"):
            check("forbidden_keywords", not bad, f"回答出现了不应出现的内容 {bad}")
        if no_answer:
            check("no_answer", says_no_answer(answer), "未明确说明资料中没有答案")
        citations = final["citations"]
        correct_citations = sum(1 for item in citations if is_relevant(item, relevant))
        if relevant and not no_answer:
            check("citation_hit", correct_citations > 0, "没有引用到期望位置")
        forbidden_files = set(expect.get("forbidden_files", []))
        if forbidden_files:
            leaked = sorted({c["filename"] for c in citations if c["filename"] in forbidden_files})
            check("citation_scope", not leaked, f"引用了范围外文件 {leaked}")

    passed = all(checks.values())
    failure_type = None if passed else classify_failure(case, checks, final)
    return {
        "passed": passed,
        "failure_type": failure_type,
        "checks": checks,
        "reasons": reasons,
        "keyword_hit_rate": keyword_rate,
        "citations_total": len(final["citations"]),
        "citations_correct": correct_citations,
        "observed_error_codes": sorted(observed_codes),
    }


def classify_failure(case: dict, checks: dict[str, bool], final: dict) -> str:
    """Map failed checks to one of FAILURE_TYPES, most fundamental cause first."""
    failed = {name for name, ok in checks.items() if not ok}
    expect = case["expect"]
    if case["category"] == "异常" or failed & {"stop_reason", "error_code"}:
        return "异常处理"
    if any(name.startswith(("tool:", "no_tool:")) for name in failed):
        return "工具选择错误"
    needs_retrieval = expect.get("relevant") and not expect.get("expect_no_answer")
    if needs_retrieval and final.get("retrieval_covered") is not True:
        return "检索未命中"
    if failed & {"citation_hit", "citation_scope"}:
        return "引用错误"
    return "回答错误"
