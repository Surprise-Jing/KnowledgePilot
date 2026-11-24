"""ingest(): parser selection, staged artifacts, source elements and embedding input."""

import hashlib
import json
import threading
import time
from contextlib import nullcontext

import pytest

from app.db import Database
from app.parsers.base import ParsedChunk, ParseError, ParseResult, SourceElementData
from app.parsers.registry import ParserRegistry
from app.services import artifacts, ingestion
from app.services.embeddings import profile
from tests.fakes.helpers import hybrid_settings

ELEMENT = SourceElementData("#/tables/0", "table", 3, (0.1, 0.2, 0.3, 0.4))
CHUNK = ParsedChunk(
    "收入 100", "pdf_page", 3, 3, table=1, heading_path=("合并资产负债表",), elements=(ELEMENT,)
)


class FakeParser:
    def __init__(self, *, error=None, lock=None, gate=None, on_parse=None):
        self.error, self.lock, self.gate, self.on_parse = error, lock, gate, on_parse

    def exclusive(self):
        return self.lock if self.lock is not None else nullcontext()

    def parse(self, request):
        if self.on_parse:
            self.on_parse(request)
        if self.gate is not None:
            assert self.gate.wait(10)
        if self.error:
            raise self.error
        (request.output_dir / "document.md").write_text("# 报表", encoding="utf-8")
        return ParseResult([CHUNK], "fake 1.0 / standard", 5, True, False)


@pytest.fixture
def settings(tmp_path):
    return hybrid_settings(tmp_path)


@pytest.fixture
def db(settings):
    database = Database(settings.data_dir / "knowledge.db")
    database.migrate()
    with database.connect() as connection:
        connection.execute("INSERT INTO users(id, name) VALUES ('u', 'u')")
        connection.execute("INSERT INTO knowledge_bases(id, user_id, name) VALUES ('k','u','k')")
    return database


