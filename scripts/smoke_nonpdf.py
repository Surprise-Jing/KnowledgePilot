"""Non-PDF real-model acceptance for the P0 round (TXT + DOCX only).

Usage (server must run with a real model configured):

    uv run --directory backend python ../scripts/smoke_nonpdf.py [--base-url http://127.0.0.1:8000]

Reuses scripts/smoke.py steps, drops the PDF upload, and adds:
- Chinese DOCX with heading/table: table-row citation location
- multi-KB scope: B-only fact not cited when only A is selected; cited when A+B selected
- follow-up after deleting the cited document: no read_document on the deleted id,
  answer printed for manual review of the "deleted vs no basis" wording
"""

from __future__ import annotations

import argparse
import sys
import time
from io import BytesIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import httpx
import smoke
from docx import Document
from smoke import ALICE, Smoke, StepFailed, ask, describe_turn, expect, require

TABLE_QUESTION = "报销制度里，差旅住宿的单晚标准上限是多少？请给出引用。"
B_QUESTION = "季度预算审批需要谁签字？请给出引用。"
DELETED_FOLLOW_UP = "第一轮回答所依据的那份文档，现在还能查看原文吗？上一轮回答的依据是什么？"


def policy_docx() -> bytes:
    document = Document()
    document.add_heading("员工报销制度", level=1)
    document.add_paragraph("本制度适用于全体员工的差旅与日常费用报销。")
    document.add_heading("差旅标准", level=2)
    document.add_paragraph("差旅住宿按城市等级执行以下标准，超出部分由个人承担。")
    table = document.add_table(rows=3, cols=3)
    for row, cells in zip(
        table.rows,
        [
            ("城市等级", "住宿上限（元/晚）", "伙食补助（元/天）"),
            ("一线城市", "650", "120"),
            ("其他城市", "450", "90"),
        ],
    ):
        for cell, text in zip(row.cells, cells):
            cell.text = text
    output = BytesIO()
    document.save(output)
    return output.getvalue()


