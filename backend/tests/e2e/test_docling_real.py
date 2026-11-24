"""Real Docling parsing, opt-in: needs the Docling models and the BGE-M3 tokenizer (run
`python -m app.parsers.prefetch` once, online). Enable with PK_RUN_DOCLING_TESTS=1."""

import os
import sys
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document

from app.config import Settings
from app.parsers.base import DoclingConfig, ParseRequest

pytestmark = [
    pytest.mark.real_parsers,
    pytest.mark.skipif(
        os.getenv("PK_RUN_DOCLING_TESTS") != "1", reason="set PK_RUN_DOCLING_TESTS=1"
    ),
]

EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"


@pytest.fixture(scope="module")
def runner():
    from app.parsers.docling_runner import DoclingRunner

    return DoclingRunner(DoclingConfig.from_settings(Settings(_env_file=None)))


def request(tmp_path, name, data, file_type):
    source = tmp_path / name
    source.write_bytes(data)
    output = tmp_path / "out"
    output.mkdir()
    return ParseRequest(source, file_type, "standard", output)


def test_real_pdf(runner, tmp_path):
    sys.path.insert(0, str(EVAL_DIR))
    import build_corpus

    source = (EVAL_DIR / "corpus" / "solar_pilot_report.pdf.txt").read_text(encoding="utf-8")
    req = request(tmp_path, "solar.pdf", build_corpus.render_pdf(source), "pdf")
    result = runner.parse(req)
    assert result.has_markdown and result.has_structure
    assert (req.output_dir / "document.md").read_text(encoding="utf-8").strip()
    assert (req.output_dir / "document.json").is_file()
    assert result.page_count == 6
    assert result.chunks
    for chunk in result.chunks:
        assert chunk.kind == "pdf_page"
        assert any(e.bbox is not None for e in chunk.elements)


def test_real_docx_table(runner, tmp_path):
    document = Document()
    document.add_heading("经营情况", 1)
    document.add_paragraph("本期收入增长。")
    table = document.add_table(rows=2, cols=2)
    for (r, c), text in {(0, 0): "项目", (0, 1): "金额", (1, 0): "收入", (1, 1): "100"}.items():
        table.cell(r, c).text = text
    buffer = BytesIO()
    document.save(buffer)
    result = runner.parse(request(tmp_path, "a.docx", buffer.getvalue(), "docx"))
    # HybridChunker may merge a small table with its neighbouring paragraph; the table stays
    # traceable through the chunk's elements either way.
    [table_chunk] = [c for c in result.chunks if any(e.label == "table" for e in c.elements)]
    assert "项目" in table_chunk.content and "|" in table_chunk.content
    assert table_chunk.heading_path == ("经营情况",)
    assert all(e.page is None and e.bbox is None for e in table_chunk.elements)
    assert result.page_count is None


def large_text_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with 32pt text: small synthetic text is below RapidOCR's detection size."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    body = " ".join(f"({line}) Tj T*" for line in lines)
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 32 Tf 44 TL 60 700 Td {body} ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_real_pdf_enhanced_ocr(runner, tmp_path):
    """Enhanced mode must have a working OCR engine installed (RapidOCR + onnxruntime)."""
    output = tmp_path / "out"
    output.mkdir()
    source = tmp_path / "scan.pdf"
    # Several body lines: a page of only short lines is classified as a heading, and
    # HybridChunker emits no chunk for headings without body text.
    lines = [
        "The pilot plant ran for six",
        "months in the northern site.",
        "Peak output reached 42 kW",
        "during the summer tests and",
        "the inverter stayed stable.",
    ]
    source.write_bytes(large_text_pdf(lines))
    result = runner.parse(ParseRequest(source, "pdf", "enhanced", output))
    assert result.parser.endswith("/ enhanced")
    assert any("42" in chunk.content for chunk in result.chunks)
