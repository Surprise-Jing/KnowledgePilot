"""Real-model end-to-end smoke check against a running KnowledgePilot server.

Steps (docs/briefs/D-testing.md §3):

1. alice creates two knowledge bases and a two-level folder tree, uploads
   examples/knowledge.txt plus a generated DOCX and PDF, and waits until all parse to success
2. bob searching alice's knowledge base gets 404 knowledge_base_not_found
3. a new conversation question streams tool_call, token and citation events and ends with
   done(stop_reason=answered)
4. the follow-up "这份文档还提到了什么？" cites the same document as turn 1
5. GET /api/runs/{id} has the tool calls with durations
6. after deleting the cited document, searching no longer returns it

The first failing step prints `FAIL <step>` with details and exits with status 1. On success
the created knowledge bases are deleted unless --keep is given.

Start the server first (it must be configured with a real model in backend/.env), e.g.

    ./start.sh            # or: uv run --directory backend uvicorn app.main:app --port 8000
    uv run --directory backend python ../scripts/smoke.py [--base-url http://127.0.0.1:8000]

The script only talks to the server; it never reads backend/.env and prints no secrets.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from io import BytesIO
from pathlib import Path

import httpx
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

REPO_ROOT = Path(__file__).resolve().parents[1]
ALICE = {"X-User-Id": "smoke_alice"}
BOB = {"X-User-Id": "smoke_bob"}
PARSE_TIMEOUT_SECONDS = 180
QUESTION = "删除文档时系统会怎么处理索引？请给出引用。"
FOLLOW_UP = "这份文档还提到了什么？"
SEARCH_QUERY = "删除文档时如何处理索引"


class StepFailed(Exception):
    pass


def require(condition: object, message: str) -> None:
    if not condition:
        raise StepFailed(message)


def expect(response: httpx.Response, status: int):
    require(
        response.status_code == status,
        f"{response.request.method} {response.request.url.path} -> {response.status_code} "
        f"(期望 {status})：{response.text[:300]}",
    )
    return response.json() if response.content else None


def docx_bytes() -> bytes:
    document = Document()
    document.add_heading("检索策略说明", level=1)
    document.add_paragraph(
        "混合检索同时使用 BM25 关键词检索和稠密向量检索，再用倒数排名融合（RRF）合并排序结果。"
    )
    document.add_heading("重排", level=2)
    document.add_paragraph("融合时每一路取最多 50 个候选片段，按内容去重后截取前 K 个结果。")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def pdf_bytes() -> bytes:
    # pypdf's built-in Helvetica cannot render CJK, so the PDF text is English.
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 20 250 Td (The maximum upload size is 10 MiB. "
        b"Scanned PDF files need OCR, which is not supported.) Tj ET"
    )
    page[NameObject("/Contents")] = stream
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def ask(client: httpx.Client, conversation_id: str, content: str, kb_ids: list[str]) -> dict:
    """Send one question and parse the SSE stream into events plus timings."""
    events: list[tuple[str, dict]] = []
    started = time.perf_counter()
    first_token = None
    with client.stream(
        "POST",
        f"/api/conversations/{conversation_id}/messages",
        headers=ALICE,
        json={"content": content, "knowledge_base_ids": kb_ids},
    ) as response:
        if response.status_code != 200:
            response.read()
            raise StepFailed(f"提问返回 {response.status_code}：{response.text[:300]}")
        event, data = None, []
        for line in response.iter_lines():
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "" and event:
                if event == "token" and first_token is None:
                    first_token = time.perf_counter() - started
                events.append((event, json.loads("".join(data))))
                event, data = None, []
    names = [name for name, _ in events]
    return {
        "events": events,
        "names": names,
        "answer": "".join(p["delta"] for n, p in events if n == "token"),
        "tools": [p["name"] for n, p in events if n == "tool_call"],
        "cited": {p["document_id"] for n, p in events if n == "citation"},
        "errors": [p for n, p in events if n == "error"],
        "done": events[-1][1] if names and names[-1] == "done" else {},
        "ttft": first_token,
        "total": time.perf_counter() - started,
    }


def describe_turn(turn: dict) -> str:
    ttft = f"{turn['ttft']:.1f}s" if turn["ttft"] is not None else "—"
    return (
        f"工具 {turn['tools']}，token 事件 {turn['names'].count('token')}，"
        f"引用 {turn['names'].count('citation')}，首字 {ttft}，总耗时 {turn['total']:.1f}s，"
        f"stop_reason={turn['done'].get('stop_reason')}，错误 {turn['errors']}"
    )


class Smoke:
    def __init__(self, client: httpx.Client, repo_root: Path):
        self.client = client
        self.repo_root = repo_root
        self.kb_ids: list[str] = []
        self.documents: dict[str, str] = {}
        self.conversation_id = ""
        self.turn1: dict = {}

    def step_health(self) -> str:
        body = expect(self.client.get("/health"), 200)
        require(body.get("status") == "ok", f"/health 返回 {body}")
        return f"answer_mode={body.get('answer_mode')}"

    def step_upload(self) -> str:
        c = self.client
        stamp = time.strftime("%H%M%S")
        for name in (f"冒烟 A {stamp}", f"冒烟 B {stamp}"):
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
        require(inner["parent_id"] == outer["id"], "二级目录的 parent_id 不正确")
        files = [
            (
                kb_a,
                "knowledge.txt",
                (self.repo_root / "examples/knowledge.txt").read_bytes(),
                inner,
            ),
            (kb_a, "retrieval.docx", docx_bytes(), outer),
            (kb_a, "limits.pdf", pdf_bytes(), None),
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
        deadline = time.monotonic() + PARSE_TIMEOUT_SECONDS
        while True:
            statuses = {
                name: expect(c.get(f"/api/documents/{doc}", headers=ALICE), 200)
                for name, doc in self.documents.items()
            }
            if all(s["status"] in ("success", "failed") for s in statuses.values()):
                break
            require(time.monotonic() < deadline, f"解析超时：{statuses}")
            time.sleep(1)
        failed = {n: s["error"] for n, s in statuses.items() if s["status"] != "success"}
        require(not failed, f"解析失败：{failed}")
        return f"2 个知识库、2 层目录、{len(self.documents)} 个文件解析成功"

    def step_isolation(self) -> str:
        response = self.client.post(
            "/api/search",
            headers=BOB,
            json={"query": "删除文档", "knowledge_base_ids": [self.kb_ids[0]]},
        )
        body = expect(response, 404)
        require(
            body["error"]["code"] == "knowledge_base_not_found",
            f"错误码为 {body['error']['code']}",
        )
        return "bob 检索 alice 的知识库返回 404 knowledge_base_not_found"

    def step_ask(self) -> str:
        conversation = expect(self.client.post("/api/conversations", headers=ALICE, json={}), 201)
        self.conversation_id = conversation["id"]
        turn = ask(self.client, self.conversation_id, QUESTION, [self.kb_ids[0]])
        names = turn["names"]
        detail = describe_turn(turn)
        require(names and names[0] == "run_started", f"第一条事件不是 run_started：{detail}")
        require(
            names[-1] == "done" and names.count("done") == 1,
            f"done 不是唯一的最后一条事件：{detail}",
        )
        require("tool_call" in names, f"模型没有调用工具：{detail}")
        require(names.count("token") > 1, f"没有流式输出 token：{detail}")
        require("citation" in names, f"没有 citation 事件：{detail}")
        require(turn["done"].get("stop_reason") == "answered", f"未正常回答：{detail}")
        self.turn1 = turn
        return detail

    def step_follow_up(self) -> str:
        turn = ask(self.client, self.conversation_id, FOLLOW_UP, [self.kb_ids[0]])
        detail = describe_turn(turn)
        require(turn["done"].get("stop_reason") == "answered", f"追问未正常回答：{detail}")
        require(
            turn["cited"] & self.turn1["cited"],
            f"追问引用的文档 {turn['cited']} 与上一轮 {self.turn1['cited']} 不一致：{detail}",
        )
        return detail

    def step_trace(self) -> str:
        run_id = self.turn1["events"][0][1]["run_id"]
        run = expect(self.client.get(f"/api/runs/{run_id}", headers=ALICE), 200)
        calls = run["tool_calls"]
        require(calls, "运行记录中没有工具调用")
        require(all(call["duration_ms"] is not None for call in calls), "工具调用缺少耗时")
        require(run["stop_reason"] == "answered", f"run.stop_reason={run['stop_reason']}")
        return (
            f"{len(calls)} 次工具调用，token {run['prompt_tokens']}/{run['completion_tokens']}，"
            f"模型 {run['model']}"
        )

    def step_delete(self) -> str:
        # Delete the document turn 1 cited (normally knowledge.txt) and make sure it is gone.
        target = next(iter(self.turn1["cited"]))
        expect(self.client.delete(f"/api/documents/{target}", headers=ALICE), 204)
        hits = expect(
            self.client.post(
                "/api/search",
                headers=ALICE,
                json={"query": SEARCH_QUERY, "knowledge_base_ids": [self.kb_ids[0]]},
            ),
            200,
        )
        leaked = [hit for hit in hits if hit["document_id"] == target]
        require(not leaked, f"删除后仍检索到该文档的 {len(leaked)} 个片段")
        return f"删除文档 {target} 后检索不到其内容（剩余 {len(hits)} 个命中）"

    def cleanup(self) -> None:
        for kb_id in self.kb_ids:
            self.client.delete(f"/api/knowledge-bases/{kb_id}", headers=ALICE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KnowledgePilot real-model smoke check")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="contains examples/")
    parser.add_argument("--timeout", type=float, default=180, help="HTTP timeout in seconds")
    parser.add_argument("--keep", action="store_true", help="keep the created knowledge bases")
    args = parser.parse_args(argv)

    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        smoke = Smoke(client, args.repo_root)
        steps = [
            ("0 服务可用", smoke.step_health),
            ("1 建库、建目录、上传三种格式并解析成功", smoke.step_upload),
            ("2 跨用户隔离", smoke.step_isolation),
            ("3 提问：工具调用、流式回答、引用", smoke.step_ask),
            ("4 追问“这份文档”引用同一文档", smoke.step_follow_up),
            ("5 运行轨迹已记录", smoke.step_trace),
            ("6 删除文档后检索不到", smoke.step_delete),
        ]
        for name, step in steps:
            try:
                detail = step()
            except (StepFailed, httpx.HTTPError) as exc:
                print(f"FAIL {name}：{exc}", flush=True)
                print(f"\n冒烟失败于步骤「{name}」；已创建的数据保留供排查。", flush=True)
                return 1
            print(f"PASS {name} — {detail}", flush=True)
        if not args.keep:
            smoke.cleanup()
    print(f"\n{len(steps)}/{len(steps)} 步通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
