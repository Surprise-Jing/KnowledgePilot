"""Offline parser doubles. `LegacyPdfDocxParser` reproduces the pre-Docling pypdf /
python-docx behaviour (one chunk group per PDF page, DOCX paragraphs grouped by heading
section, DOCX table rows grouped per table) so the gate tests and the evaluation corpus keep
their page / paragraph annotations without loading Docling models. Both libraries are dev
dependencies; product code never uses them."""

import re
from contextlib import nullcontext
from dataclasses import dataclass

from docx import Document
from pypdf import PdfReader

from app.parsers.base import ParsedChunk, ParseError, ParseRequest, ParseResult
from app.parsers.registry import ParserRegistry
from app.parsers.text import MAX_CHUNK_CHARS, SPLIT_OVERLAP, TextParser


@dataclass(frozen=True)
class _Block:
    text: str
    kind: str
    index: int
    group: tuple
    table: int | None = None
    heading_path: tuple[str, ...] = ()


def _heading_level(paragraph) -> int | None:
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    if name.lower() == "title":
        return 1
    match = re.fullmatch(r"(?:heading|标题)\s*(\d)", name.strip(), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _blocks(request: ParseRequest):
    if request.file_type == "pdf":
        for number, page in enumerate(PdfReader(request.path).pages, 1):
            text = (page.extract_text() or "").strip()
            if text:
                yield _Block(text, "pdf_page", number, (number,))
        return
    document = Document(request.path)
    headings: list[str] = []
    for number, paragraph in enumerate(document.paragraphs, 1):
        text = paragraph.text.strip()
        if not text:
            continue
        level = _heading_level(paragraph)
        if level is not None:
            headings = [*headings[: level - 1], text]
        path = tuple(headings)
        yield _Block(text, "docx_paragraphs", number, ("p", path), heading_path=path)
    for table_number, table in enumerate(document.tables, 1):
        for row_number, row in enumerate(table.rows, 1):
            text = " | ".join(cell.text.strip() for cell in row.cells)
            if text.strip(" |"):
                yield _Block(text, "docx_table_rows", row_number, ("t", table_number), table_number)


def _chunks(blocks) -> list[ParsedChunk]:
    chunks: list[ParsedChunk] = []
    buffer: list[_Block] = []

    def flush():
        if buffer:
            first, last = buffer[0], buffer[-1]
            chunks.append(
                ParsedChunk(
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
                    ParsedChunk(
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


class LegacyPdfDocxParser:
    def exclusive(self):
        return nullcontext()

    def parse(self, request: ParseRequest) -> ParseResult:
        try:
            chunks = _chunks(_blocks(request))
            page_count = len(PdfReader(request.path).pages) if request.file_type == "pdf" else None
        except Exception as exc:  # mirror the product's generic failure message
            raise ParseError("文档解析失败，请检查文件是否损坏或加密") from exc
        if not chunks:
            raise ParseError("未提取到文本；如为扫描件，请使用增强模式（OCR）重新解析")
        (request.output_dir / "document.md").write_text(
            "\n\n".join(chunk.content for chunk in chunks), encoding="utf-8"
        )
        return ParseResult(
            chunks=chunks,
            parser="legacy-test",
            page_count=page_count,
            has_markdown=True,
            has_structure=False,
        )


def offline_registry() -> ParserRegistry:
    legacy = LegacyPdfDocxParser()
    return ParserRegistry({"txt": TextParser(), "pdf": legacy, "docx": legacy})
