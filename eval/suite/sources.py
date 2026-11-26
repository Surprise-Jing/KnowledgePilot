"""Download, pin and verify original public documents. Only explicit CLI actions download;
validation and tests work offline against the local cache."""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

import httpx
from docx import Document
from finance.dataset import normalize
from pypdf import PdfReader

from suite.dataset import CACHE, Suite, doc_format

DOWNLOADED_TYPES = {"original_pdf", "original_docx", "html_body_as_txt"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "table", "section", "article", "h1", "h2", "h3", "h4",
    "h5", "h6", "pre", "blockquote", "dd", "dt",
}  # fmt: skip
_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "noscript", "head"}


def source_path(document: dict, cache: Path = CACHE) -> Path:
    return cache / document["kb_id"] / document["filename"]


class _TextExtractor(HTMLParser):
    """Collect visible text; with `container_class`, only inside the first element carrying
    that class (pages often repeat the article for a mobile layout)."""

    def __init__(self, container_class: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.container_class = container_class
        self.container_tag: str | None = None
        self.container_depth = 0
        self.container_done = False
        self.container_found = False

    def _active(self) -> bool:
        return self.container_class is None or (self.container_depth > 0)

    def handle_starttag(self, tag, attrs):
        if self.container_class and not self.container_done:
            if self.container_depth:
                self.container_depth += tag == self.container_tag
            elif self.container_class in (dict(attrs).get("class") or "").split():
                self.container_tag, self.container_depth = tag, 1
                self.container_found = True
        if not self._active():
            return
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self._active():
            if tag in _SKIP_TAGS:
                self.skip_depth = max(0, self.skip_depth - 1)
            elif tag in _BLOCK_TAGS:
                self.parts.append("\n")
        if self.container_depth and tag == self.container_tag:
            self.container_depth -= 1
            self.container_done = self.container_depth == 0

    def handle_data(self, data):
        if self._active() and not self.skip_depth:
            self.parts.append(data)


def html_to_text(html: str, container_class: str | None = None) -> str:
    """Keep the page's own words; only layout whitespace changes (one block per line)."""
    parser = _TextExtractor(container_class)
    parser.feed(html)
    parser.close()
    if container_class and not parser.container_found:
        raise ValueError(f"网页中找不到正文容器：{container_class}")
    lines = (
        re.sub(r"[ \t\r\f\v\u3000\xa0]+", " ", line).strip()
        for line in "".join(parser.parts).split("\n")
    )
    return "\n".join(line for line in lines if line)


def _counts(document: dict, payload: bytes) -> dict:
    fmt = doc_format(document)
    if fmt == "pdf":
        return {"pages": len(PdfReader(BytesIO(payload)).pages)}
    if fmt == "txt":
        return {"lines": len(payload.decode("utf-8").splitlines())}
    return {}


def _check(document: dict, payload: bytes) -> None:
    name = document["filename"]
    if hashlib.sha256(payload).hexdigest() != document["sha256"]:
        raise ValueError(f"文件哈希与清单不符：{name}")
    if len(payload) != document["bytes"]:
        raise ValueError(f"文件大小与清单不符：{name}")
    for key, value in _counts(document, payload).items():
        if value != document.get(key):
            raise ValueError(f"{'页数' if key == 'pages' else '行数'}与清单不符：{name}")


def _download(client: httpx.Client, url: str) -> bytes:
    response = client.get(url)
    response.raise_for_status()
    return response.content


def fetch(
    suite: Suite, cache: Path = CACHE, client: httpx.Client | None = None, pin: bool = False
) -> dict[str, dict]:
    """Download missing originals and verify them before they are moved into the cache.

    With `pin=True`, a document whose manifest `sha256` is empty gets its hash, size and
    page/line count recorded (returned per document ID for the CLI to write back). A pinned
    hash is never replaced: a changed upstream file fails instead."""
    owned = client is None
    client = client or httpx.Client(timeout=120, follow_redirects=True)
    pinned: dict[str, dict] = {}
    try:
        for document in suite.documents.values():
            path = source_path(document, cache)
            source_type = document["source_type"]
            if source_type not in DOWNLOADED_TYPES:
                if not path.exists():
                    raise ValueError(f"缺少本地构建的文件，请先运行该库的构建脚本：{path}")
                _check(document, path.read_bytes())
                continue
            if path.exists() and document.get("sha256"):
                _check(document, path.read_bytes())
                continue
            if path.exists() and pin and source_type != "html_body_as_txt":
                # A file the user downloaded by hand (e.g. behind a site's bot protection).
                payload = path.read_bytes()
                record = {
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                    **_counts(document, payload),
                }
                document.update(record)
                pinned.setdefault(document["id"], {}).update(record)
                continue
            if source_type == "html_body_as_txt":
                raw = _download(client, document["raw"]["url"])
                raw_sha = hashlib.sha256(raw).hexdigest()
                if pin and not document["raw"].get("sha256"):
                    document["raw"] = {**document["raw"], "sha256": raw_sha}
                    pinned.setdefault(document["id"], {})["raw"] = document["raw"]
                if raw_sha != document["raw"]["sha256"]:
                    raise ValueError(f"原始网页哈希与清单不符：{document['filename']}")
                payload = html_to_text(
                    raw.decode("utf-8"), document["raw"].get("container_class")
                ).encode("utf-8")
            else:
                payload = _download(client, document["url"])
                magic = b"%PDF" if source_type == "original_pdf" else b"PK"
                if not payload.startswith(magic):
                    raise ValueError(f"下载结果格式不符：{document['filename']}")
            if pin and not document.get("sha256"):
                record = {
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                    **_counts(document, payload),
                }
                document.update(record)
                pinned.setdefault(document["id"], {}).update(record)
            _check(document, payload)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".download")
            try:
                temporary.write_bytes(payload)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    finally:
        if owned:
            client.close()
    return pinned


