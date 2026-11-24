"""TextParser and the shared element helpers of the Parser Adapter layer (app/parsers)."""

import pytest

from app.parsers.base import ParseError, ParseRequest, SourceElementData, limit_elements
from app.parsers.text import TextParser


def parse(tmp_path, data: bytes):
    source = tmp_path / "a.txt"
    source.write_bytes(data)
    output = tmp_path / "out"
    output.mkdir()
    return TextParser().parse(ParseRequest(source, "txt", "standard", output)), output


def test_text_parser_lines_and_markdown(tmp_path):
    text = "第一行\n\n第三行"
    result, output = parse(tmp_path, text.encode("gbk"))
    assert [(c.kind, c.start, c.end) for c in result.chunks] == [("txt_lines", 1, 3)]
    assert (output / "document.md").read_text(encoding="utf-8") == text
    assert [e.ref for e in result.chunks[0].elements] == ["line:1", "line:3"]
    assert all(e.page is None and e.bbox is None for e in result.chunks[0].elements)
    assert result.parser == "text"
    assert result.has_markdown and not result.has_structure
    assert result.page_count is None


def test_text_parser_long_line_windows(tmp_path):
    result, _ = parse(tmp_path, ("字" * 2000).encode())
    assert [(c.char_start, c.char_end) for c in result.chunks] == [
        (0, 800),
        (600, 1400),
        (1200, 2000),
    ]


def test_text_parser_bad_encoding(tmp_path):
    with pytest.raises(ParseError, match="无法识别文本编码"):
        parse(tmp_path, b"\xff\xfe\x00\xd8")


def test_limit_elements_dedup_and_cap():
    items = [SourceElementData(f"#/texts/{i}", "text", 1, None) for i in range(70)]
    items.append(SourceElementData("#/texts/0", "text", 1, None))
    limited = limit_elements(items)
    assert len(limited) == 64
    assert len({(e.ref, e.page) for e in limited}) == 64


def test_text_chunk_elements_are_capped(tmp_path):
    result, _ = parse(tmp_path, "\n".join("项" for _ in range(300)).encode())
    assert len(result.chunks) == 1
    assert len(result.chunks[0].elements) == 64
