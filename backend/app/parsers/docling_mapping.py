"""DoclingDocument → ParsedChunk. Pure functions over docling-core types (no conversion
models), so they are unit-tested on hand-built documents. Imported only by the parse worker
process and tests; see app/parsers/__init__.py.

Written against docling-core 2.108 (HybridChunker with `repeat_table_header` and a
serializer provider; semchunk needs `tokenizer.get_tokenizer()` to be callable)."""

import logging
from typing import Literal

from docling_core.transforms.chunker.hierarchical_chunker import (
    ChunkingDocSerializer,
    ChunkingSerializerProvider,
)
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer
from docling_core.transforms.serializer.markdown import MarkdownTableSerializer
from docling_core.types.doc import BoundingBox, DocItem, DoclingDocument, TableItem

from app.parsers.base import ParsedChunk, SourceElementData, limit_elements

logger = logging.getLogger(__name__)


class MarkdownTableProvider(ChunkingSerializerProvider):
    """Serialize tables as Markdown tables instead of the default "row, col = value" triplets:
    financial statements read better for the model and in citations."""

    def get_serializer(self, doc: DoclingDocument) -> ChunkingDocSerializer:
        return ChunkingDocSerializer(doc=doc, table_serializer=MarkdownTableSerializer())


def build_chunker(tokenizer: BaseTokenizer) -> HybridChunker:
    return HybridChunker(
        tokenizer=tokenizer,
        merge_peers=True,
        repeat_table_header=True,
        serializer_provider=MarkdownTableProvider(),
    )


def normalize_bbox(
    bbox: BoundingBox, width: float, height: float
) -> tuple[float, float, float, float] | None:
    """[x0, y0, x1, y1] in 0–1 with a top-left origin; None without a usable page size."""
    if not width or not height or width <= 0 or height <= 0:
        return None
    box = bbox.to_top_left_origin(height)

    def clamp(value: float) -> float:
        return round(min(max(value, 0.0), 1.0), 4)

    x0, x1 = sorted((box.l / width, box.r / width))
    y0, y1 = sorted((box.t / height, box.b / height))
    return clamp(x0), clamp(y0), clamp(x1), clamp(y1)


def _page_size(doc: DoclingDocument, page_no: int) -> tuple[float, float]:
    page = doc.pages.get(page_no)
    if page is None or page.size is None:
        return 0.0, 0.0
    return page.size.width, page.size.height


def _elements(doc: DoclingDocument, items: list[DocItem]) -> list[SourceElementData]:
    elements: list[SourceElementData] = []
    for item in items:
        if not item.prov:
            elements.append(SourceElementData(item.self_ref, item.label.value, None, None))
            continue
        for prov in item.prov:
            width, height = _page_size(doc, prov.page_no)
            bbox = normalize_bbox(prov.bbox, width, height) if prov.bbox is not None else None
            elements.append(SourceElementData(item.self_ref, item.label.value, prov.page_no, bbox))
    return elements


def _first_table_ref(items: list[DocItem], table_numbers: dict[str, int]) -> str | None:
    # Match by reference, not by class: chunks merged by HybridChunker can carry table items
    # that are not TableItem instances.
    return next((item.self_ref for item in items if item.self_ref in table_numbers), None)


def chunks_from_document(
    doc: DoclingDocument, chunker: HybridChunker, file_type: Literal["pdf", "docx"]
) -> list[ParsedChunk]:
    table_numbers = {table.self_ref: number for number, table in enumerate(doc.tables, 1)}
    # DOCX has no pages: number text elements (everything except tables) in reading order.
    text_numbers: dict[str, int] = {}
    if file_type == "docx":
        for item, _level in doc.iterate_items():
            if (
                isinstance(item, DocItem)
                and not isinstance(item, TableItem)
                and item.self_ref not in table_numbers
            ):
                text_numbers[item.self_ref] = len(text_numbers) + 1

    chunks: list[ParsedChunk] = []
    previous_pages = (1, 1)
    missing_prov = 0
    for chunk in chunker.chunk(doc):
        items = [item for item in chunk.meta.doc_items if isinstance(item, DocItem)]
        elements = _elements(doc, items)
        table_ref = _first_table_ref(items, table_numbers)
        table_number = table_numbers.get(table_ref) if table_ref is not None else None
        heading_path = tuple(chunk.meta.headings or ())
        if file_type == "pdf":
            pages = [e.page for e in elements if e.page is not None]
            if pages:
                previous_pages = (min(pages), max(pages))
            else:
                missing_prov += 1
            start, end = previous_pages
            kind = "pdf_page"
        else:
            numbers = [text_numbers[i.self_ref] for i in items if i.self_ref in text_numbers]
            if numbers:
                kind, start, end, table_number = "docx_paragraphs", min(numbers), max(numbers), None
            elif table_number is not None:
                rows = doc.tables[table_number - 1].data.num_rows
                kind, start, end = "docx_table_rows", 1, max(rows, 1)
            else:
                continue
        chunks.append(
            ParsedChunk(
                content=chunk.text,
                kind=kind,
                start=start,
                end=end,
                table=table_number,
                heading_path=heading_path,
                elements=limit_elements(elements),
            )
        )
    if missing_prov:
        logger.warning("PDF chunks without provenance reused previous pages: %s", missing_prov)
    return chunks