@pytest.fixture
def embedded(monkeypatch, settings):
    calls = []

    def fake_embed(_settings, texts):
        calls.append(list(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(ingestion, "embed", fake_embed)
    return calls


def add_document(db, settings, document_id="d1"):
    path = settings.data_dir / f"{document_id}.pdf"
    path.write_bytes(b"%PDF")
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO documents(id, knowledge_base_id, filename, file_type, size_bytes,
            sha256, path, status) VALUES (?, 'k', 'a.pdf', 'pdf', 4, ?, ?, 'queued')""",
            (document_id, document_id, str(path)),
        )
    return document_id


def row(db, document_id):
    with db.connect() as connection:
        return connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()


def registry(parser):
    return ParserRegistry({"pdf": parser})


def leftovers(settings):
    root = artifacts.artifacts_root(settings)
    return sorted(p.name for p in root.iterdir() if ".tmp-" in p.name or ".old-" in p.name)


def test_ingest_success_persists_elements_and_artifacts(db, settings, embedded):
    doc = add_document(db, settings)
    ingestion.ingest(db, doc, settings, registry(FakeParser()))
    document = row(db, doc)
    assert (document["status"], document["parser"], document["page_count"]) == (
        "success",
        "fake 1.0 / standard",
        5,
    )
    with db.connect() as connection:
        chunk = connection.execute("SELECT * FROM chunks WHERE document_id=?", (doc,)).fetchone()
    assert json.loads(chunk["elements"]) == [
        {"ref": "#/tables/0", "label": "table", "page": 3, "bbox": [0.1, 0.2, 0.3, 0.4]}
    ]
    assert chunk["loc_table"] == 1
    final = artifacts.final_dir(artifacts.artifacts_root(settings), doc)
    assert (final / "document.md").read_text(encoding="utf-8") == "# 报表"
    assert leftovers(settings) == []


def test_ingest_embedding_input_has_headings(db, settings, embedded):
    doc = add_document(db, settings)
    ingestion.ingest(db, doc, settings, registry(FakeParser()))
    assert embedded == [["合并资产负债表\n收入 100"]]


def test_ingest_failure_keeps_old_artifacts(db, settings, embedded):
    doc = add_document(db, settings)
    ingestion.ingest(db, doc, settings, registry(FakeParser()))
    with db.connect() as connection:
        connection.execute("UPDATE documents SET status='queued' WHERE id=?", (doc,))
    ingestion.ingest(db, doc, settings, registry(FakeParser(error=ParseError("x"))))
    document = row(db, doc)
    assert (document["status"], document["error"]) == ("failed", "x")
    final = artifacts.final_dir(artifacts.artifacts_root(settings), doc)
    assert (final / "document.md").is_file()
    assert leftovers(settings) == []


def test_second_document_stays_queued_while_first_parses(db, settings, embedded):
    lock, gate = threading.Lock(), threading.Event()
    parser = FakeParser(lock=lock, gate=gate)
    first, second = add_document(db, settings, "d1"), add_document(db, settings, "d2")
    threads = [
        threading.Thread(target=ingestion.ingest, args=(db, doc, settings, registry(parser)))
        for doc in (first, second)
    ]
    threads[0].start()
    deadline = time.monotonic() + 5
    while row(db, first)["status"] != "parsing" and time.monotonic() < deadline:
        time.sleep(0.01)
    threads[1].start()
    time.sleep(0.2)
    assert row(db, first)["status"] == "parsing"
    assert row(db, second)["status"] == "queued"
    gate.set()
    for thread in threads:
        thread.join(10)
    assert row(db, first)["status"] == row(db, second)["status"] == "success"


def test_delete_during_parse_leaves_nothing(db, settings, embedded):
    doc = add_document(db, settings)

    def delete(_request):
        with db.connect() as connection:
            connection.execute("DELETE FROM documents WHERE id=?", (doc,))

    ingestion.ingest(db, doc, settings, registry(FakeParser(on_parse=delete)))
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    root = artifacts.artifacts_root(settings)
    assert not root.exists() or [p for p in root.iterdir() if p.name.startswith(doc)] == []


def test_unexpected_error_uses_generic_message(db, settings, embedded):
    doc = add_document(db, settings)
    ingestion.ingest(db, doc, settings, registry(FakeParser(error=RuntimeError("boom"))))
    document = row(db, doc)
    assert document["status"] == "failed"
    assert document["error"] == "文档解析失败，请检查文件是否损坏或加密"


def test_cleanup_staging(settings):
    root = artifacts.artifacts_root(settings)
    for name in ("x", "x.tmp-1", "x.old-2"):
        (root / name).mkdir(parents=True)
    assert artifacts.cleanup_staging(root) == 2
    assert [p.name for p in root.iterdir()] == ["x"]


def test_profile_includes_text_version(settings):
    identity = [
        settings.embedding_base_url.rstrip("/"),
        settings.embedding_model,
        settings.embedding_dimensions,
        settings.embedding_revision,
    ]
    old = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    assert profile(settings) != old


# ---- scheduling: queued heavy documents must not hold request threads (final review) ----


def test_schedule_runs_heavy_parsers_off_the_caller_thread(db, settings, embedded):
    from concurrent.futures import ThreadPoolExecutor

    gate = threading.Event()
    parser = FakeParser(lock=threading.Lock(), gate=gate)
    parser.executor = ThreadPoolExecutor(max_workers=1)
    docs = [add_document(db, settings, f"d{i}") for i in range(3)]
    started = time.monotonic()
    for doc in docs:
        ingestion.schedule(db, doc, settings, registry(parser))
    assert time.monotonic() - started < 1  # callers (BackgroundTasks threads) return at once
    deadline = time.monotonic() + 5
    while row(db, docs[0])["status"] != "parsing" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [row(db, d)["status"] for d in docs] == ["parsing", "queued", "queued"]
    gate.set()
    parser.executor.shutdown(wait=True)
    assert [row(db, d)["status"] for d in docs] == ["success"] * 3


def test_schedule_runs_light_parsers_inline(db, settings, embedded):
    doc = add_document(db, settings)
    ingestion.schedule(db, doc, settings, registry(FakeParser()))
    assert row(db, doc)["status"] == "success"


# ---- artifact swap vs. delete (final review) ----


def test_artifact_swap_happens_inside_the_write_transaction(db, settings, embedded, monkeypatch):
    import sqlite3

    real_commit = artifacts.commit
    seen = {}

    def checking_commit(staging, final):
        probe = sqlite3.connect(db.path, timeout=0)
        try:
            probe.execute("BEGIN IMMEDIATE")
            seen["locked"] = False
        except sqlite3.OperationalError:
            seen["locked"] = True  # a concurrent delete cannot slip in before the swap
        finally:
            probe.close()
        real_commit(staging, final)

    monkeypatch.setattr(ingestion.artifacts, "commit", checking_commit)
    doc = add_document(db, settings)
    ingestion.ingest(db, doc, settings, registry(FakeParser()))
    assert seen == {"locked": True}
    assert row(db, doc)["status"] == "success"


def test_failed_artifact_swap_rolls_back(db, settings, embedded, monkeypatch):
    doc = add_document(db, settings)
    ingestion.ingest(db, doc, settings, registry(FakeParser()))
    with db.connect() as connection:
        connection.execute("UPDATE documents SET status='queued' WHERE id=?", (doc,))
        before = connection.execute("SELECT id FROM chunks WHERE document_id=?", (doc,)).fetchall()

    def broken(staging, final):
        raise OSError("disk full")

    monkeypatch.setattr(ingestion.artifacts, "commit", broken)
    ingestion.ingest(db, doc, settings, registry(FakeParser()))
    assert row(db, doc)["status"] == "failed"
    with db.connect() as connection:
        after = connection.execute("SELECT id FROM chunks WHERE document_id=?", (doc,)).fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]  # old chunks kept
    final = artifacts.final_dir(artifacts.artifacts_root(settings), doc)
    assert (final / "document.md").is_file()
    assert leftovers(settings) == []


def test_commit_restores_old_directory_when_swap_fails(tmp_path):
    final = tmp_path / "doc"
    final.mkdir()
    (final / "document.md").write_text("old")
    with pytest.raises(OSError):
        artifacts.commit(tmp_path / "missing.tmp-1", final)
    assert (final / "document.md").read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["doc"]
