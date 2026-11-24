"""Docling document → ParsedChunk mapping (app/parsers/docling_mapping.py), on hand-built
DoclingDocuments: no conversion models are loaded."""

import pytest
from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)

from app.parsers.docling_mapping import build_chunker, chunks_from_document
from tests.fakes.tokenizer import CharTokenizer

LONG = "跨页段落" * 65  # 260 characters


def prov(page, l, t, r, b):
    return ProvenanceItem(
        page_no=page,
        bbox=BoundingBox(l=l, t=t, r=r, b=b, coord_origin=CoordOrigin.BOTTOMLEFT),
        charspan=(0, 1),
    )


def table_data():
    rows = [["项目", "2024"], ["收入", "100"], ["成本", "60"]]
    cells = [
        TableCell(
            text=text,
            start_row_offset_idx=r,
            end_row_offset_idx=r + 1,
            start_col_offset_idx=c,
            end_col_offset_idx=c + 1,
            column_header=r == 0,
        )
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    return TableData(num_rows=3, num_cols=2, table_cells=cells)


PAGE = Size(width=600, height=800)


def report(with_prov=True, page2=PAGE):
    doc = DoclingDocument(name="report")
    p = prov if with_prov else (lambda *args: None)
    if with_prov:
        doc.add_page(page_no=1, size=Size(width=600, height=800))
        doc.add_page(page_no=2, size=page2)
    doc.add_heading("合并资产负债表", level=1, prov=p(1, 50, 780, 300, 760))
    doc.add_text(
        DocItemLabel.TEXT, "本期营业收入同比增长百分之十二。", prov=p(1, 50, 740, 550, 700)
    )
    doc.add_table(data=table_data(), prov=p(1, 50, 600, 550, 400))
    item = doc.add_text(DocItemLabel.TEXT, LONG, prov=p(1, 50, 100, 550, 40))
    if with_prov:
        item.prov.append(prov(2, 50, 780, 550, 700))
    return doc


def mapped(doc, file_type="pdf"):
    return chunks_from_document(doc, build_chunker(CharTokenizer()), file_type)


def test_pdf_table_chunk():
    [table] = [c for c in mapped(report()) if c.table is not None]
    assert table.kind == "pdf_page"
    assert table.table == 1
    assert (table.start, table.end) == (1, 1)
    assert table.content.startswith("| 项目 | 2024 |")
    assert table.heading_path == ("合并资产负债表",)
    assert table.elements[0].bbox == (0.0833, 0.25, 0.9167, 0.5)
    assert table.elements[0].label == "table"
    assert table.elements[0].ref == "#/tables/0"


def test_pdf_cross_page_text():
    parts = [c for c in mapped(report()) if c.content.startswith("跨页")]
    assert len(parts) >= 2
    for part in parts:
        assert (part.start, part.end) == (1, 2)
        assert {e.page for e in part.elements} == {1, 2}
        assert part.char_start is None and part.char_end is None


@pytest.mark.filterwarnings("ignore:Provenance bbox coordinate:UserWarning")
def test_missing_size_bbox_none():
    parts = [
        c for c in mapped(report(page2=Size(width=0, height=0))) if c.content.startswith("跨页")
    ]
    elements = {e.page: e for e in parts[0].elements}
    assert elements[2].bbox is None
    assert elements[1].bbox is not None


def test_docx_numbering():
    chunks = mapped(report(with_prov=False), "docx")
    text = next(c for c in chunks if c.content.startswith("本期"))
    assert text.kind == "docx_paragraphs"
    assert text.start >= 1
    assert all(e.page is None and e.bbox is None for c in chunks for e in c.elements)
    [table] = [c for c in chunks if c.kind == "docx_table_rows"]
    assert (table.table, table.start, table.end) == (1, 1, 3)


def test_empty_document():
    assert mapped(DoclingDocument(name="empty")) == []


def test_merged_chunk_keeps_table_number():
    """Under a large budget HybridChunker merges a table with neighbouring text; the chunk
    must still carry the table number (seen on a real 91-page annual report)."""
    doc = DoclingDocument(name="merged")
    doc.add_page(page_no=1, size=PAGE)
    doc.add_heading("KEY INDICATORS", level=1, prov=prov(1, 50, 780, 300, 760))
    doc.add_table(data=table_data(), prov=prov(1, 50, 600, 550, 400))
    doc.add_text(DocItemLabel.TEXT, "说明文字。", prov=prov(1, 50, 380, 550, 360))
    doc.add_table(data=table_data(), prov=prov(1, 50, 340, 550, 200))
    chunker = build_chunker(CharTokenizer(max_tokens=2000))
    [chunk] = chunks_from_document(doc, chunker, "pdf")
    assert [e.label for e in chunk.elements] == ["table", "text", "table"]
    assert chunk.table == 1
