"""The parse worker: one spawned child process that converts one document at a time.

Docling is loaded only in the child, so the web process stays small and a crash or a hang
inside native parsing code cannot take it down: a job that exceeds the timeout is killed
and the next job starts a fresh child. The child entry point is given as "module:function"
(never imported here) so tests can substitute fakes."""

import importlib
import itertools
import logging
import multiprocessing
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.connection import Connection, wait

from app.parsers.base import DoclingConfig, ParseError, ParseRequest, ParseResult

logger = logging.getLogger(__name__)

CRASHED = "解析进程异常退出，请重试"


def _exit_with_parent() -> None:
    """A daemon child is only reaped on a clean parent exit; if the web process is killed
    while a long conversion runs, stop here instead of finishing work nobody will read."""
    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def watch():
        wait([parent.sentinel])
        os._exit(1)

    threading.Thread(target=watch, name="parse-parent-watchdog", daemon=True).start()


def _entry(target: str, conn: Connection, config: DoclingConfig) -> None:
    _exit_with_parent()
    module, function = target.split(":")
    getattr(importlib.import_module(module), function)(conn, config)


class ParserWorker:
    def __init__(self, target: str, config: DoclingConfig, timeout: float):
        self.target = target
        self.config = config
        self.timeout = timeout
        self.process: multiprocessing.process.BaseProcess | None = None
        self._conn: Connection | None = None
        self._lock = threading.Lock()
        self._jobs = itertools.count(1)

    def lock(self) -> threading.Lock:
        """Callers hold this from claiming a document until its result is stored, so queued
        documents stay queued while another one is being parsed."""
        return self._lock

    def _start(self) -> Connection:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        self.process = context.Process(
            target=_entry, args=(self.target, child, self.config), daemon=True
        )
        self.process.start()
        child.close()
        self._conn = parent
        return parent

    def _connection(self) -> Connection:
        if self._conn is None or self.process is None or not self.process.is_alive():
            self._discard()
            return self._start()
        return self._conn

    def _discard(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _kill(self) -> None:
        process = self.process
        if process is not None and process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
        self._discard()

    def _crashed(self) -> ParseError:
        process = self.process
        if process is not None:
            process.join(5)
        logger.error(
            "Parse worker exited unexpectedly (exit code %s)",
            process.exitcode if process is not None else None,
        )
        self._kill()
        return ParseError(CRASHED)

    def run(self, request: ParseRequest) -> ParseResult:
        conn = self._connection()
        job_id = next(self._jobs)
        try:
            conn.send(("parse", job_id, request))
            if not conn.poll(self.timeout):
                self._kill()
                raise ParseError(f"解析超时（超过 {int(self.timeout)} 秒），可稍后重试")
            answer_id, ok, payload = conn.recv()
        except (EOFError, BrokenPipeError, ConnectionResetError):
            raise self._crashed() from None
        if answer_id != job_id:
            # Never expected with one job in flight; restart rather than mix up results.
            logger.error("Parse worker answered job %s, expected %s", answer_id, job_id)
            self._kill()
            raise ParseError(CRASHED)
        if not ok:
            raise ParseError(payload)
        return payload

    def close(self) -> None:
        process, conn = self.process, self._conn
        if process is not None and process.is_alive() and conn is not None:
            try:
                conn.send(("stop",))
            except (BrokenPipeError, OSError):
                logger.warning("Parse worker pipe already closed at shutdown")
            process.join(5)
        self._kill()


class DoclingParser:
    """Parser for PDF/DOCX that delegates to the shared parse worker. `executor` is the single
    queue thread that ingestion jobs for these formats run on (see ingestion.schedule)."""

    def __init__(self, worker: ParserWorker):
        self.worker = worker
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parse-queue")

    def exclusive(self) -> threading.Lock:
        return self.worker.lock()

    def parse(self, request: ParseRequest) -> ParseResult:
        return self.worker.run(request)

    def close(self) -> None:
        # Documents still waiting stay `queued` and are marked failed at the next start.
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.worker.close()
