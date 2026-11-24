"""Run the real-document benchmark without modifying product code or the legacy suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

import run as legacy

from finance import dataset, scoring


def setup_corpus(client, manifest: dict, cache: Path) -> legacy.CorpusIds:
    ids = legacy.CorpusIds()
    for doc in manifest["documents"]:
        name = doc["knowledge_base"]
        if name not in ids.knowledge_bases:
            response = client.post(
                "/api/knowledge-bases", headers=legacy.HEADERS, json={"name": name}
            )
            ids.knowledge_bases[name] = legacy.expect_status(response, 201)["id"]
        kb_id = ids.knowledge_bases[name]
        parent = None
        for depth in range(1, len(doc["folder"]) + 1):
            folder_path = tuple(doc["folder"][:depth])
            key = (name, folder_path)
            if key not in ids.folders:
                response = client.post(
                    f"/api/knowledge-bases/{kb_id}/folders",
                    headers=legacy.HEADERS,
                    json={"name": folder_path[-1], "parent_id": parent},
                )
                ids.folders[key] = legacy.expect_status(response, 201)["id"]
            parent = ids.folders[key]
        response = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            headers=legacy.HEADERS,
            files={"file": (doc["filename"], dataset.source_path(doc, cache).read_bytes())},
            data={"folder_id": parent or ""},
        )
        ids.documents[doc["filename"]] = legacy.expect_status(response, 202)["id"]
    deadline = time.monotonic() + legacy.PARSE_TIMEOUT_SECONDS
    while True:
        statuses = [
            legacy.expect_status(client.get(f"/api/documents/{key}", headers=legacy.HEADERS), 200)
            for key in ids.documents.values()
        ]
        if any(item["status"] == "failed" for item in statuses):
            raise RuntimeError("真实报告解析失败，请检查后端解析日志")
        if all(item["status"] == "success" for item in statuses):
            return ids
        if time.monotonic() >= deadline:
            raise RuntimeError("真实报告解析超时")
        time.sleep(0.2)


def required_groups(case: dict) -> list[list[str]]:
    return [
        list(group)
        for group in dict.fromkeys(
            tuple(group) for atom in case["claims"] for group in atom["evidence_groups"]
        )
    ]


def retrieval_case(client, ids, case, evidence, documents, top_k: int) -> dict:
    started = time.perf_counter()
    # Use the actual final user turn, including unresolved references, as the fixed baseline.
    hits = legacy.search(
        client, case["turns"][-1]["content"], ids.kb_ids(case["knowledge_bases"]), top_k
    )
    groups = required_groups(case)
    coverage = [
        any(
            scoring.evidence_matches(evidence[key], hit, documents) for key in group for hit in hits
        )
        for group in groups
    ]
    return {
        "id": case["id"],
        "category": case["category"],
        "evidence_groups": len(groups),
        "covered_groups": sum(coverage),
        "coverage": sum(coverage) / len(groups) if groups else None,
        "all_evidence_retrieved": all(coverage),
        "latency_s": round(time.perf_counter() - started, 4),
        "top": [{"filename": hit["filename"], "location": hit["location"]} for hit in hits],
    }


def agent_case(client, ids, case, evidence, documents) -> dict:
    response = client.post(
        "/api/conversations",
        headers=legacy.HEADERS,
        json={"title": f"finance {case['id']}"},
    )
    conversation_id = legacy.expect_status(response, 201)["id"]
    turns = []
    for question in case["turns"]:
        events, ttft, duration = legacy.stream_turn(
            client,
            conversation_id,
            question["content"],
            ids.kb_ids(case["knowledge_bases"]),
        )
        turn = legacy.summarize_turn(events)
        # Unlike the legacy summary, retain actual cited text for anchor-level checks.
        turn["citations"] = [payload for name, payload in events if name == "citation"]
        turn.update(question=question["content"], ttft_s=ttft, latency_s=duration)
        turns.append(turn)
    return {
        "id": case["id"],
        "category": case["category"],
        **scoring.judge(case, turns[-1], evidence, documents),
        "turns": turns,
        "review": {
            "status": "pending",
            "reviewer": None,
            "rubric": case["semantic_rubric"],
            "reference_answer": case["reference_answer"],
        },
    }


def evaluate(args, manifest, evidence, cases) -> dict:
    documents = {doc["id"]: doc for doc in manifest["documents"]}
    create_app = legacy.load_create_app()
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory(prefix="kp-finance-") as tmp:
        settings = legacy.make_settings(args.env_file, Path(tmp), retriever=args.retriever)
        if args.mode == "agent":
            if not settings.llm_model or not settings.llm_api_key.get_secret_value():
                raise ValueError("Agent 评测需要配置真实模型；请提供 --env-file")
            server_context = legacy.LiveServer(create_app(settings))
        else:
            server_context = TestClient(create_app(settings))
        with server_context as host:
            if args.mode == "agent":
                with httpx.Client(
                    base_url=host.base_url, timeout=legacy.REQUEST_TIMEOUT_SECONDS
                ) as client:
                    ids = setup_corpus(client, manifest, args.cache)
                    rows = []
                    for case in cases:
                        print(f"[finance] {case['id']}", file=sys.stderr, flush=True)
                        rows.append(agent_case(client, ids, case, evidence, documents))
            else:
                ids = setup_corpus(host, manifest, args.cache)
                rows = [
                    retrieval_case(host, ids, case, evidence, documents, args.top_k)
                    for case in cases
                ]
        return {
            "retriever": args.retriever,
            "model": settings.llm_model if args.mode == "agent" else None,
            "agent_max_steps": settings.agent_max_steps,
            "context_budget_chars": settings.context_budget_chars,
            "top_k": args.top_k if args.mode == "retrieval" else None,
            "cases": rows,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("validate", "retrieval", "agent"), default="validate")
    parser.add_argument(
        "--fetch", action="store_true", help="download the four pinned official PDFs"
    )
    parser.add_argument(
        "--verify-sources", action="store_true", help="verify all PDF anchors offline"
    )
    parser.add_argument("--cache", type=Path, default=dataset.CACHE)
    parser.add_argument("--split", choices=("all", "dev", "test"), default="all")
    parser.add_argument("--only", help="comma-separated case IDs")
    parser.add_argument("--retriever", choices=legacy.STRATEGIES, default="cosine")
    parser.add_argument("--top-k", type=int, choices=range(1, 21), default=10)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--out", type=Path, default=EVAL_DIR / "results/finance")
    args = parser.parse_args(argv)
    if args.env_file is not None:
        # `uv run --directory backend` changes cwd; accept the repository-relative paths
        # shown in the README as well as paths relative to the invocation directory.
        if not args.env_file.is_file() and not args.env_file.is_absolute():
            args.env_file = dataset.REPO / args.env_file
        if not args.env_file.is_file():
            parser.error("指定的模型配置文件不存在")
    manifest, evidence, cases = dataset.load()
    stats = dataset.validate(manifest, evidence, cases)
    if args.fetch:
        dataset.fetch_sources(manifest, args.cache)
    if args.verify_sources or args.mode != "validate":
        stats.update(dataset.verify_sources(manifest, evidence, args.cache))
    if args.only:
        wanted = set(args.only.split(","))
        unknown = wanted - {case["id"] for case in cases}
        if unknown:
            parser.error(f"未知用例：{sorted(unknown)}")
        cases = [case for case in cases if case["id"] in wanted]
    cases = [case for case in cases if args.split == "all" or case["split"] == args.split]
    if not cases:
        parser.error("筛选后没有用例")
    if args.mode == "validate":
        print(json.dumps({**stats, "selected_cases": len(cases)}, ensure_ascii=False))
        return 0
    started = datetime.now(UTC)
    result = evaluate(args, manifest, evidence, cases)
    fingerprint = hashlib.sha256(
        b"".join(
            (dataset.ROOT / name).read_bytes()
            for name in ("manifest.json", "evidence.json", "cases.jsonl")
        )
    ).hexdigest()
    result.update(
        dataset_id=manifest["dataset_id"],
        dataset_sha256=fingerprint,
        mode=args.mode,
        split=args.split,
        started_at=started.isoformat(),
        duration_s=round((datetime.now(UTC) - started).total_seconds(), 2),
        semantic_review="pending" if args.mode == "agent" else "not_applicable",
    )
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{started:%Y%m%dT%H%M%SZ}-{args.mode}-{args.retriever}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in result["cases"]:
        print(row["id"], row.get("coverage", row.get("claim_evidence_coverage")))
    print(f"结果文件：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
