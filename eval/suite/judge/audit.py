"""Human audit sheet: stratified sample of judged claims, and judge–human agreement.

python ../eval/suite/judge/audit.py sample <judge_dir> --out <judge_dir>/audit.csv
python ../eval/suite/judge/audit.py agreement <judge_dir>/audit.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL_DIR))

from suite import dataset
from suite.judge import packets

COLUMNS = (
    "group_id", "repeat_index", "case_id", "claim_id", "question", "answer_excerpt",
    "reference", "judge_correct", "judge_citation", "human_correct", "human_citation", "note",
)  # fmt: skip


def sample(merged: list[dict], suite, rate: float = 0.2, seed: int = 20261124) -> list[dict]:
    """Every flagged or reviewed claim, ≥1 per group and per dimension, then fill to `rate`."""
    rng = random.Random(seed)
    dims = {group["id"]: group["dimensions"] for group in suite.groups}
    chosen: dict[tuple, dict] = {}

    def take(row: dict) -> None:
        chosen.setdefault(packets._key(row), row)

    for row in merged:
        if row.get("flagged") or row.get("source") == "review":
            take(row)
    for group_id in sorted({row["group_id"] for row in merged}):
        if not any(r["group_id"] == group_id for r in chosen.values()):
            take(rng.choice([r for r in merged if r["group_id"] == group_id]))
    for dim in dataset.DIMENSIONS:
        pool = [r for r in merged if dim in dims.get(r["group_id"], [])]
        if pool and not any(dim in dims.get(r["group_id"], []) for r in chosen.values()):
            take(rng.choice(pool))
    rest = [r for r in merged if packets._key(r) not in chosen]
    rng.shuffle(rest)
    for row in rest[: max(0, math.ceil(rate * len(merged)) - len(chosen))]:
        take(row)
    return sorted(chosen.values(), key=lambda r: (r["group_id"], *packets._key(r)))


def write_sheet(rows: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "group_id": row["group_id"],
                    "repeat_index": row["repeat_index"],
                    "case_id": row["case_id"],
                    "claim_id": row["claim_id"],
                    "question": row["question"],
                    "answer_excerpt": row["answer"][:300],
                    "reference": row["reference"],
                    "judge_correct": row["final"]["correct"],
                    "judge_citation": row["final"]["citation_supports"],
                    "human_correct": "",
                    "human_citation": "",
                    "note": "",
                }
            )


def read_sheet(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            if row["human_correct"] not in packets.CORRECT:
                raise ValueError(f"抽检表第 {line} 行 human_correct 需填 yes/no/partial")
            if row["human_citation"] not in packets.CITATION:
                raise ValueError(
                    f"抽检表第 {line} 行 human_citation 需填 supported/unsupported/no_citation"
                )
            rows.append(
                {
                    **row,
                    "repeat_index": int(row["repeat_index"]),
                    "correct": row["human_correct"],
                    "citation_supports": row["human_citation"],
                }
            )
    return rows


def _kappa(pairs: list[tuple[str, str]]) -> dict:
    n = len(pairs)
    if not n:
        return {"accuracy": None, "kappa": None, "n": 0}
    observed = sum(a == b for a, b in pairs) / n
    labels = {label for pair in pairs for label in pair}
    expected = sum(
        (sum(a == label for a, _ in pairs) / n) * (sum(b == label for _, b in pairs) / n)
        for label in labels
    )
    kappa = (
        (1.0 if observed == 1 else 0.0) if expected == 1 else (observed - expected) / (1 - expected)
    )
    return {"accuracy": observed, "kappa": kappa, "n": n}


def agreement(rows: list[dict]) -> dict:
    return {
        "correct": _kappa([(r["judge_correct"], r["human_correct"]) for r in rows]),
        "citation": _kappa([(r["judge_citation"], r["human_citation"]) for r in rows]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sample", "agreement"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--rate", type=float, default=0.2)
    args = parser.parse_args(argv)
    if args.command == "sample":
        initial, review = packets.collect(args.path)
        rows = sample(packets.merge(initial, review, None), dataset.load(), args.rate)
        out = args.out or args.path / "audit.csv"
        write_sheet(rows, out)
        print(f"抽检 {len(rows)} 个要点：{out}")
        return 0
    print(json.dumps(agreement(read_sheet(args.path)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
