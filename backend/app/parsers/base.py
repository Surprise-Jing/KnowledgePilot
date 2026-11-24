"""Parser interface shared by the web process and the parse worker. Everything here is
plain picklable data so results can cross the worker's process boundary."""

import logging
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.schemas import LocationKind, ParseMode

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

# Upper bound per chunk so a table spanning many pages cannot bloat citation snapshots.
MAX_ELEMENTS = 64


@dataclass(frozen=True)
class DoclingConfig:
    """The subset of Settings the parse worker needs, as picklable data."""

    artifacts_path: str
    device: str
    threads: int
    ocr_engine: str
    force_full_page_ocr: bool
    tokenizer: str
    max_tokens: int
    log_level: str = "INFO"

    @classmethod
    def from_settings(cls, settings: "Settings") -> "DoclingConfig":
        return cls(
            artifacts_path=settings.docling_artifacts_path,
            device=settings.docling_device,
            threads=settings.docling_threads,
            ocr_engine=settings.docling_ocr_engine,
            force_full_page_ocr=settings.docling_force_full_page_ocr,
            tokenizer=settings.chunk_tokenizer,
            max_tokens=settings.chunk_max_tokens,
            log_level=settings.log_level,
        )


class ParseError(Exception):
    """A parse failure with a user-facing (Chinese) message, stored as the document error."""


@dataclass(frozen=True)
class ParseRequest:
    path: Path
    file_type: str
    mode: ParseMode
    # Staging directory for document.md / document.json; the caller commits or discards it.
    output_dir: Path


@dataclass(frozen=True)
class SourceElementData:
    ref: str
    label: str
    page: int | None
    # Normalized [x0, y0, x1, y1], top-left origin, 0–1.
    bbox: tuple[float, float, float, float] | None


@dataclass(frozen=True)
class ParsedChunk:
    content: str
    kind: LocationKind
    start: int
    end: int
    table: int | None = None
    heading_path: tuple[str, ...] = ()
    char_start: int | None = None
    char_end: int | None = None
    elements: tuple[SourceElementData, ...] = ()


@dataclass(frozen=True)
class ParseResult:
    chunks: list[ParsedChunk]
    parser: str
    page_count: int | None
    has_markdown: bool
    has_structure: bool


class Parser(Protocol):
    def parse(self, request: ParseRequest) -> ParseResult: ...

    def exclusive(self) -> AbstractContextManager[None]:
        """Held while a document moves from queued to parsed; serializes heavy parsers."""
        ...


def limit_elements(items: Iterable[SourceElementData]) -> tuple[SourceElementData, ...]:
    """Drop duplicate (ref, page) pairs, keeping order, and cap at MAX_ELEMENTS."""
    seen: set[tuple[str, int | None]] = set()
    kept: list[SourceElementData] = []
    dropped = 0
    for item in items:
        key = (item.ref, item.page)
        if key in seen:
            continue
        seen.add(key)
        if len(kept) < MAX_ELEMENTS:
            kept.append(item)
        else:
            dropped += 1
    if dropped:
        logger.warning("Chunk source elements capped at %s; dropped %s", MAX_ELEMENTS, dropped)
    return tuple(kept)
