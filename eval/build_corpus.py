"""Build the evaluation corpus (TXT copied, DOCX and PDF rendered from text sources).

The corpus is self-written and fictional (see eval/corpus/README.md). Sources:

- `*.txt`              copied as-is.
- `<name>.docx.md`     tiny markup: `# ` heading 1, `## ` heading 2, `| a | b |` table row
                       (consecutive rows form one table), anything else is a body paragraph.
- `<name>.pdf.txt`     one text line per PDF line, pages separated by `=== page ===`.
                       ASCII only: pypdf's built-in Helvetica cannot render CJK text.

Usage (from the repository root):

    uv run --directory backend python ../eval/build_corpus.py --out /tmp/corpus [--describe]

`--describe` parses each built file with the backend's ingestion code and prints every chunk
location, which is what `expect.relevant` in eval/cases.jsonl refers to.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

EVAL_DIR = Path(__file__).resolve().parent
CORPUS_DIR = EVAL_DIR / "corpus"
REPO_ROOT = EVAL_DIR.parent
PAGE_BREAK = "=== page ==="


@dataclass(frozen=True)
class CorpusFile:
    knowledge_base: str
    # Folder path from the knowledge base root; empty for the root.
    folder: tuple[str, ...]
    filename: str
    source: str


# Knowledge base layout used by eval/run.py. "临时库" holds the document that the exception
# case E1 deletes between turns, so deleting it never affects other cases.
CORPUS: tuple[CorpusFile, ...] = (
    CorpusFile("工作资料", ("产品",), "xingtu_user_guide.txt", "xingtu_user_guide.txt"),
    CorpusFile("工作资料", ("制度", "人事"), "qinghe_handbook.docx", "qinghe_handbook.docx.md"),
    CorpusFile("研究资料", (), "remote_work_survey.txt", "remote_work_survey.txt"),
    CorpusFile("研究资料", ("报告",), "solar_pilot_report.pdf", "solar_pilot_report.pdf.txt"),
    CorpusFile("临时库", (), "temp_notice.txt", "temp_notice.txt"),
)

KNOWLEDGE_BASES: tuple[str, ...] = tuple(dict.fromkeys(item.knowledge_base for item in CORPUS))


def render_docx(source: str) -> bytes:
    document = Document()
    table_rows: list[list[str]] = []

    def flush_table() -> None:
        if table_rows:
            table = document.add_table(rows=len(table_rows), cols=len(table_rows[0]))
            for row, values in zip(table.rows, table_rows, strict=True):
                for cell, value in zip(row.cells, values, strict=True):
                    cell.text = value
            table_rows.clear()

    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("|"):
            table_rows.append([cell.strip() for cell in line.strip("|").split("|")])
            continue
        flush_table()
        if line.startswith("## "):
            document.add_heading(line[3:], level=2)
        elif line.startswith("# "):
            document.add_heading(line[2:], level=1)
        else:
            document.add_paragraph(line)
    flush_table()
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _pdf_escape(text: str) -> str:
    if not text.isascii():
        raise ValueError(f"PDF source must be ASCII (built-in fonts cannot render it): {text!r}")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render_pdf(source: str) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    pages = [page.strip() for page in source.split(PAGE_BREAK)]
    for page_text in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        # One text object; T* moves to the next line using the 14pt leading.
        operations = ["BT", "/F1 10 Tf", "14 TL", "40 750 Td"]
        for line in page_text.splitlines():
            operations.append(f"({_pdf_escape(line.strip())}) Tj T*")
        operations.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(operations).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def render(item: CorpusFile) -> bytes:
    source_path = CORPUS_DIR / item.source
    if item.filename.endswith(".docx"):
        return render_docx(source_path.read_text(encoding="utf-8"))
    if item.filename.endswith(".pdf"):
        return render_pdf(source_path.read_text(encoding="utf-8"))
    return source_path.read_bytes()


def build(out_dir: Path) -> dict[str, Path]:
    """Write every corpus file into `out_dir`; returns filename → path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for item in CORPUS:
        path = out_dir / item.filename
        if item.filename.endswith(".txt"):
            shutil.copyfile(CORPUS_DIR / item.source, path)
        else:
            path.write_bytes(render(item))
        paths[item.filename] = path
    return paths


def describe(paths: dict[str, Path]) -> None:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.schemas import Location
    from app.services.ingestion import build_chunks, parse_blocks

    for filename, path in paths.items():
        print(f"## {filename}")
        for ordinal, chunk in enumerate(build_chunks(parse_blocks(path))):
            location = Location(
                kind=chunk.kind,
                start=chunk.start,
                end=chunk.end,
                table=chunk.table,
                heading_path=list(chunk.heading_path),
                char_start=chunk.char_start,
                char_end=chunk.char_end,
            )
            preview = chunk.content.replace("\n", " / ")[:90]
            print(
                f"  [{ordinal}] {chunk.kind} {location.label} ({len(chunk.content)} 字): {preview}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--describe", action="store_true", help="print parsed chunk locations")
    args = parser.parse_args()
    paths = build(args.out)
    print(f"built {len(paths)} files in {args.out}")
    if args.describe:
        describe(paths)


if __name__ == "__main__":
    main()