def extract(document: dict, path: Path) -> list[str]:
    """PDF: normalized text per page. TXT: original lines. DOCX: one normalized full text."""
    fmt = doc_format(document)
    if fmt == "pdf":
        return [normalize(page.extract_text() or "") for page in PdfReader(path).pages]
    if fmt == "txt":
        return path.read_text(encoding="utf-8").splitlines()
    docx = Document(str(path))
    texts = [paragraph.text for paragraph in docx.paragraphs]
    texts += [cell.text for table in docx.tables for row in table.rows for cell in row.cells]
    return [normalize("".join(texts))]


def verify(suite: Suite, cache: Path = CACHE) -> dict:
    texts: dict[str, list[str]] = {}
    for document in suite.documents.values():
        path = source_path(document, cache)
        if not path.exists():
            raise ValueError(f"缺少原文：{path}（显式运行 run.py --fetch 下载）")
        _check(document, path.read_bytes())
        texts[document["id"]] = extract(document, path)
    for key, item in suite.evidence.items():
        locator, anchor = item["locator"], normalize(item["anchor"])
        pages = texts[item["document"]]
        if locator["kind"] == "pdf_page":
            found = anchor in pages[locator["page"] - 1]
        elif locator["kind"] == "txt_lines":
            found = anchor in normalize(pages[locator["line"] - 1])
        else:
            found = anchor in pages[0] and (
                "heading" not in locator or normalize(locator["heading"]) in pages[0]
            )
        if not found:
            raise ValueError(f"原文中找不到证据锚点（或标题）：{key}")
    return {"verified_documents": len(texts), "verified_anchors": len(suite.evidence)}


def export_materials(suite: Suite, dest: Path, cache: Path = CACHE) -> dict[str, int]:
    """Copy every verified original into dest/<knowledge base>/<folder...>/<filename>, the
    layout the product's upload page uses, so the corpus can be uploaded by hand."""
    import shutil

    counts: dict[str, int] = {}
    for document in suite.documents.values():
        source = source_path(document, cache)
        if not source.exists():
            raise ValueError(f"缺少原文：{source}（先运行 run.py --fetch）")
        _check(document, source.read_bytes())
        target = dest.joinpath(
            document["knowledge_base"], *document["folder"], document["filename"]
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        counts[document["knowledge_base"]] = counts.get(document["knowledge_base"], 0) + 1
    return counts
