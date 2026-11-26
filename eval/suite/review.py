"""Render eval/suite/REVIEW.md: every group and case with its gold annotation, for human review.

    uv run --directory backend python ../eval/suite/review.py

Reviewers set each case's `review.status` in the group JSON to approved / revised / rejected
(revised: say what changed in `review.note`), then rerun this script and `run.py --verify-sources`.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from suite import dataset


def _where(item: dict, documents: dict) -> str:
    filename = documents[item["document"]]["filename"]
    locator = item["locator"]
    if locator["kind"] == "pdf_page":
        return f"{filename} 第 {locator['page']} 页"
    if locator["kind"] == "txt_lines":
        return f"{filename} 第 {locator['line']} 行"
    return f"{filename}（{locator.get('heading', 'DOCX')}）"


def _claim(atom: dict, suite) -> list[str]:
    signals = [f"{n['value']} {n['unit']}（±{n['tolerance']}）" for n in atom["answer_numbers"]]
    signals += [" / ".join(group) for group in atom["answer_keywords"]]
    lines = [f"- **{atom['label']}**（`{atom['id']}`）：{'；'.join(signals) or '—'}"]
    for alternatives in atom["evidence_groups"]:
        options = " 或 ".join(
            f"{_where(suite.evidence[key], suite.documents)}：“{suite.evidence[key]['anchor']}”"
            for key in alternatives
        )
        lines.append(f"  - 证据：{options}")
    return lines


def render(suite) -> str:
    statuses = Counter(case["review"]["status"] for g in suite.groups for case in g["cases"])
    out = [
        "# 评测集 v2 人工审阅",
        "",
        "由 `eval/suite/review.py` 生成，请勿手改；修改组文件 `eval/suite/groups/Gxx.json` 后重新生成。",
        "审阅时在组文件中把 `review.status` 改为 approved / revised / rejected，revised 在 `review.note` 写明改动。",
        "",
        "审阅状态统计：" + "，".join(f"{k} {v}" for k, v in sorted(statuses.items())),
        "",
    ]
    for group in suite.groups:
        out += [
            f"## {group['id']} {group['title']}",
            "",
            f"知识库：{'、'.join(group['knowledge_bases'])}｜维度：{' '.join(group['dimensions'])}"
            f"｜split：{group['split']}｜算法对比：{'是' if group.get('compare') else '否'}"
            f"｜演示候选：{'是' if group.get('demo_candidate') else '否'}"
            + (f"｜设置覆盖：`{group['overrides']}`" if group.get("overrides") else ""),
            "",
        ]
        for case in group["cases"]:
            expect = case["expect"]
            flags = [k for k in ("no_answer", "degraded") if expect.get(k)]
            if expect.get("tools"):
                flags.append(f"期望工具 {expect['tools']}")
            if expect.get("error_code"):
                flags.append(f"期望错误 {expect['error_code']}")
            out += [f"### {case['id']}", ""]
            out.append(
                f"来源：{case['origin']}｜问答范围：{'、'.join(dataset.scope(group, case))}"
                + (f"｜期望：{'，'.join(flags)}" if flags else "")
            )
            out.append("")
            for index, turn in enumerate(case["turns"], 1):
                before = "（本轮前删除文档）" if turn.get("before") else ""
                out.append(f"{index}. {before}{turn['content']}")
            out += ["", f"参考答案：{case['reference_answer']}", "", "要点与证据："]
            for atom in case["claims"]:
                out += _claim(atom, suite)
            for spec in case["calculations"]:
                out.append(
                    f"- 计算 `{spec['id']}`：{spec['operation']}({', '.join(spec['operands'])})"
                    f" = {spec['expected']} {spec['unit']}"
                )
            out += ["", "评审规则：" + "；".join(case["semantic_rubric"]), ""]
            out += [f"审阅状态：{case['review']['status']}", "", "审阅意见：", ""]
    return "\n".join(out) + "\n"


def main() -> int:
    path = dataset.SUITE / "REVIEW.md"
    path.write_text(render(dataset.load()), encoding="utf-8")
    print(f"审阅文件：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