class NonPdfSmoke(Smoke):
    def step_upload(self) -> str:
        c = self.client
        stamp = time.strftime("%H%M%S")
        for name in (f"P0 验收 A {stamp}", f"P0 验收 B {stamp}"):
            kb = expect(c.post("/api/knowledge-bases", headers=ALICE, json={"name": name}), 201)
            self.kb_ids.append(kb["id"])
        kb_a, kb_b = self.kb_ids
        outer = expect(
            c.post(f"/api/knowledge-bases/{kb_a}/folders", headers=ALICE, json={"name": "产品"}),
            201,
        )
        inner = expect(
            c.post(
                f"/api/knowledge-bases/{kb_a}/folders",
                headers=ALICE,
                json={"name": "说明", "parent_id": outer["id"]},
            ),
            201,
        )
        files = [
            (
                kb_a,
                "knowledge.txt",
                (self.repo_root / "examples/knowledge.txt").read_bytes(),
                inner,
            ),
            (kb_a, "retrieval.docx", smoke.docx_bytes(), outer),
            (kb_a, "报销制度.docx", policy_docx(), None),
            (kb_b, "other.txt", "B 库独有内容：季度预算审批需要财务总监签字。".encode(), None),
        ]
        for kb_id, name, data, folder in files:
            document = expect(
                c.post(
                    f"/api/knowledge-bases/{kb_id}/documents",
                    headers=ALICE,
                    files={"file": (name, data)},
                    data={"folder_id": folder["id"] if folder else ""},
                ),
                202,
            )
            self.documents[name] = document["id"]
        deadline = time.monotonic() + smoke.PARSE_TIMEOUT_SECONDS
        while True:
            statuses = {
                n: expect(c.get(f"/api/documents/{d}", headers=ALICE), 200)
                for n, d in self.documents.items()
            }
            if all(s["status"] in ("success", "failed") for s in statuses.values()):
                break
            require(time.monotonic() < deadline, f"解析超时：{statuses}")
            time.sleep(1)
        failed = {n: s["error"] for n, s in statuses.items() if s["status"] != "success"}
        require(not failed, f"解析失败：{failed}")
        return f"2 个知识库、2 层目录、{len(self.documents)} 个 TXT/DOCX 文件解析成功（无 PDF）"

    def citations(self, turn: dict) -> list[dict]:
        return [p for n, p in turn["events"] if n == "citation"]

    def step_docx_table(self) -> str:
        conv = expect(self.client.post("/api/conversations", headers=ALICE, json={}), 201)
        turn = ask(self.client, conv["id"], TABLE_QUESTION, [self.kb_ids[0]])
        detail = describe_turn(turn)
        require(turn["done"].get("stop_reason") == "answered", f"未正常回答：{detail}")
        cites = [c for c in self.citations(turn) if c["filename"] == "报销制度.docx"]
        require(cites, f"没有引用 报销制度.docx：{detail}")
        table_cites = [c for c in cites if (c["location"] or {}).get("kind") == "docx_table_rows"]
        require(table_cites, f"引用位置不是表格行：{[c['location_label'] for c in cites]}")
        require(any("650" in c["content"] for c in table_cites), "表格行引用内容不含 650")
        require("650" in turn["answer"], f"回答未给出 650：{turn['answer'][:200]}")
        return f"{detail}；位置 {[c['location_label'] for c in table_cites]}"

    def step_scope(self) -> str:
        kb_a, kb_b = self.kb_ids
        conv = expect(self.client.post("/api/conversations", headers=ALICE, json={}), 201)
        only_a = ask(self.client, conv["id"], B_QUESTION, [kb_a])
        require(
            only_a["done"].get("stop_reason") in ("answered", "no_evidence"),
            f"仅 A 库提问异常结束：{describe_turn(only_a)}",
        )
        leaked = [c for c in self.citations(only_a) if c["filename"] == "other.txt"]
        require(not leaked, "只选 A 库时引用了 B 库的 other.txt")
        require(
            "财务总监" not in only_a["answer"],
            f"只选 A 库时回答出现 B 库事实：{only_a['answer'][:200]}",
        )
        conv2 = expect(self.client.post("/api/conversations", headers=ALICE, json={}), 201)
        both = ask(self.client, conv2["id"], B_QUESTION, [kb_a, kb_b])
        hit = [c for c in self.citations(both) if c["filename"] == "other.txt"]
        require(hit, f"选择 A+B 时未引用 other.txt：{describe_turn(both)}")
        return (
            f"仅 A：{describe_turn(only_a)} 回答摘要「{only_a['answer'][:80]}」；"
            f"A+B：引用 other.txt {hit[0]['location_label']}"
        )

    def step_deleted_follow_up(self) -> str:
        deleted = next(iter(self.turn1["cited"]))
        turn = ask(self.client, self.conversation_id, DELETED_FOLLOW_UP, [self.kb_ids[0]])
        detail = describe_turn(turn)
        require(
            turn["done"].get("stop_reason") in ("answered", "no_evidence"), f"异常结束：{detail}"
        )
        require(deleted not in turn["cited"], "删除后追问仍引用了已删除文档")
        run_id = turn["events"][0][1]["run_id"]
        run = expect(self.client.get(f"/api/runs/{run_id}", headers=ALICE), 200)
        reads = [
            c
            for c in run["tool_calls"]
            if c["tool_name"] == "read_document" and c["arguments"].get("document_id") == deleted
        ]
        self.deleted_answer = turn["answer"]
        return (
            f"{detail}；对已删除文档的 read_document 调用 {len(reads)} 次；"
            f"回答全文：「{turn['answer']}」"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="contains examples/")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        s = NonPdfSmoke(client, args.repo_root)
        steps = [
            ("0 服务可用", s.step_health),
            ("1 建库、建目录、上传 TXT/DOCX 并解析成功", s.step_upload),
            ("2 跨用户隔离", s.step_isolation),
            ("3 提问：工具调用、流式回答、引用", s.step_ask),
            ("4 追问“这份文档”引用同一文档", s.step_follow_up),
            ("5 运行轨迹已记录", s.step_trace),
            ("6 中文 DOCX 表格行引用定位", s.step_docx_table),
            ("7 多库范围不串库", s.step_scope),
            ("8 删除文档后检索不到", s.step_delete),
            ("9 删除后追问", s.step_deleted_follow_up),
        ]
        failures = 0
        for name, step in steps:
            try:
                detail = step()
            except (StepFailed, httpx.HTTPError) as exc:
                failures += 1
                print(f"FAIL {name}：{exc}", flush=True)
                if name.startswith(("0", "1", "3")):
                    return 1
                continue
            print(f"PASS {name} — {detail}", flush=True)
        s.cleanup()
    print(f"\n{len(steps) - failures}/{len(steps)} 步通过")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
