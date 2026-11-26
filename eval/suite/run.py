"""Suite v2 runner: validate, build an ingest-once snapshot, then run retrieval or agent mode.

Usage (from the repository root):

    uv run --directory backend python ../eval/suite/run.py --verify-sources
    uv run --directory backend python ../eval/suite/run.py --fetch --pin
    uv run --directory backend python ../eval/suite/run.py --mode snapshot --env-file backend/.env
    uv run --directory backend python ../eval/suite/run.py --mode retrieval --retriever hybrid \\
        --env-file backend/.env --split dev
    uv run --directory backend python ../eval/suite/run.py --mode agent --retriever hybrid \\
        --env-file backend/.env --split dev --repeat 3

Every document is parsed once into a snapshot data directory keyed by the dataset fingerprint
and the parse/embedding settings; each run copies it to a fresh temporary directory. Groups
without settings overrides or document deletions share one copy and one app; the others get
their own. Results go to eval/results/suite/ (git-ignored) and contain no keys or URLs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

import run as legacy

from suite import dataset, scoring, sources

RESULTS = EVAL_DIR / "results/suite"
SNAPSHOTS = RESULTS / "snapshots"
DEFAULT_PARSE_TIMEOUT = {"KB2": 3600, "KB6": 3600}
DEFAULT_PARSE_TIMEOUT_OTHER = 900
# Settings that change what ingestion writes; secrets and service URLs never enter the key.
_SNAPSHOT_FIELDS = (
    "embedding_model",
    "embedding_dimensions",
    "embedding_revision",
    "chunk_tokenizer",
    "chunk_max_tokens",
)


@dataclass
class SuiteIds:
    knowledge_bases: dict[str, str] = field(default_factory=dict)  # name → product id
    documents: dict[str, str] = field(default_factory=dict)  # suite doc id → product id

    def kb_ids(self, names: list[str]) -> list[str]:
        return [self.knowledge_bases[name] for name in names]


def snapshot_key(suite_fingerprint: str, settings) -> str:
    values = {name: getattr(settings, name) for name in _SNAPSHOT_FIELDS}
    values.update(
        {
            name: value
            for name, value in settings.model_dump().items()
            if name.startswith("docling_")
        }
    )
    values["embedding_enabled"] = bool(settings.embedding_base_url)
    payload = json.dumps({"suite": suite_fingerprint, **values}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def wait_parsed(
    client, document_ids: dict[str, str], timeout_s: int, poll_s: float = 1.0, retries: int = 3
) -> None:
    """Wait until every document is parsed. A failed document is retried through the product's
    own retry endpoint up to `retries` times: the embedding provider fails sporadically, and one
    blip would otherwise abort a snapshot of hundreds of documents."""
    deadline = time.monotonic() + timeout_s
    attempts: dict[str, int] = {}
    while True:
        statuses = {
            key: legacy.expect_status(client.get(f"/api/documents/{value}"), 200)
            for key, value in document_ids.items()
        }
        failed = [key for key, item in statuses.items() if item["status"] == "failed"]
        exhausted = [key for key in failed if attempts.get(key, 0) >= retries]
        if exhausted:
            raise RuntimeError(f"文档解析失败：{exhausted}（查看后端解析日志）")
        for key in failed:
            attempts[key] = attempts.get(key, 0) + 1
            print(
                f"[suite] {key} 解析失败（{statuses[key].get('error')}），"
                f"第 {attempts[key]}/{retries} 次重试",
                file=sys.stderr,
                flush=True,
            )
            response = client.post(f"/api/documents/{document_ids[key]}/retry")
            legacy.expect_status(response, 202)
        if failed:
            time.sleep(poll_s)
            continue
        pending = [key for key, item in statuses.items() if item["status"] != "success"]
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"解析超时：仍有 {len(pending)} 份文档未完成（{pending[:5]}）")
        time.sleep(poll_s)


def _upload_all(client, suite: dataset.Suite, cache: Path, parse_timeout: dict) -> SuiteIds:
    ids = SuiteIds()
    folders: dict[tuple[str, tuple[str, ...]], str] = {}
    for kb_id, manifest in suite.manifests.items():
        uploaded: dict[str, str] = {}
        for doc in manifest["documents"]:
            name = doc["knowledge_base"]
            if name not in ids.knowledge_bases:
                response = client.post("/api/knowledge-bases", json={"name": name})
                ids.knowledge_bases[name] = legacy.expect_status(response, 201)["id"]
            kb = ids.knowledge_bases[name]
            parent = None
            for depth in range(1, len(doc["folder"]) + 1):
                key = (name, tuple(doc["folder"][:depth]))
                if key not in folders:
                    response = client.post(
                        f"/api/knowledge-bases/{kb}/folders",
                        json={"name": key[1][-1], "parent_id": parent},
                    )
                    folders[key] = legacy.expect_status(response, 201)["id"]
                parent = folders[key]
            payload = sources.source_path(suite.documents[doc["id"]], cache).read_bytes()
            response = client.post(
                f"/api/knowledge-bases/{kb}/documents",
                files={"file": (doc["filename"], payload)},
                data={"folder_id": parent or ""},
            )
            uploaded[doc["id"]] = legacy.expect_status(response, 202)["id"]
        timeout = parse_timeout.get(kb_id, DEFAULT_PARSE_TIMEOUT_OTHER)
        print(f"[suite] {kb_id}: {len(uploaded)} 份文档等待解析", file=sys.stderr, flush=True)
        wait_parsed(client, uploaded, timeout)
        ids.documents.update(uploaded)
    return ids


def build_snapshot(
    suite: dataset.Suite, settings_factory, cache: Path, out_dir: Path, parse_timeout: dict
) -> Path:
    """Ingest every document through the real HTTP API into `out_dir/data`."""
    from fastapi.testclient import TestClient

    create_app = legacy.load_create_app()
    building = out_dir.with_name(out_dir.name + ".building")
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir(parents=True)
    with TestClient(create_app(settings_factory(building / "data"))) as client:
        legacy.authorize(client, client.app.state.db)
        ids = _upload_all(client, suite, cache, parse_timeout)
    (building / "ids.json").write_text(json.dumps(asdict(ids), ensure_ascii=False), "utf-8")
    shutil.rmtree(out_dir, ignore_errors=True)
    building.replace(out_dir)
    return out_dir


def copy_snapshot(snapshot: Path, dest: Path) -> SuiteIds:
    shutil.copytree(snapshot / "data", dest, dirs_exist_ok=True)
    return SuiteIds(**json.loads((snapshot / "ids.json").read_text("utf-8")))


# ---- retrieval mode ----


def _relevance(items: list[dict], hit: dict, documents: dict) -> bool:
    return any(scoring.evidence_matches(item, hit, documents) for item in items)


def retrieval_rows(
    client, ids: SuiteIds, suite: dataset.Suite, groups: list[dict], top_k: int,
    extra_queries: list[dict] = (),
) -> list[dict]:  # fmt: skip
    """Fixed baseline: the raw final user turn, no rewriting, decomposition or retries."""
    queries = []
    for group in groups:
        for case in group["cases"]:
            evidence_groups = [
                [suite.evidence[key] for key in alternatives]
                for atom in case["claims"]
                for alternatives in atom["evidence_groups"]
            ]
            unique = list({json.dumps(g, sort_keys=True): g for g in evidence_groups}.values())
            if unique:
                queries.append(
                    (group["id"], case["id"], case["turns"][-1]["content"],
                     dataset.scope(group, case), unique)
                )  # fmt: skip
    for item in extra_queries:
        items = [
            {"document": s["document"], "locator": {"kind": "txt_lines", "line": s["line"]},
             "anchor": s["anchor"]}
            for s in item["supporting"]
        ]  # fmt: skip
        # Every supporting fact is its own required group (multi-hop needs all of them).
        queries.append(("KB6", item["id"], item["question"], [item["knowledge_base"]],
                        [[evidence] for evidence in items]))  # fmt: skip
    rows = []
    for group_id, case_id, query, kb_names, evidence_groups in queries:
        started = time.perf_counter()
        hits = legacy.search(client, query, ids.kb_ids(kb_names), top_k)
        flat = [item for alternatives in evidence_groups for item in alternatives]
        relevance = [int(_relevance(flat, hit, suite.documents)) for hit in hits]
        covered = [
            any(_relevance(alternatives, hit, suite.documents) for hit in hits)
            for alternatives in evidence_groups
        ]
        rows.append(
            {
                "group": group_id,
                "id": case_id,
                "evidence_groups": len(evidence_groups),
                "covered_groups": sum(covered),
                "all_evidence_retrieved": all(covered),
                "first_relevant_rank": relevance.index(1) + 1 if 1 in relevance else None,
                "relevance": relevance,
                "latency_s": round(time.perf_counter() - started, 4),
            }
        )
    return rows


# ---- agent mode ----


def agent_group(client, admin, ids: SuiteIds, group: dict, suite: dataset.Suite) -> dict:
    cases = []
    for case in group["cases"]:
        conversation = legacy.expect_status(
            client.post("/api/conversations", json={"title": case["id"]}),
            201,
        )["id"]
        turns = []
        for question in case["turns"]:
            for action in question.get("before", []):
                document = ids.documents[action["document"]]
                response = admin.delete(f"/api/documents/{document}")
                legacy.expect_status(response, 204)
            events, ttft, duration = legacy.stream_turn(
                client,
                conversation,
                question["content"],
                ids.kb_ids(dataset.scope(group, case)),
            )
            turn = legacy.summarize_turn(events)
            # Keep the cited text: anchor-level checks need it (summarize_turn drops it).
            turn["citations"] = [payload for name, payload in events if name == "citation"]
            turn.update(question=question["content"], ttft_s=ttft, latency_s=duration)
            turns.append(turn)
        verdict = scoring.judge(group, case, turns, suite.evidence, suite.documents)
        cases.append({"id": case["id"], **verdict, "turns": turns})
    return {"id": group["id"], "cases": cases}


def plan_instances(groups: list[dict]) -> list[tuple[dict, list[dict]]]:
    """Groups that neither override settings nor delete documents share one app instance."""

    def isolated(group: dict) -> bool:
        deletes = any(turn.get("before") for case in group["cases"] for turn in case["turns"])
        return bool(group.get("overrides")) or deletes

    shared = [group for group in groups if not isolated(group)]
    plans = [({}, shared)] if shared else []
    return plans + [(group.get("overrides") or {}, [group]) for group in groups if isolated(group)]


def describe_settings(settings) -> dict:
    """Run metadata without secrets or service URLs."""
    return {
        "retriever": settings.retriever,
        "model": settings.llm_model or None,
        "embedding_model": settings.embedding_model if settings.embedding_base_url else None,
        "agent_max_steps": settings.agent_max_steps,
        "context_budget_chars": settings.context_budget_chars,
    }


def ensure_snapshot(args, suite) -> tuple[Path, str]:
    probe = legacy.make_settings(args.env_file, Path(tempfile.gettempdir()) / "kp-probe")
    key = snapshot_key(dataset.fingerprint(), probe)
    path = SNAPSHOTS / key
    if not (path / "ids.json").exists():
        # Ingest with hybrid so vectors exist for every strategy evaluated later.
        build_snapshot(
            suite,
            lambda data: legacy.make_settings(args.env_file, data, retriever="hybrid"),
            args.cache,
            path,
            args.parse_timeout,
        )
    return path, key


def load_extra_queries(suite) -> list[dict]:
    path = suite.root / "kbs/KB6/questions.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    name = suite.manifests["KB6"]["documents"][0]["knowledge_base"]
    return [{**row, "knowledge_base": name} for row in rows if row["answerable"]]


def evaluate(args, suite, groups) -> dict:
    from fastapi.testclient import TestClient

    create_app = legacy.load_create_app()
    snapshot, key = ensure_snapshot(args, suite)
    result = {"snapshot_key": key, "groups": []}
    if args.mode == "retrieval":
        with tempfile.TemporaryDirectory(prefix="kp-suite-") as tmp:
            ids = copy_snapshot(snapshot, Path(tmp))
            settings = legacy.make_settings(args.env_file, Path(tmp), retriever=args.retriever)
            with TestClient(create_app(settings)) as client:
                legacy.authorize(client, client.app.state.db)
                extra = load_extra_queries(suite) if not args.groups else []
                result["rows"] = retrieval_rows(client, ids, suite, groups, args.top_k, extra)
            result["settings"] = describe_settings(settings)
        return result
    for repeat_index in range(args.repeat):
        for overrides, members in plan_instances(groups):
            with tempfile.TemporaryDirectory(prefix="kp-suite-") as tmp:
                ids = copy_snapshot(snapshot, Path(tmp))
                settings = legacy.make_settings(
                    args.env_file, Path(tmp), retriever=args.retriever, **overrides
                )
                if not settings.llm_model:
                    raise ValueError("Agent 评测需要配置真实模型；请提供 --env-file")
                app = create_app(settings)
                with (
                    legacy.LiveServer(app) as server,
                    httpx.Client(
                        base_url=server.base_url, timeout=legacy.REQUEST_TIMEOUT_SECONDS
                    ) as client,
                ):
                    legacy.authorize(client, app.state.db)
                    for group in members:
                        print(f"[suite] r{repeat_index + 1} {group['id']}", file=sys.stderr)
                        row = agent_group(client, client, ids, group, suite)
                        result["groups"].append({**row, "repeat_index": repeat_index})
                result.setdefault("settings", describe_settings(settings))
    return result


def _parse_timeout(value: str) -> dict:
    return {**DEFAULT_PARSE_TIMEOUT, **json.loads(value)} if value else DEFAULT_PARSE_TIMEOUT


def write_pins(suite, pinned: dict[str, dict]) -> None:
    for kb_id, manifest in suite.manifests.items():
        changed = False
        for doc in manifest["documents"]:
            if doc["id"] in pinned:
                doc.update(pinned[doc["id"]])
                changed = True
        if changed:
            path = suite.root / "kbs" / kb_id / "manifest.json"
            path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("validate", "snapshot", "retrieval", "agent"), default="validate"
    )
    parser.add_argument("--fetch", action="store_true", help="download missing originals")
    parser.add_argument("--pin", action="store_true", help="record hashes left empty")
    parser.add_argument("--verify-sources", action="store_true")
    parser.add_argument(
        "--export-materials",
        type=Path,
        nargs="?",
        const=RESULTS / "materials",
        help="copy originals into <dir>/<knowledge base>/<folder> for manual upload",
    )
    parser.add_argument("--cache", type=Path, default=dataset.CACHE)
    parser.add_argument("--groups", help="comma-separated group IDs")
    parser.add_argument("--split", choices=("all", "dev", "test"), default="all")
    parser.add_argument("--compare-only", action="store_true")
    parser.add_argument("--retriever", choices=legacy.STRATEGIES, default="hybrid")
    parser.add_argument("--top-k", type=int, choices=range(1, 21), default=10)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--out", type=Path, default=RESULTS)
    parser.add_argument("--parse-timeout", type=_parse_timeout, default=DEFAULT_PARSE_TIMEOUT)
    args = parser.parse_args(argv)
    if args.env_file is not None:
        if not args.env_file.is_file() and not args.env_file.is_absolute():
            args.env_file = dataset.REPO / args.env_file
        if not args.env_file.is_file():
            parser.error("指定的模型配置文件不存在")
    suite = dataset.load()
    if args.fetch:
        pinned = sources.fetch(suite, args.cache, pin=args.pin)
        write_pins(suite, pinned)
        suite = dataset.load()
    stats = dataset.validate(suite)
    if args.verify_sources or args.mode != "validate":
        stats.update(sources.verify(suite, args.cache))
    if args.export_materials:
        counts = sources.export_materials(suite, args.export_materials, args.cache)
        print(json.dumps({"exported_to": str(args.export_materials), **counts}, ensure_ascii=False))
    groups = suite.groups
    if args.groups:
        wanted = set(args.groups.split(","))
        unknown = wanted - {group["id"] for group in groups}
        if unknown:
            parser.error(f"未知组：{sorted(unknown)}")
        groups = [group for group in groups if group["id"] in wanted]
    groups = [g for g in groups if args.split == "all" or g["split"] == args.split]
    groups = [g for g in groups if not args.compare_only or g.get("compare")]
    if args.mode == "validate":
        cov = dataset.coverage(suite)
        print(json.dumps({**stats, "coverage_problems": dataset.check_coverage(cov)},
                         ensure_ascii=False))  # fmt: skip
        return 0
    if args.mode == "snapshot":
        path, key = ensure_snapshot(args, suite)
        print(f"快照：{path}（{key}）")
        return 0
    started = datetime.now(UTC)
    result = evaluate(args, suite, groups)
    result.update(
        dataset_sha256=dataset.fingerprint(),
        mode=args.mode,
        split=args.split,
        repeat=args.repeat,
        started_at=started.isoformat(),
        duration_s=round((datetime.now(UTC) - started).total_seconds(), 2),
    )
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{started:%Y%m%dT%H%M%SZ}-{args.mode}-{args.retriever}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"结果文件：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
