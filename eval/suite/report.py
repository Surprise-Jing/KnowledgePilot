"""Markdown tables for docs/eval-report.md from runner results and final verdicts.

    uv run --directory backend python ../eval/suite/report.py \\
        --agent ../eval/results/suite/<run>-agent-hybrid.json \\
        --retrieval ../eval/results/suite/<run>-retrieval-cosine.json ... \\
        --verdicts ../eval/results/suite/<run>/judge/merged.json --out /tmp/tables.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from suite import dataset, stats

Verdicts = dict[tuple[str, int], bool]


def _rate(passed: int, n: int) -> str:
    lo, hi = stats.wilson(passed, n)
    share = f"{passed / n:.3f}" if n else "—"
    return f"{passed}/{n} | {share} | [{lo:.3f}, {hi:.3f}]"


def _fmt(value, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _coverage_matrix(suite) -> list[str]:
    dims = dataset.DIMENSIONS
    lines = ["## 维度 × 组覆盖", "", "| 组 | 标题 | split | " + " | ".join(dims) + " |",
             "| --- | --- | --- | " + " | ".join("---" for _ in dims) + " |"]  # fmt: skip
    for group in suite.groups:
        marks = " | ".join("✓" if dim in group["dimensions"] else " " for dim in dims)
        lines.append(f"| {group['id']} | {group['title']} | {group['split']} | {marks} |")
    return lines


def _case_runs(agent_results: list[dict]) -> list[tuple[str, str, int, dict]]:
    return [
        (group["id"], case["id"], group["repeat_index"], case)
        for result in agent_results
        for group in result["groups"]
        for case in group["cases"]
    ]


def _summary(runs, verdicts: Verdicts | None, label: str) -> list[str]:
    rule = sum(case["rule_passed"] for *_, case in runs)
    lines = [f"| {label}规则通过 | {_rate(rule, len(runs))} |"] if label else []
    if not label:
        lines.append(f"| 规则通过 | {_rate(rule, len(runs))} |")
    if verdicts is not None:
        semantic = [verdicts.get((case_id, rep)) for _, case_id, rep, _ in runs]
        judged = [v for v in semantic if v is not None]
        lines.append(f"| {label}语义通过 | {_rate(sum(judged), len(judged))} |")
    return lines


def _pass_hat(runs, verdicts: Verdicts | None) -> list[str]:
    by_case: dict[str, list] = defaultdict(list)
    for _, case_id, rep, case in runs:
        by_case[case_id].append((rep, case["rule_passed"], (verdicts or {}).get((case_id, rep))))
    k = max((len(v) for v in by_case.values()), default=0)
    rule = stats.pass_hat_k([[r for _, r, _ in v] for v in by_case.values()])
    lines = [f"| pass^{k} | {rule:.3f} | 规则 |"]
    if verdicts is not None:
        semantic = stats.pass_hat_k([[bool(s) for *_, s in v] for v in by_case.values()])
        lines.append(f"| pass^{k}（语义） | {semantic:.3f} | 语义 |")
    return lines


def _retrieval(retrieval_results: list[dict]) -> list[str]:
    lines = ["## 检索策略对比（原问题、无改写）", "",
             "| 策略 | 查询数 | Recall@1 | Recall@5 | Recall@10 | MRR | nDCG@10 | 证据齐全率 | Δ齐全率 vs cosine [95%] |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]  # fmt: skip
    baseline = next((r for r in retrieval_results if r["settings"]["retriever"] == "cosine"), None)
    base = {
        row["id"]: float(row["all_evidence_retrieved"]) for row in (baseline or {}).get("rows", [])
    }
    for result in retrieval_results:
        rows = result["rows"]
        ranks = [row["first_relevant_rank"] for row in rows]
        complete = sum(row["all_evidence_retrieved"] for row in rows) / len(rows) if rows else 0
        delta = "—"
        paired = [row for row in rows if row["id"] in base]
        if baseline is not None and result is not baseline and paired:
            mean, lo, hi = stats.paired_bootstrap(
                [base[row["id"]] for row in paired],
                [float(row["all_evidence_retrieved"]) for row in paired],
            )
            delta = f"{mean:+.3f} [{lo:+.3f}, {hi:+.3f}]"
        lines.append(
            f"| {result['settings']['retriever']} | {len(rows)} | "
            f"{stats.recall_at_k(ranks, 1):.3f} | {stats.recall_at_k(ranks, 5):.3f} | "
            f"{stats.recall_at_k(ranks, 10):.3f} | {stats.mrr(ranks):.3f} | "
            f"{stats.ndcg_at_k([row['relevance'] for row in rows]):.3f} | {complete:.3f} | {delta} |"
        )
    return lines


def _cost(runs) -> list[str]:
    turns = [turn for *_, case in runs for turn in case["turns"]]
    ttft = [t["ttft_s"] for t in turns if t.get("ttft_s") is not None]
    total = [t["latency_s"] for t in turns if t.get("latency_s") is not None]
    tokens = [(t.get("prompt_tokens") or 0) + (t.get("completion_tokens") or 0) for t in turns]
    calls = [len(t.get("tool_calls", [])) for t in turns]
    n = len(turns) or 1
    return [
        "## 成本与延迟（按轮次）", "", "| 指标 | 值 |", "| --- | --- |",
        f"| 轮次 | {len(turns)} |",
        f"| 首字延迟 P50 / P95 (s) | {_fmt(stats.percentile(ttft, 50))} / {_fmt(stats.percentile(ttft, 95))} |",
        f"| 总耗时 P50 / P95 (s) | {_fmt(stats.percentile(total, 50))} / {_fmt(stats.percentile(total, 95))} |",
        f"| 平均 token / 轮 | {sum(tokens) / n:.0f} |",
        f"| 平均工具调用 / 轮 | {sum(calls) / n:.2f} |",
    ]  # fmt: skip


def tables(
    suite, agent_results: list[dict], retrieval_results: list[dict], final_verdicts: Verdicts | None
) -> str:
    lines = _coverage_matrix(suite)
    runs = _case_runs(agent_results)
    if runs:
        lines += ["", "## 总体结果（每题每次运行计 1 次）", "",
                  "| 指标 | 通过/总数 | 比例 | Wilson 95% |", "| --- | --- | --- | --- |"]  # fmt: skip
        lines += _summary(runs, final_verdicts, "")
        lines += [
            "",
            "| 一致性 | 值 | 口径 |",
            "| --- | --- | --- |",
            *_pass_hat(runs, final_verdicts),
        ]
        group_dims = {group["id"]: group["dimensions"] for group in suite.groups}
        lines += ["", "## 分维度", "", "| 维度 | 指标 | 通过/总数 | 比例 | Wilson 95% |",
                  "| --- | --- | --- | --- | --- |"]  # fmt: skip
        for dim in dataset.DIMENSIONS:
            subset = [r for r in runs if dim in group_dims.get(r[0], [])]
            if subset:
                lines += [f"| {dim} {row[1:]}" for row in _summary(subset, final_verdicts, "")]
        lines += ["", "## 分组", "", "| 组 | 指标 | 通过/总数 | 比例 | Wilson 95% |",
                  "| --- | --- | --- | --- | --- |"]  # fmt: skip
        for group in suite.groups:
            subset = [r for r in runs if r[0] == group["id"]]
            if subset:
                lines += [
                    f"| {group['id']} {row[1:]}" for row in _summary(subset, final_verdicts, "")
                ]
        lines += ["", *_cost(runs)]
    if retrieval_results:
        lines += ["", *_retrieval(retrieval_results)]
    return "\n".join(lines) + "\n"


DIMENSION_NAMES = {
    "D1": "检索准确", "D2": "回答正确", "D3": "引用可追溯", "D4": "范围隔离", "D5": "工具自主选择",
    "D6": "多轮与指代", "D7": "不编造", "D8": "复杂任务", "D9": "异常与降级",
}  # fmt: skip
ENGLISH_KBS = {"AI论文", "多跳问答"}


MISSING_WORDS = (
    "未披露",
    "没有披露",
    "未提及",
    "没有提及",
    "未找到",
    "没有找到",
    "没有相关",
    "不足",
)


def _features(group: dict, case: dict, suite) -> dict[str, bool]:
    expect = case["expect"]
    question = " ".join(turn["content"] for turn in case["turns"])
    kbs = {suite.documents[key]["knowledge_base"] for key in case["documents"]}
    narrower = set(dataset.scope(group, case)) < set(group["knowledge_bases"])
    return {
        "多轮追问": len(case["turns"]) > 1,
        "含计算": bool(case["calculations"]),
        "明确无答案": bool(expect.get("no_answer")),
        # A text-only claim asking the answer to say something is not disclosed.
        "部分缺失需说明": not expect.get("no_answer")
        and any(
            not atom["evidence_groups"]
            and any(
                word in option
                for group_ in atom["answer_keywords"]
                for option in group_
                for word in MISSING_WORDS
            )
            for atom in case["claims"]
        ),
        "期望特定工具": bool(expect.get("tools") or expect.get("forbidden_tools")),
        "异常注入": bool(group.get("overrides"))
        or expect.get("stop_reason", "answered") != "answered"
        or bool(expect.get("degraded")),
        "对话中删除文档": any(turn.get("before") for turn in case["turns"]),
        "限定范围或禁用文档": narrower or bool(case["forbidden_documents"]),
        "多文档证据": len(set(case["documents"])) > 1,
        "中文问英文资料": bool(kbs)
        and kbs <= ENGLISH_KBS
        and any("\u4e00" <= ch <= "\u9fff" for ch in question),
    }


def dimension_report(suite, extra_questions: list[dict] | None = None) -> str:
    """Static statistics of what the suite examines; needs no run results."""
    cases = [(g, c) for g in suite.groups for c in g["cases"]]
    claims = sum(len(c["claims"]) for _, c in cases)
    calcs = sum(len(c["calculations"]) for _, c in cases)
    split = Counter(g["split"] for g, _ in cases)
    origin = Counter(c["origin"] for _, c in cases)
    review = Counter(c["review"]["status"] for _, c in cases)
    out = [
        "# 评测集 v2：考察维度统计",
        "",
        "由 `eval/suite/report.py --dimensions` 根据组文件与知识库清单生成，不含运行结果。",
        "",
        "## 总览",
        "",
        "| 项目 | 数量 |",
        "| --- | --- |",
        f"| 知识库 | {len({d['knowledge_base'] for d in suite.documents.values()})} |",
        f"| 文档 | {len(suite.documents)} |",
        f"| 评测组 | {len(suite.groups)} |",
        f"| 题目 | {len(cases)}（dev {split['dev']} / test {split['test']}） |",
        f"| 问答轮次 | {sum(len(c['turns']) for _, c in cases)} |",
        f"| 评分要点 | {claims} |",
        f"| 证据锚点 | {len(suite.evidence)} |",
        f"| 计算标注 | {calcs} |",
        "| 题目来源 | " + "，".join(f"{k} {v}" for k, v in sorted(origin.items())) + " |",
        "| 审阅状态 | " + "，".join(f"{k} {v}" for k, v in sorted(review.items())) + " |",
    ]
    if extra_questions is not None:
        answerable = sum(1 for q in extra_questions if q.get("answerable"))
        out.append(f"| 检索对比扩展查询：{len(extra_questions)} | 可回答 {answerable} |")
    out += [
        "",
        "## 维度统计",
        "",
        "一组可覆盖多个维度；题数与要点数按所属组的维度计入。",
        "",
        "| 维度 | 名称 | 组数 | 题数 | 要点数 | dev 题 | test 题 | 组 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for dim in dataset.DIMENSIONS:
        members = [g for g in suite.groups if dim in g["dimensions"]]
        rows = [(g, c) for g, c in cases if dim in g["dimensions"]]
        per_split = Counter(g["split"] for g, _ in rows)
        out.append(
            f"| {dim} | {DIMENSION_NAMES[dim]} | {len(members)} | {len(rows)} | "
            f"{sum(len(c['claims']) for _, c in rows)} | {per_split['dev']} | {per_split['test']} | "
            f"{' '.join(g['id'] for g in members) or '—'} |"
        )
    feature_rows: dict[str, list] = {}
    for group, case in cases:
        for name, hit in _features(group, case, suite).items():
            feature_rows.setdefault(name, [])
            if hit:
                feature_rows[name].append(case["id"])
    out += ["", "## 题型特征", "", "| 特征 | 题数 | 题目 |", "| --- | --- | --- |"]
    for name, ids in feature_rows.items():
        out.append(f"| {name} | {len(ids)} | {'、'.join(ids) or '—'} |")
    out += ["", "## 知识库", "", "| 知识库 | 文档 | 题数 | 要点数 | 证据锚点 |",
            "| --- | --- | --- | --- | --- |"]  # fmt: skip
    for name in sorted({d["knowledge_base"] for d in suite.documents.values()}):
        docs = {k for k, d in suite.documents.items() if d["knowledge_base"] == name}
        rows = [(g, c) for g, c in cases if name in dataset.scope(g, c)]
        anchors = sum(1 for item in suite.evidence.values() if item["document"] in docs)
        out.append(
            f"| {name} | {len(docs)} | {len(rows)} | {sum(len(c['claims']) for _, c in rows)} | {anchors} |"
        )
    out += ["", "## 文件格式", "", "| 格式 | 组数 | 题数 |", "| --- | --- | --- |"]
    for fmt in ("pdf", "docx", "txt"):
        rows = [
            (g, c) for g, c in cases
            if any(dataset.doc_format(suite.documents[k]) == fmt for k in c["documents"])
        ]  # fmt: skip
        out.append(f"| {fmt} | {len({g['id'] for g, _ in rows})} | {len(rows)} |")
    out += ["", "## 分组明细", "", "| 组 | 标题 | split | 维度 | 题数 | 要点数 | 算法对比 |",
            "| --- | --- | --- | --- | --- | --- | --- |"]  # fmt: skip
    for g in suite.groups:
        out.append(
            f"| {g['id']} | {g['title']} | {g['split']} | {' '.join(g['dimensions'])} | "
            f"{len(g['cases'])} | {sum(len(c['claims']) for c in g['cases'])} | "
            f"{'是' if g.get('compare') else '否'} |"
        )
    problems = dataset.check_coverage(dataset.coverage(suite))
    out += ["", "## 覆盖约束检查", ""]
    out += [f"- 未满足：{p}" for p in problems] or ["- 全部满足"]
    return "\n".join(out) + "\n"


def load_verdicts(path: Path) -> Verdicts:
    rows = json.loads(path.read_text(encoding="utf-8"))["cases"]
    return {(row["case_id"], row["repeat_index"]): row["semantic_pass"] for row in rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", type=Path, nargs="*", default=[])
    parser.add_argument("--retrieval", type=Path, nargs="*", default=[])
    parser.add_argument("--verdicts", type=Path)
    parser.add_argument("--dimensions", action="store_true", help="static dimension statistics")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.dimensions:
        suite = dataset.load()
        questions = suite.root / "kbs/KB6/questions.jsonl"
        extra = [json.loads(line) for line in questions.read_text("utf-8").splitlines() if line]
        args.out.write_text(dimension_report(suite, extra), encoding="utf-8")
        print(f"维度统计：{args.out}")
        return 0
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    text = tables(
        dataset.load(),
        [read(p) for p in args.agent],
        [read(p) for p in args.retrieval],
        load_verdicts(args.verdicts) if args.verdicts else None,
    )
    args.out.write_text(text, encoding="utf-8")
    print(f"表格：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
