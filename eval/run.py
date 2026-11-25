"""KnowledgePilot evaluation runner (docs/briefs/D-testing.md §2, docs/eval-report.md).

Every run starts from a fresh temporary data directory, builds the corpus (eval/build_corpus.py),
creates the knowledge bases and folders, uploads the files through the HTTP API and waits until
parsing succeeds. Then:

- `--mode retrieval`: POST /api/search for every case with `expect.relevant` and computes
  Hit@1/3/5 and MRR per strategy. No chat model is used; `vector` / `hybrid` need the
  embedding settings from `--env-file`.
- `--mode agent`: full conversations with the real chat model through
  POST /api/conversations/{id}/messages (SSE), then judges every case (eval/metrics.py).

Usage (from the repository root):

    uv run --directory backend python ../eval/run.py --mode retrieval --retriever all \\
        --env-file backend/.env
    uv run --directory backend python ../eval/run.py --mode agent --retriever hybrid \\
        --env-file backend/.env

Results go to eval/results/<timestamp>-<mode>-<strategy>.json (git-ignored); a Markdown
summary is printed to stdout. Neither contains API keys or service URLs.

How the app is hosted: the backend app is built in-process with
`create_app(Settings(_env_file=<env-file>, data_dir=<tmp>, ...))`, so each run is isolated and
per-case settings overrides (e.g. a tiny `llm_timeout`) need no environment juggling.
Retrieval mode talks to it through FastAPI's TestClient. Agent mode serves the same app with
uvicorn on a background thread bound to 127.0.0.1 and an ephemeral port, because TestClient
buffers a streamed response until it ends, which would make time-to-first-token meaningless.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import tempfile
import threading
import time
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Self

import httpx

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(EVAL_DIR))

import build_corpus
import metrics

CASES_PATH = EVAL_DIR / "cases.jsonl"
RESULTS_DIR = EVAL_DIR / "results"
STRATEGIES = ("cosine", "bm25", "vector", "hybrid")
EVAL_USER = "eval"
PARSE_TIMEOUT_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 300


def authorize(client, db) -> None:
    """Sign `client` in as the eval user of this app's database. Evaluation runs against a
    temporary data directory, so the account is created without a password and the session
    is opened directly; requests then carry it as an ordinary session cookie."""
    from app.services import auth

    with db.connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO users(id, name, username) VALUES (?, ?, ?)",
            (EVAL_USER, EVAL_USER, EVAL_USER),
        )
        token = auth.create_session(connection, EVAL_USER, ttl_days=1)
    client.cookies.set(auth.SESSION_COOKIE, token)


# ---- cases ----


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def retrieval_query(case: dict) -> str | None:
    """Self-contained query for retrieval mode, or None when the case has no retrieval target
    (no `relevant`, expected no-answer, or `retrieval_query: null`)."""
    expect = case["expect"]
    if not expect.get("relevant") or expect.get("expect_no_answer"):
        return None
    if "retrieval_query" in expect:
        return expect["retrieval_query"]
    return case["turns"][0]["content"]


# ---- app hosting ----


def load_create_app():
    """Import the app factory. Importing app.main configures logging once at INFO (its
    module-level app); lower it so request logs do not drown the summary."""
    from app.main import create_app

    logging.getLogger().setLevel(logging.WARNING)
    return create_app


def make_settings(env_file: Path | None, data_dir: Path, **overrides: Any):
    from app.config import Settings

    values = {
        "data_dir": data_dir,
        # Never serve a frontend build during evaluation.
        "frontend_dir": data_dir / "no-frontend",
        "log_level": "WARNING",
        **overrides,
    }
    return Settings(_env_file=env_file, **values)


class LiveServer:
    """Serve an ASGI app with uvicorn on a background thread (real streaming over TCP)."""

    def __init__(self, app):
        import uvicorn

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind(("127.0.0.1", 0))
        self.base_url = f"http://127.0.0.1:{self._socket.getsockname()[1]}"
        self._server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [self._socket]}, daemon=True
        )

    def __enter__(self) -> Self:
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=15)
        self._socket.close()


# ---- corpus setup ----


@dataclass
class CorpusIds:
    knowledge_bases: dict[str, str] = field(default_factory=dict)  # name → id
    folders: dict[tuple[str, tuple[str, ...]], str] = field(default_factory=dict)
    documents: dict[str, str] = field(default_factory=dict)  # filename → id

    def kb_ids(self, names: list[str]) -> list[str]:
        return [self.knowledge_bases[name] for name in names]

    def filename_of(self, document_id: str) -> str | None:
        return next((n for n, i in self.documents.items() if i == document_id), None)


def expect_status(response: httpx.Response, status: int) -> Any:
    if response.status_code != status:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {response.text[:300]}"
        )
    return response.json() if response.content else None


def setup_corpus(client: httpx.Client, build_dir: Path) -> CorpusIds:
    """Create knowledge bases, nested folders and upload every corpus file; wait for parsing.
    Raises when any file does not parse to `success`."""
    paths = build_corpus.build(build_dir)
    ids = CorpusIds()
    for name in build_corpus.KNOWLEDGE_BASES:
        kb = client.post("/api/knowledge-bases", json={"name": name})
        ids.knowledge_bases[name] = expect_status(kb, 201)["id"]
    for item in build_corpus.CORPUS:
        kb_id = ids.knowledge_bases[item.knowledge_base]
        parent = None
        for depth in range(1, len(item.folder) + 1):
            key = (item.knowledge_base, item.folder[:depth])
            if key not in ids.folders:
                body = {"name": item.folder[depth - 1], "parent_id": parent}
                folder = client.post(f"/api/knowledge-bases/{kb_id}/folders", json=body)
                ids.folders[key] = expect_status(folder, 201)["id"]
            parent = ids.folders[key]
        upload = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            files={"file": (item.filename, paths[item.filename].read_bytes())},
            data={"folder_id": parent or ""},
        )
        ids.documents[item.filename] = expect_status(upload, 202)["id"]
    deadline = time.monotonic() + PARSE_TIMEOUT_SECONDS
    while True:
        statuses = {
            name: expect_status(client.get(f"/api/documents/{doc}"), 200)
            for name, doc in ids.documents.items()
        }
        if all(s["status"] in ("success", "failed") for s in statuses.values()):
            break
        if time.monotonic() > deadline:
            raise RuntimeError("corpus parsing did not finish in time")
        time.sleep(0.5)
    failed = {name: s["error"] for name, s in statuses.items() if s["status"] != "success"}
    if failed:
        raise RuntimeError(f"corpus files failed to parse: {failed}")
    return ids


def search(client: httpx.Client, query: str, kb_ids: list[str], top_k: int) -> list[dict]:
    body = {"query": query, "knowledge_base_ids": kb_ids, "top_k": top_k}
    return expect_status(client.post("/api/search", json=body), 200)


# ---- retrieval mode ----


def evaluate_retrieval(
    client: httpx.Client, ids: CorpusIds, cases: list[dict], strategy: str, top_k: int
) -> dict:
    rows = []
    for case in cases:
        query = retrieval_query(case)
        if query is None:
            continue
        started = time.perf_counter()
        hits = search(client, query, ids.kb_ids(case["knowledge_bases"]), top_k)
        elapsed = time.perf_counter() - started
        rank = metrics.first_relevant_rank(hits, case["expect"]["relevant"])
        rows.append(
            {
                "id": case["id"],
                "category": case["category"],
                "query": query,
                "rank": rank,
                "latency_s": round(elapsed, 4),
                "top": [
                    {"filename": h["filename"], "label": h["location"]["label"]} for h in hits[:5]
                ],
            }
        )
    summary = metrics.retrieval_metrics([row["rank"] for row in rows])
    summary["latency_p50_s"] = metrics.percentile([row["latency_s"] for row in rows], 50)
    return {"strategy": strategy, "summary": summary, "cases": rows}


def run_retrieval(env_file: Path | None, strategies: list[str], cases: list[dict], top_k: int):
    from fastapi.testclient import TestClient

    create_app = load_create_app()

    # Vectors are written at ingestion time, so ingest once with an embedding strategy when
    # any requested strategy needs vectors; lexical strategies ignore stored vectors.
    ingest_with = "hybrid" if set(strategies) & {"vector", "hybrid"} else strategies[0]
    with tempfile.TemporaryDirectory(prefix="kp-eval-") as tmp:
        data_dir = Path(tmp) / "data"
        base = make_settings(env_file, data_dir, retriever=ingest_with)
        with TestClient(create_app(base)) as client:
            authorize(client, client.app.state.db)
            ids = setup_corpus(client, Path(tmp) / "corpus")
        results = []
        for strategy in strategies:
            settings = base.model_copy(update={"retriever": strategy})
            # A separate app over the same data directory, configured for this strategy.
            with TestClient(create_app(settings)) as client:
                authorize(client, client.app.state.db)
                results.append(evaluate_retrieval(client, ids, cases, strategy, top_k))
    return {"top_k": top_k, "ingested_with": ingest_with, "strategies": results}


def retrieval_markdown(result: dict) -> str:
    lines = [
        f"### 检索评测（top_k={result['top_k']}，入库策略 {result['ingested_with']}）",
        "",
        "| 策略 | 查询数 | Hit@1 | Hit@3 | Hit@5 | MRR | P50 延迟 (s) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in result["strategies"]:
        s = item["summary"]
        lines.append(
            f"| {item['strategy']} | {s['queries']} | {s['hit@1']:.3f} | {s['hit@3']:.3f} | "
            f"{s['hit@5']:.3f} | {s['mrr']:.3f} | {s['latency_p50_s']:.3f} |"
        )
    lines += ["", "| 用例 | " + " | ".join(i["strategy"] for i in result["strategies"]) + " |"]
    lines.append("| --- |" + " --- |" * len(result["strategies"]))
    for index, row in enumerate(result["strategies"][0]["cases"]):
        ranks = [item["cases"][index]["rank"] for item in result["strategies"]]
        cells = " | ".join("—" if rank is None else str(rank) for rank in ranks)
        lines.append(f"| {row['id']} | {cells} |")
    lines.append("")
    lines.append("（单元格为首个相关片段的排名，— 表示 top_k 内未命中）")
    return "\n".join(lines)


# ---- agent mode ----


def stream_turn(client: httpx.Client, conversation_id: str, content: str, kb_ids: list[str]):
    """POST one question and parse the SSE stream. Returns events and timings."""
    events: list[tuple[str, dict]] = []
    started = time.perf_counter()
    first_token = None
    with client.stream(
        "POST",
        f"/api/conversations/{conversation_id}/messages",
        json={"content": content, "knowledge_base_ids": kb_ids},
    ) as response:
        if response.status_code != 200:
            response.read()
            raise RuntimeError(f"message -> {response.status_code}: {response.text[:300]}")
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
    return events, first_token, time.perf_counter() - started


def summarize_turn(events: list[tuple[str, dict]]) -> dict:
    calls: dict[str, dict] = {}
    for name, payload in events:
        if name == "tool_call":
            calls[payload["call_id"]] = {
                "name": payload["name"],
                "arguments": payload["arguments"],
                "attempt": payload.get("attempt", 1),
                "status": None,
                "error_code": None,
                "fallback": None,
                "summary": None,
                "duration_ms": None,
            }
        elif name == "tool_result" and payload["call_id"] in calls:
            calls[payload["call_id"]].update(
                status=payload["status"],
                error_code=(payload.get("error") or {}).get("code"),
                fallback=payload.get("fallback"),
                summary=payload["summary"],
                duration_ms=payload["duration_ms"],
            )
    errors = [payload for name, payload in events if name == "error"]
    done = next((payload for name, payload in events if name == "done"), {})
    usage = done.get("usage") or {}
    return {
        "run_id": events[0][1].get("run_id") if events else None,
        "answer": "".join(p["delta"] for n, p in events if n == "token"),
        "tool_calls": list(calls.values()),
        "citations": [
            {
                "marker": p["marker"],
                "filename": p["filename"],
                "document_id": p["document_id"],
                "location": p["location"],
                "label": p["location_label"],
            }
            for n, p in events
            if n == "citation"
        ],
        "error_code": errors[0]["code"] if errors else None,
        "stop_reason": done.get("stop_reason"),
        "degraded": bool(done.get("degraded")),
        "step_count": done.get("step_count"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "event_order_ok": bool(events)
        and events[0][0] == "run_started"
        and events[-1][0] == "done"
        and [n for n, _ in events].count("done") == 1,
    }


def retrieval_coverage(client: httpx.Client, ids: CorpusIds, case: dict, turn: dict) -> bool | None:
    """Did any content tool of this turn surface a relevant location? search_chunks calls are
    replayed through /api/search with the same arguments (same data and strategy); a
    successful read_document of a relevant file counts as covered. None when no content tool
    ran or the case has no relevant locations."""
    relevant = case["expect"].get("relevant", [])
    if not relevant:
        return None
    ran_content_tool = False
    for call in turn["tool_calls"]:
        args = call["arguments"]
        if call["name"] == "search_chunks" and isinstance(args.get("query"), str):
            ran_content_tool = True
            kb_ids = args.get("knowledge_base_ids") or ids.kb_ids(case["knowledge_bases"])
            try:
                hits = search(client, args["query"], kb_ids, int(args.get("top_k") or 5))
            except RuntimeError:
                # Out-of-scope or invalid replays retrieve nothing, like the original call.
                continue
            if any(metrics.is_relevant(hit, relevant) for hit in hits):
                return True
        elif call["name"] == "read_document":
            ran_content_tool = True
            filename = ids.filename_of(str(args.get("document_id")))
            if call["status"] == "success" and any(spec["file"] == filename for spec in relevant):
                return True
    return False if ran_content_tool else None


def run_case(client: httpx.Client, replay: httpx.Client, ids: CorpusIds, case: dict) -> dict:
    conversation = expect_status(
        client.post("/api/conversations", json={"title": f"eval {case['id']}"}),
        201,
    )
    turns = []
    for turn in case["turns"]:
        for action in turn.get("before", []):
            if action["action"] != "delete_document":
                raise ValueError(f"unknown action {action['action']}")
            document_id = ids.documents[action["file"]]
            expect_status(replay.delete(f"/api/documents/{document_id}"), 204)
        events, ttft, total = stream_turn(
            client, conversation["id"], turn["content"], ids.kb_ids(case["knowledge_bases"])
        )
        result = summarize_turn(events)
        result.update(question=turn["content"], ttft_s=ttft, latency_s=total)
        result["retrieval_covered"] = retrieval_coverage(replay, ids, case, result)
        if result["run_id"]:
            run = expect_status(client.get(f"/api/runs/{result['run_id']}"), 200)
            result["run_status"] = run["status"]
            result["trace_tool_calls"] = len(run["tool_calls"])
        turns.append(result)
    verdict = metrics.judge_case(case, turns)
    return {"id": case["id"], "category": case["category"], **verdict, "turns": turns}


def agent_summary(results: list[dict], cases: list[dict]) -> dict:
    by_id = {case["id"]: case for case in cases}
    turns = [turn for result in results for turn in result["turns"]]
    tool_calls = [call for turn in turns for call in turn["tool_calls"]]
    keyword_rates = [r["keyword_hit_rate"] for r in results if r["keyword_hit_rate"] is not None]
    cite_cases = [
        r
        for r in results
        if by_id[r["id"]]["expect"].get("relevant")
        and not by_id[r["id"]]["expect"].get("expect_no_answer")
    ]
    tool_cases = [r for r in results if by_id[r["id"]]["expect"].get("expect_tools")]
    no_answer_cases = [r for r in results if by_id[r["id"]]["expect"].get("expect_no_answer")]
    ttfts = [t["ttft_s"] for t in turns if t["ttft_s"] is not None]
    latencies = [t["latency_s"] for t in turns]
    prompt = sum(t["prompt_tokens"] or 0 for t in turns)
    completion = sum(t["completion_tokens"] or 0 for t in turns)

    def rate(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    return {
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "pass_rate": rate(sum(r["passed"] for r in results), len(results)),
        "by_category": {
            category: f"{sum(r['passed'] for r in results if r['category'] == category)}/{n}"
            for category, n in Counter(r["category"] for r in results).items()
        },
        "keyword_hit_rate": rate(sum(keyword_rates), len(keyword_rates)) if keyword_rates else None,
        "citation_precision": rate(
            sum(r["citations_correct"] for r in cite_cases),
            sum(r["citations_total"] for r in cite_cases),
        ),
        "citation_case_hit_rate": rate(
            sum(r["checks"].get("citation_hit", False) for r in cite_cases), len(cite_cases)
        ),
        "tool_selection_accuracy": rate(
            sum(
                all(
                    ok for name, ok in r["checks"].items() if name.startswith(("tool:", "no_tool:"))
                )
                for r in tool_cases
            ),
            len(tool_cases),
        ),
        "no_answer_accuracy": rate(
            sum(r["checks"].get("no_answer", False) for r in no_answer_cases), len(no_answer_cases)
        ),
        "tool_calls": len(tool_calls),
        "tool_success_rate": rate(
            sum(call["status"] == "success" for call in tool_calls), len(tool_calls)
        ),
        "tool_error_codes": dict(Counter(c["error_code"] for c in tool_calls if c["error_code"])),
        "tool_usage": dict(Counter(call["name"] for call in tool_calls)),
        "avg_tool_calls_per_case": rate(len(tool_calls), len(results)),
        "turns": len(turns),
        "degraded_runs": sum(t["degraded"] for t in turns),
        "event_order_ok": all(t["event_order_ok"] for t in turns),
        "ttft_p50_s": metrics.percentile(ttfts, 50),
        "ttft_p95_s": metrics.percentile(ttfts, 95),
        "latency_p50_s": metrics.percentile(latencies, 50),
        "latency_p95_s": metrics.percentile(latencies, 95),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "avg_tokens_per_turn": rate(prompt + completion, len(turns)),
        "failures_by_type": dict(Counter(r["failure_type"] for r in results if r["failure_type"])),
    }


def run_agent(env_file: Path, strategy: str, cases: list[dict]) -> dict:
    create_app = load_create_app()

    with tempfile.TemporaryDirectory(prefix="kp-eval-") as tmp, ExitStack() as stack:
        data_dir = Path(tmp) / "data"
        base = make_settings(env_file, data_dir, retriever=strategy)
        if not base.llm_model or not base.llm_api_key.get_secret_value():
            raise SystemExit("agent mode needs PK_LLM_MODEL and PK_LLM_API_KEY in --env-file")
        servers: dict[str, LiveServer] = {}
        clients: dict[str, httpx.Client] = {}

        def client_for(overrides: dict) -> httpx.Client:
            # Cases with settings overrides get their own app over the same data directory.
            key = json.dumps(overrides, sort_keys=True)
            if key not in servers:
                settings = base.model_copy(update=overrides)
                app = create_app(settings)
                servers[key] = stack.enter_context(LiveServer(app))
                clients[key] = stack.enter_context(
                    httpx.Client(base_url=servers[key].base_url, timeout=REQUEST_TIMEOUT_SECONDS)
                )
                authorize(clients[key], app.state.db)
            return clients[key]

        replay = client_for({})
        ids = setup_corpus(replay, Path(tmp) / "corpus")
        results = []
        for case in cases:
            print(f"[agent] {case['id']} …", file=sys.stderr, flush=True)
            try:
                result = run_case(client_for(case.get("overrides", {})), replay, ids, case)
            except (RuntimeError, httpx.HTTPError) as exc:
                result = {
                    "id": case["id"],
                    "category": case["category"],
                    "passed": False,
                    "failure_type": "异常处理",
                    "checks": {"harness": False},
                    "reasons": [f"评测执行失败：{exc}"],
                    "keyword_hit_rate": None,
                    "citations_total": 0,
                    "citations_correct": 0,
                    "observed_error_codes": [],
                    "turns": [],
                }
            print(
                f"[agent] {case['id']} {'PASS' if result['passed'] else 'FAIL'} "
                + "; ".join(result["reasons"]),
                file=sys.stderr,
                flush=True,
            )
            results.append(result)
    return {
        "model": base.llm_model,
        "llm_stream": base.llm_stream,
        "agent_max_steps": base.agent_max_steps,
        "summary": agent_summary(results, cases),
        "cases": results,
    }


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def agent_markdown(result: dict, strategy: str) -> str:
    s = result["summary"]
    lines = [
        f"### Agent 评测（模型 {result['model']}，检索 {strategy}）",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 通过用例 | {s['passed']}/{s['cases']}（{_fmt(s['pass_rate'])}） |",
        f"| 回答要点命中率 | {_fmt(s['keyword_hit_rate'])} |",
        f"| 引用正确率（正确引用 / 全部引用） | {_fmt(s['citation_precision'])} |",
        f"| 引用命中用例率 | {_fmt(s['citation_case_hit_rate'])} |",
        f"| 工具选择正确率 | {_fmt(s['tool_selection_accuracy'])} |",
        f"| 无答案识别率 | {_fmt(s['no_answer_accuracy'])} |",
        f"| 工具调用成功率 | {_fmt(s['tool_success_rate'])}（{s['tool_calls']} 次） |",
        f"| 平均工具调用次数 / 用例 | {_fmt(s['avg_tool_calls_per_case'], 2)} |",
        f"| 降级运行 | {s['degraded_runs']}/{s['turns']} 轮 |",
        f"| 首字延迟 P50 / P95 (s) | {_fmt(s['ttft_p50_s'], 2)} / {_fmt(s['ttft_p95_s'], 2)} |",
        f"| 总耗时 P50 / P95 (s) | {_fmt(s['latency_p50_s'], 2)} / {_fmt(s['latency_p95_s'], 2)} |",
        f"| Token（输入 / 输出） | {s['prompt_tokens']} / {s['completion_tokens']} |",
        "",
        "| 用例 | 类别 | 结果 | 失败类型 | 工具调用 | 引用 | 原因 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in result["cases"]:
        tools = ", ".join(c["name"] for t in r["turns"] for c in t["tool_calls"]) or "—"
        cites = f"{r['citations_correct']}/{r['citations_total']}"
        reasons = "；".join(r["reasons"]).replace("|", "/") or "—"
        lines.append(
            f"| {r['id']} | {r['category']} | {'通过' if r['passed'] else '失败'} | "
            f"{r['failure_type'] or '—'} | {tools} | {cites} | {reasons} |"
        )
    return "\n".join(lines)


# ---- entry point ----


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KnowledgePilot evaluation runner")
    parser.add_argument("--mode", choices=("retrieval", "agent"), required=True)
    parser.add_argument("--retriever", choices=(*STRATEGIES, "all"), default="hybrid")
    parser.add_argument(
        "--env-file", type=Path, help="backend .env with model/embedding settings (never printed)"
    )
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--only", help="comma-separated case ids to run")
    parser.add_argument("--top-k", type=int, default=10, help="retrieval mode depth (≤ 20)")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)

    if args.env_file is not None and not args.env_file.is_file():
        parser.error(f"--env-file not found: {args.env_file}")
    cases = load_cases(args.cases)
    if args.only:
        wanted = set(args.only.split(","))
        cases = [case for case in cases if case["id"] in wanted]
    strategies = list(STRATEGIES) if args.retriever == "all" else [args.retriever]
    started = datetime.now()
    if args.mode == "retrieval":
        result = run_retrieval(args.env_file, strategies, cases, args.top_k)
        markdown = retrieval_markdown(result)
    else:
        if args.env_file is None:
            parser.error("--mode agent needs --env-file")
        if args.retriever == "all":
            parser.error("--mode agent runs one strategy at a time")
        result = run_agent(args.env_file, args.retriever, cases)
        markdown = agent_markdown(result, args.retriever)
    payload = {
        "mode": args.mode,
        "retriever": args.retriever,
        "started_at": started.isoformat(timespec="seconds"),
        "duration_s": round((datetime.now() - started).total_seconds(), 1),
        "case_ids": [case["id"] for case in cases],
        **result,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{started:%Y%m%d-%H%M%S}-{args.mode}-{args.retriever}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(markdown)
    print(f"\n结果文件：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
