"""Tool runtime context (docs/contracts.md §5.1). Owner: B."""

import threading
from dataclasses import dataclass, field

from app.config import Settings
from app.db import Database


@dataclass(frozen=True)
class CitationSource:
    """Snapshot of a chunk as a tool returned it, so a citation can be stored even if the
    document is reparsed or deleted afterwards."""

    chunk_id: str
    document_id: str
    filename: str
    location: dict
    content: str
    # Position in the document; known for chunks read with read_document, not for search hits.
    ordinal: int | None = None


class MarkerAllocator:
    """Stable chunk_id -> "c1", "c2"… mapping for one run.

    A lock guards the maps: a tool that hit the Agent's timeout keeps running in its worker
    thread and may still allocate while the next tool runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_chunk: dict[str, str] = {}
        self._sources: dict[str, CitationSource] = {}

    def marker_for(self, chunk_id: str, source: CitationSource | None = None) -> str:
        with self._lock:
            marker = self._by_chunk.get(chunk_id)
            if marker is None:
                marker = f"c{len(self._by_chunk) + 1}"
                self._by_chunk[chunk_id] = marker
            if source is not None:
                self._sources[marker] = source
            return marker

    def source(self, marker: str) -> CitationSource | None:
        with self._lock:
            return self._sources.get(marker)

    def issued(self) -> list[str]:
        with self._lock:
            return list(self._by_chunk.values())

    def sources_for_document(self, document_id: str) -> list[tuple[str, CitationSource]]:
        """(marker, source) of every snippet of `document_id` a tool returned so far, in the
        order the markers were issued. Used by the read_document fallback (contracts §10.2)."""
        with self._lock:
            return [
                (marker, source)
                for marker, source in self._sources.items()
                if source.document_id == document_id
            ]


@dataclass
class ToolContext:
    db: Database
    settings: Settings
    user_id: str
    # Knowledge bases selected for this question; for MCP, all of the user's knowledge bases.
    allowed_kb_ids: list[str]
    run_id: str | None = None
    markers: MarkerAllocator = field(default_factory=MarkerAllocator)
