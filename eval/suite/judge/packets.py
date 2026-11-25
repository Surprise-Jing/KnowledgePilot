"""Judge packets for in-session review agents, verdict validation, review queue and merge.

    python ../eval/suite/judge/packets.py build <result.json>
    python ../eval/suite/judge/packets.py check <judge_dir>
    python ../eval/suite/judge/packets.py select-review <judge_dir>
    python ../eval/suite/judge/packets.py merge <judge_dir> [--audit audit.csv]

Layout under <judge_dir>: packets/<G>-r<k>.json, verdicts/<G>-r<k>.initial.json,
verdicts/<G>-r<k>.review.json, review-queue.json, merged.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL_DIR))

from suite import dataset

ANSWER_CLAIM = "__answer__"
CORRECT = {"yes", "no", "partial"}
CITATION = {"supported", "unsupported", "no_citation"}
STAGES = {"initial", "review"}


def _reference(atom: dict) -> str:
    parts = [f"{spec['value']} {spec['unit']}" for spec in atom["answer_numbers"]]
    parts += [alternatives[0] for alternatives in atom["answer_keywords"] if alternatives]
    return "; ".join(parts)


def _packet_case(case: dict, row: dict, suite) -> dict:
    final = row["turns"][-1]
    rules = {claim["id"]: claim for claim in row["claims"]}
    claims = [
        {
            "claim_id": atom["id"],
            "label": atom["label"],
            "reference": _reference(atom),
            "requires_evidence": bool(atom["evidence_groups"]),
            "evidence_excerpts": [
                {
                    "filename": suite.documents[suite.evidence[key]["document"]]["filename"],
                    "locator": suite.evidence[key]["locator"],
                    "anchor": suite.evidence[key]["anchor"],
                }
                for alternatives in atom["evidence_groups"]
                for key in alternatives
            ],
            "rule": rules.get(atom["id"], {}),
        }
        for atom in case["claims"]
    ]
    if not claims:
        # No-answer and fault cases are judged as one whole-answer item.
        claims = [
            {"claim_id": ANSWER_CLAIM, "label": "整体回答是否符合参考答案与评审规则",
             "reference": case["reference_answer"], "requires_evidence": False,
             "evidence_excerpts": [], "rule": {"value_signal": row["rule_passed"],
                                               "rule_satisfied": row["rule_passed"]}}
        ]  # fmt: skip
    return {
        "case_id": case["id"],
        "turns": [turn["question"] for turn in row["turns"]],
        "answer": final["answer"],
        "citations": [
            {
                "marker": c.get("marker"),
                "filename": c.get("filename"),
                "location_label": c.get("location_label") or c.get("label"),
                "content": c.get("content"),
            }
            for c in final["citations"]
        ],
        "reference_answer": case["reference_answer"],
        "semantic_rubric": case["semantic_rubric"],
        "expect_no_answer": bool(case["expect"].get("no_answer")),
        "structural_ok": all(row["structural_checks"].values()) and row["tool_check"] is not False,
        "claims": claims,
    }


def build_packets(result: dict, suite, out_dir: Path, result_file: str) -> list[Path]:
    groups = {group["id"]: group for group in suite.groups}
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for row in result["groups"]:
        group = groups[row["id"]]
        cases = {case["id"]: case for case in group["cases"]}
        packet = {
            "result_file": result_file,
            "group_id": row["id"],
            "repeat_index": row["repeat_index"],
            "cases": [_packet_case(cases[c["id"]], c, suite) for c in row["cases"]],
        }
        path = out_dir / f"{row['id']}-r{row['repeat_index'] + 1}.json"
        path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", "utf-8")
        paths.append(path)
    return paths


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_verdicts(packet: Path, verdict: Path, only: set[tuple[str, str]] | None = None) -> list:
    """Verdict rows joined with packet context; any mismatch rejects the whole file."""
    data, judged = _read(packet), _read(verdict)
    name = verdict.name
    if (judged.get("group_id"), judged.get("repeat_index")) != (
        data["group_id"],
        data["repeat_index"],
    ):
        raise ValueError(f"判定文件与评审包的组或重复序号不一致：{name}")
    if judged.get("stage") not in STAGES or not judged.get("judge_model"):
        raise ValueError(f"判定文件缺少 stage 或 judge_model：{name}")
    context = {
        (case["case_id"], claim["claim_id"]): (case, claim)
        for case in data["cases"]
        for claim in case["claims"]
    }
    expected = set(context) if only is None else only
    keys = [(row.get("case_id"), row.get("claim_id")) for row in judged.get("verdicts", [])]
    missing = sorted(expected - set(keys))
    extra = sorted(set(keys) - expected)
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if missing or extra or duplicated:
        fmt = lambda items: ", ".join(f"{a}/{b}" for a, b in items) or "无"
        raise ValueError(
            f"判定文件 {name} 与评审包不一致：缺少 {fmt(missing)}；多余 {fmt(extra)}；"
            f"重复 {fmt(duplicated)}"
        )
    rows = []
    for row in judged["verdicts"]:
        if row.get("correct") not in CORRECT:
            raise ValueError(f"correct 取值无效：{name} {row['case_id']}/{row['claim_id']}")
        if row.get("citation_supports") not in CITATION:
            raise ValueError(f"citation_supports 取值无效：{name} {row['case_id']}")
        if not row.get("reason"):
            raise ValueError(f"缺少理由：{name} {row['case_id']}/{row['claim_id']}")
        case, claim = context[(row["case_id"], row["claim_id"])]
        rows.append(
            {
                **row,
                "group_id": data["group_id"],
                "repeat_index": data["repeat_index"],
                "stage": judged["stage"],
                "judge_model": judged["judge_model"],
                "question": " / ".join(case["turns"]),
                "answer": case["answer"],
                "reference": claim["reference"] or case["reference_answer"],
                "requires_evidence": claim["requires_evidence"],
                "structural_ok": case["structural_ok"],
                "rule": claim["rule"],
            }
        )
    return rows


def needs_review(verdict: dict | None, rule: dict) -> bool:
    if verdict is None or verdict["correct"] == "partial":
        return True
    if verdict["correct"] == "yes" and rule.get("value_signal") is False:
        return True
    return verdict["correct"] == "no" and rule.get("rule_satisfied") is True


def _key(row: dict) -> tuple:
    return (row["repeat_index"], row["case_id"], row["claim_id"])


def merge(initial: list[dict], review: list[dict], human: list[dict] | None) -> list[dict]:
    """Final verdict per claim: human audit > Opus review > Sonnet initial; all kept."""
    reviewed = {_key(row): row for row in review}
    audited = {_key(row): row for row in human or []}
    merged = []
    for row in initial:
        stages = {
            "initial": row,
            "review": reviewed.get(_key(row)),
            "human": audited.get(_key(row)),
        }
        source = next(name for name in ("human", "review", "initial") if stages[name])
        pick = stages[source]
        merged.append(
            {
                **{k: v for k, v in row.items() if k not in {"correct", "citation_supports"}},
                "source": source,
                "flagged": needs_review(row, row.get("rule", {})),
                "final": {
                    "correct": pick["correct"],
                    "citation_supports": pick["citation_supports"],
                },
                **{
                    name: {
                        "correct": value["correct"],
                        "citation_supports": value["citation_supports"],
                    }
                    for name, value in stages.items()
                    if value
                },
            }
        )
    return merged


def case_semantic_pass(claims: list[dict], structural_ok: bool) -> bool:
    return structural_ok and all(
        claim["final"]["correct"] == "yes"
        and (not claim["requires_evidence"] or claim["final"]["citation_supports"] == "supported")
        for claim in claims
    )


def _pairs(judge_dir: Path):
    for packet in sorted((judge_dir / "packets").glob("*.json")):
        yield packet, judge_dir / "verdicts" / f"{packet.stem}.initial.json"


def collect(judge_dir: Path) -> tuple[list[dict], list[dict]]:
    queue = (
        _read(judge_dir / "review-queue.json") if (judge_dir / "review-queue.json").exists() else {}
    )
    initial, review = [], []
    for packet, verdict in _pairs(judge_dir):
        if not verdict.exists():
            raise ValueError(f"缺少初审判定：{verdict}")
        initial += load_verdicts(packet, verdict)
        wanted = {tuple(item) for item in queue.get(packet.stem, [])}
        review_path = verdict.with_name(f"{packet.stem}.review.json")
        if wanted:
            if not review_path.exists():
                raise ValueError(f"缺少复审判定：{review_path}")
            review += load_verdicts(packet, review_path, only=wanted)
    return initial, review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "check", "select-review", "merge"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args(argv)
    if args.command == "build":
        result = _read(args.path)
        judge_dir = args.path.with_suffix("") / "judge"
        paths = build_packets(result, dataset.load(), judge_dir / "packets", args.path.name)
        (judge_dir / "verdicts").mkdir(parents=True, exist_ok=True)
        print(f"{len(paths)} 个评审包：{judge_dir / 'packets'}")
        return 0
    judge_dir = args.path
    if args.command == "check":
        initial = [load_verdicts(p, v) for p, v in _pairs(judge_dir) if v.exists()]
        missing = [v.name for _, v in _pairs(judge_dir) if not v.exists()]
        print(json.dumps({"checked": len(initial), "missing": missing}, ensure_ascii=False))
        return 1 if missing else 0
    if args.command == "select-review":
        queue = {}
        for packet, verdict in _pairs(judge_dir):
            rows = load_verdicts(packet, verdict)
            flagged = [[r["case_id"], r["claim_id"]] for r in rows if needs_review(r, r["rule"])]
            if flagged:
                queue[packet.stem] = flagged
        path = judge_dir / "review-queue.json"
        path.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(f"需复审 {sum(map(len, queue.values()))} 个要点：{path}")
        return 0
    from suite.judge import audit

    initial, review = collect(judge_dir)
    human = audit.read_sheet(args.audit) if args.audit else None
    claims = merge(initial, review, human)
    by_case: dict[tuple, list] = {}
    for claim in claims:
        by_case.setdefault((claim["group_id"], claim["repeat_index"], claim["case_id"]), []).append(
            claim
        )
    cases = [
        {"group_id": g, "repeat_index": r, "case_id": c,
         "semantic_pass": case_semantic_pass(rows, rows[0]["structural_ok"])}
        for (g, r, c), rows in by_case.items()
    ]  # fmt: skip
    path = judge_dir / "merged.json"
    path.write_text(
        json.dumps({"claims": claims, "cases": cases}, ensure_ascii=False, indent=2) + "\n",
        "utf-8",
    )
    print(f"最终判定：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
