"""Lightweight TXT parser: UTF-8 / GB18030 decoding, consecutive lines merged into chunks of
at most MAX_CHUNK_CHARS, overlong lines split into overlapping windows."""

from contextlib import nullcontext
from dataclasses import dataclass

from app.parsers.base import (
    ParsedChunk,
    ParseError,
    ParseRequest,
    ParseResult,
    SourceElementData,
    limit_elements,
)

MAX_CHUNK_CHARS = 800
SPLIT_OVERLAP = 200
TXT_ENCODINGS = ("utf-8-sig", "gb18030")


@dataclass(frozen=True)
class _Line:
    number: int
    text: str


def read_text(data: bytes) -> str:
    for encoding in TXT_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseError("无法识别文本编码，请转换为 UTF-8 或 GBK 后重新上传")


def _element(line: _Line) -> SourceElementData:
    return SourceElementData(f"line:{line.number}", "text", None, None)


def build_chunks(lines: list[_Line]) -> list[ParsedChunk]:
    chunks: list[ParsedChunk] = []
    buffer: list[_Line] = []

    def flush():
        if buffer:
            chunks.append(
                ParsedChunk(
                    "\n".join(line.text for line in buffer),
                    "txt_lines",
                    buffer[0].number,
                    buffer[-1].number,
                    elements=limit_elements(_element(line) for line in buffer),
                )
            )
            buffer.clear()

    for line in lines:
        if len(line.text) > MAX_CHUNK_CHARS:
            flush()
            step = MAX_CHUNK_CHARS - SPLIT_OVERLAP
            for start in range(0, len(line.text), step):
                piece = line.text[start : start + MAX_CHUNK_CHARS]
                chunks.append(
                    ParsedChunk(
                        piece,
                        "txt_lines",
                        line.number,
                        line.number,
                        char_start=start,
                        char_end=start + len(piece),
                        elements=(_element(line),),
                    )
                )
                if start + MAX_CHUNK_CHARS >= len(line.text):
                    break
            continue
        size = sum(len(item.text) + 1 for item in buffer)
        if buffer and size + len(line.text) > MAX_CHUNK_CHARS:
            flush()
        buffer.append(line)
    flush()
    return chunks


class TextParser:
    def exclusive(self):
        return nullcontext()

    def parse(self, request: ParseRequest) -> ParseResult:
        text = read_text(request.path.read_bytes())
        (request.output_dir / "document.md").write_text(text, encoding="utf-8")
        lines = [
            _Line(number, line.strip())
            for number, line in enumerate(text.splitlines(), 1)
            if line.strip()
        ]
        return ParseResult(
            chunks=build_chunks(lines),
            parser="text",
            page_count=None,
            has_markdown=True,
            has_structure=False,
        )
