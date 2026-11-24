"""Fake parse-worker entry points for ParserWorker tests. Same signature as
app.parsers.docling_runner.serve; imported by the spawned child via "module:function"."""

import os
import time

from app.parsers.base import ParsedChunk, ParseResult


def _result(request) -> ParseResult:
    (request.output_dir / "document.md").write_text("# ok", encoding="utf-8")
    return ParseResult(
        chunks=[ParsedChunk("ok", "pdf_page", 1, 1)],
        parser="fake",
        page_count=1,
        has_markdown=True,
        has_structure=False,
    )


def _loop(conn, handle):
    while True:
        try:
            message = conn.recv()
        except EOFError:
            return
        if message[0] == "stop":
            return
        _, job_id, request = message
        conn.send(handle(job_id, request))


def serve_ok(conn, config):
    _loop(conn, lambda job_id, request: (job_id, True, _result(request)))


def serve_sleep(conn, config):
    """Hang on the first request ever (marker file next to the output dir), then answer."""

    def handle(job_id, request):
        marker = request.output_dir.parent / "slept"
        if not marker.exists():
            marker.write_text("1")
            time.sleep(30)
        return job_id, True, _result(request)

    _loop(conn, handle)


def serve_crash(conn, config):
    conn.recv()
    os._exit(3)


def serve_error(conn, config):
    _loop(conn, lambda job_id, request: (job_id, False, "测试错误"))
