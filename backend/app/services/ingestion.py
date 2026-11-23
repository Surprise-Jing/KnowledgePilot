import json
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from docx import Document
from pypdf import PdfReader

from app.db import Database

logger = logging.getLogger(__name__)

MAX_CHUNK_CHARS = 800
SPLIT_OVERLAP = 200
TXT_ENCODINGS = ("utf-8-sig", "gb18030")


@dataclass(frozen=True)
class Block:
    """The smallest addressable unit of a document: a TXT line, a PDF page, a DOCX paragraph
    or a DOCX table row. Consecutive blocks with the same `group` may be merged into one chunk."""

    text: str
    kind: str
    index: int
    group: tuple
    table: int | None = None
    heading_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class Chunk:
    content: str
    kind: str
    start: int
    end: int
    table: int | None = None
    heading_path: tuple[str, ...] = ()
    char_start: int | None = None
    char_end: int | None = None


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in TXT_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文本编码，请转换为 UTF-8 或 GBK 后重新上传")


def heading_level(paragraph) -> int | None:
    """Return the outline level of a DOCX heading paragraph, or None for body text."""
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    if name.lower() == "title":
        return 1
    match = re.fullmatch(r"(?:heading|标题)\s*(\d)", name.strip(), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_blocks(path: Path) -> Iterator[Block]:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        for number, line in enumerate(read_text(path).splitlines(), 1):
            if line.strip():
                yield Block(line.strip(), "txt_lines", number, ())
    elif suffix == ".pdf":
        for number, page in enumerate(PdfReader(path).pages, 1):
            text = (page.extract_text() or "").strip()
            if text:
                yield Block(text, "pdf_page", number, (number,))
    elif suffix == ".docx":
        document = Document(path)
        headings: list[str] = []
        for number, paragraph in enumerate(document.paragraphs, 1):
            text = paragraph.text.strip()
            if not text:
                continue
            level = heading_level(paragraph)
            if level is not None:
                headings = [*headings[: level - 1], text]
            path_ = tuple(headings)
            yield Block(text, "docx_paragraphs", number, ("p", path_), heading_path=path_)
        for table_number, table in enumerate(document.tables, 1):
            for row_number, row in enumerate(table.rows, 1):
                text = " | ".join(cell.text.strip() for cell in row.cells)
                if text.strip(" |"):
                    yield Block(
                        text, "docx_table_rows", row_number, ("t", table_number), table_number
                    )
    else:
        raise ValueError("不支持的文档格式")


def build_chunks(blocks: Iterable[Block]) -> list[Chunk]:
    """Merge consecutive blocks of one group up to MAX_CHUNK_CHARS; split oversized blocks
    into overlapping windows that record their character range."""
    chunks: list[Chunk] = []
    buffer: list[Block] = []

    def flush():
        if buffer:
            first, last = buffer[0], buffer[-1]
            chunks.append(
                Chunk(
                    "\n".join(block.text for block in buffer),
                    first.kind,
                    first.index,
                    last.index,
                    first.table,
                    first.heading_path,
                )
            )
            buffer.clear()

    for block in blocks:
        if len(block.text) > MAX_CHUNK_CHARS:
            flush()
            step = MAX_CHUNK_CHARS - SPLIT_OVERLAP
            for start in range(0, len(block.text), step):
                piece = block.text[start : start + MAX_CHUNK_CHARS]
                chunks.append(
                    Chunk(
                        piece,
                        block.kind,
                        block.index,
                        block.index,
                        block.table,
                        block.heading_path,
                        start,
                        start + len(piece),
                    )
                )
                if start + MAX_CHUNK_CHARS >= len(block.text):
                    break
            continue
        size = sum(len(item.text) + 1 for item in buffer)
        if buffer and (block.group != buffer[0].group or size + len(block.text) > MAX_CHUNK_CHARS):
            flush()
        buffer.append(block)
    flush()
    return chunks


def ingest(db: Database, document_id: str):
    with db.connect() as connection:
        claimed = connection.execute(
            """UPDATE documents SET status='parsing', error=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='queued'""",
            (document_id,),
        ).rowcount
        if not claimed:
            return
        row = connection.execute("SELECT path FROM documents WHERE id=?", (document_id,)).fetchone()
    try:
        chunks = build_chunks(parse_blocks(Path(row["path"])))
        if not chunks:
            raise ValueError("未提取到文本；扫描 PDF 暂不支持 OCR")
        rows = [
            (
                str(uuid4()),
                document_id,
                ordinal,
                chunk.content,
                chunk.kind,
                chunk.start,
                chunk.end,
                chunk.table,
                json.dumps(chunk.heading_path, ensure_ascii=False),
                chunk.char_start,
                chunk.char_end,
            )
            for ordinal, chunk in enumerate(chunks)
        ]
        with db.connect() as connection:
            # Acquire a write lock before checking existence: deletion cannot race the insert.
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute(
                "SELECT 1 FROM documents WHERE id=?", (document_id,)
            ).fetchone():
                return
            connection.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            connection.executemany(
                """INSERT INTO chunks
                (id, document_id, ordinal, content, loc_kind, loc_start, loc_end, loc_table,
                 heading_path, char_start, char_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            connection.execute(
                """UPDATE documents SET status='success', error=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
                (document_id,),
            )
    except Exception as exc:
        logger.exception("Document ingestion failed: %s", document_id)
        reason = (
            str(exc)
            if isinstance(exc, (ValueError, UnicodeError))
            else "文档解析失败，请检查文件是否损坏或加密"
        )
        with db.connect() as connection:
            connection.execute(
                """UPDATE documents SET status='failed', error=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
                (reason[:500], document_id),
            )
