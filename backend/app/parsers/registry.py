"""File type → Parser. create_app stores one registry in app.state.parsers; tests replace
`build_registry` (tests/conftest.py) to stay offline."""

from app.config import Settings
from app.parsers.base import DoclingConfig, ParseError, Parser
from app.parsers.text import TextParser
from app.parsers.worker import DoclingParser, ParserWorker

# Imported only inside the worker process; passed by name so this module stays Docling-free.
DOCLING_ENTRY = "app.parsers.docling_runner:serve"


class ParserRegistry:
    def __init__(self, parsers: dict[str, Parser]):
        self._parsers = parsers

    def for_type(self, file_type: str) -> Parser:
        try:
            return self._parsers[file_type]
        except KeyError:
            raise ParseError("不支持的文档格式") from None

    def close(self) -> None:
        closed: set[int] = set()
        for parser in self._parsers.values():
            close = getattr(parser, "close", None)
            if close is not None and id(parser) not in closed:
                closed.add(id(parser))
                close()


def build_registry(settings: Settings) -> ParserRegistry:
    """PDF and DOCX share one worker, so heavy conversions never run concurrently."""
    worker = ParserWorker(
        DOCLING_ENTRY, DoclingConfig.from_settings(settings), settings.parse_timeout
    )
    docling = DoclingParser(worker)
    return ParserRegistry({"txt": TextParser(), "pdf": docling, "docx": docling})
