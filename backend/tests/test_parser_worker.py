"""ParserWorker: the spawned parse process, its timeout, crash handling and shutdown."""

import subprocess
import sys
from pathlib import Path

import pytest

from app.parsers.base import DoclingConfig, ParseError, ParseRequest
from app.parsers.worker import DoclingParser, ParserWorker

CONFIG = DoclingConfig("", "cpu", 1, "rapidocr", True, "BAAI/bge-m3", 512)
BACKEND = Path(__file__).resolve().parents[1]


def request(tmp_path, name="job"):
    output = tmp_path / name
    output.mkdir()
    source = tmp_path / f"{name}.pdf"
    source.write_bytes(b"%PDF")
    return ParseRequest(source, "pdf", "standard", output)


@pytest.fixture
def make_worker():
    workers = []

    def make(target, timeout=10.0):
        worker = ParserWorker(f"tests.fakes.parser_worker:{target}", CONFIG, timeout)
        workers.append(worker)
        return worker

    yield make
    for worker in workers:
        worker.close()


def test_worker_returns_result(make_worker, tmp_path):
    req = request(tmp_path)
    result = make_worker("serve_ok").run(req)
    assert [c.content for c in result.chunks] == ["ok"]
    assert (req.output_dir / "document.md").read_text() == "# ok"


def test_worker_timeout_then_recovers(make_worker, tmp_path):
    worker = make_worker("serve_sleep", timeout=1.0)
    with pytest.raises(ParseError, match="解析超时"):
        worker.run(request(tmp_path, "first"))
    first = worker.process
    assert first is not None and not first.is_alive()
    assert worker.run(request(tmp_path, "second")).parser == "fake"


def test_worker_crash(make_worker, tmp_path):
    worker = make_worker("serve_crash")
    for name in ("a", "b"):
        with pytest.raises(ParseError) as caught:
            worker.run(request(tmp_path, name))
        assert str(caught.value) == "解析进程异常退出，请重试"


def test_worker_error_message(make_worker, tmp_path):
    with pytest.raises(ParseError) as caught:
        make_worker("serve_error").run(request(tmp_path))
    assert str(caught.value) == "测试错误"


def test_worker_close_no_orphans(make_worker, tmp_path):
    worker = make_worker("serve_ok")
    worker.run(request(tmp_path))
    process = worker.process
    worker.close()
    assert not process.is_alive()


def test_docling_parser_delegates_and_serializes(make_worker, tmp_path):
    worker = make_worker("serve_ok")
    parser = DoclingParser(worker)
    assert parser.exclusive() is worker.lock()
    assert parser.parse(request(tmp_path)).parser == "fake"


def test_web_process_does_not_import_docling():
    code = (
        "import sys, app.main; "
        "print(any(m.split('.')[0] in ('docling', 'docling_core') for m in sys.modules))"
    )
    output = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, check=True
    )
    assert output.stdout.strip() == "False"


@pytest.mark.real_parsers
def test_registry_shares_one_lazy_worker(tmp_path):
    from app.config import Settings
    from app.parsers.registry import DOCLING_ENTRY, build_registry
    from app.parsers.text import TextParser

    registry = build_registry(Settings(_env_file=None, data_dir=tmp_path, parse_timeout=7))
    pdf, docx = registry.for_type("pdf"), registry.for_type("docx")
    assert isinstance(registry.for_type("txt"), TextParser)
    assert isinstance(pdf, DoclingParser) and pdf is docx
    assert pdf.worker.target == DOCLING_ENTRY and pdf.worker.timeout == 7
    assert pdf.worker.process is None  # started on the first PDF/DOCX only
    with pytest.raises(ParseError, match="不支持"):
        registry.for_type("xlsx")
    registry.close()


def test_busy_child_exits_when_parent_dies(tmp_path):
    """A child stuck in a long parse must not outlive a crashed web process."""
    import os
    import time

    script = f"""
import os, sys, threading, time
sys.path.insert(0, {str(BACKEND)!r})
from pathlib import Path
from app.parsers.base import DoclingConfig, ParseRequest
from app.parsers.worker import ParserWorker
worker = ParserWorker("tests.fakes.parser_worker:serve_sleep",
                      DoclingConfig("", "cpu", 1, "rapidocr", True, "t", 512), 60)
out = Path({str(tmp_path)!r}) / "job"; out.mkdir()
src = Path({str(tmp_path)!r}) / "a.pdf"; src.write_bytes(b"%PDF")
threading.Thread(target=worker.run, args=(ParseRequest(src, "pdf", "standard", out),),
                 daemon=True).start()
while not (Path({str(tmp_path)!r}) / "slept").exists():
    time.sleep(0.05)
print(worker.process.pid, flush=True)
os._exit(0)
"""
    # Read one line only: the child inherits stdout, so waiting for EOF would wait for it.
    parent = subprocess.Popen(
        [sys.executable, "-c", script], cwd=BACKEND, stdout=subprocess.PIPE, text=True
    )
    child = int(parent.stdout.readline().strip())
    parent.wait(30)
    deadline = time.monotonic() + 5
    alive = True
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            alive = False
            break
        time.sleep(0.1)
    if alive:
        os.kill(child, 9)
    assert not alive
