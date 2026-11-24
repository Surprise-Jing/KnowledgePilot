"""Docling conversion, run inside the parse worker process (app/parsers/worker.py).
Never imported by the web process: it loads Docling, torch and the layout/table models.

Written against docling 2.152: PdfPipelineOptions with TableFormer ACCURATE; OCR only in the
enhanced mode."""

import logging
from importlib.metadata import version
from multiprocessing.connection import Connection
from typing import Any

from app.parsers.base import (
    DoclingConfig,
    ParseError,
    ParseRequest,
    ParseResult,
)
from app.parsers.docling_mapping import build_chunker, chunks_from_document

logger = logging.getLogger(__name__)

CORRUPT = "文档解析失败，请检查文件是否损坏或加密"
MODELS_UNAVAILABLE = "解析模型不可用，请联网后重试或先运行 python -m app.parsers.prefetch"
EMPTY_STANDARD = "未提取到文本；如为扫描件，请使用增强模式（OCR）重新解析"
EMPTY_ENHANCED = "OCR 后仍未提取到文本，请检查文件"


def _model_errors() -> tuple[type[BaseException], ...]:
    from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError

    return OSError, HfHubHTTPError, LocalEntryNotFoundError


def _ocr_options(config: DoclingConfig):
    from docling.datamodel import pipeline_options as po

    engines = {
        "rapidocr": po.RapidOcrOptions,
        "easyocr": po.EasyOcrOptions,
        "tesseract": po.TesseractCliOcrOptions,
        "ocrmac": po.OcrMacOptions,
    }
    return engines[config.ocr_engine](force_full_page_ocr=config.force_full_page_ocr)


def artifacts_path(config: DoclingConfig) -> str | None:
    """Local model directory: the configured one, else Docling's default cache once prefetch
    has filled it. A local path makes Docling load models without asking the Hugging Face Hub,
    which can hang for a long time on an unreliable network."""
    if config.artifacts_path:
        return config.artifacts_path
    from docling.datamodel.settings import settings

    default = settings.cache_dir / "models"
    return str(default) if default.is_dir() else None


def _tokenizer(name: str):
    """Prefer the cached tokenizer (no network round trip); download only when missing."""
    from transformers import AutoTokenizer

    try:
        return AutoTokenizer.from_pretrained(name, local_files_only=True)
    except OSError:
        return AutoTokenizer.from_pretrained(name)


def pipeline_options(config: DoclingConfig, mode: str):
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
    )

    options = PdfPipelineOptions(
        do_ocr=mode == "enhanced",
        do_table_structure=True,
        table_structure_options=TableStructureOptions(
            mode=TableFormerMode.ACCURATE, do_cell_matching=True
        ),
        do_formula_enrichment=False,
        do_code_enrichment=False,
        do_picture_description=False,
        do_picture_classification=False,
        generate_page_images=False,
        accelerator_options=AcceleratorOptions(device=config.device, num_threads=config.threads),
        artifacts_path=artifacts_path(config),
    )
    if mode == "enhanced":
        options.ocr_options = _ocr_options(config)
    return options


class DoclingRunner:
    """Caches one converter per mode and the chunking tokenizer for the worker's lifetime."""

    def __init__(self, config: DoclingConfig):
        self.config = config
        self._converters: dict[str, Any] = {}
        self._chunker = None

    def _converter(self, mode: str):
        if mode not in self._converters:
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import (
                DocumentConverter,
                PdfFormatOption,
                WordFormatOption,
            )

            self._converters[mode] = DocumentConverter(
                allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=pipeline_options(self.config, mode)
                    ),
                    InputFormat.DOCX: WordFormatOption(),
                },
            )
        return self._converters[mode]

    def chunker(self):
        if self._chunker is None:
            from docling_core.transforms.chunker.tokenizer.huggingface import (
                HuggingFaceTokenizer,
            )

            tokenizer = HuggingFaceTokenizer(
                tokenizer=_tokenizer(self.config.tokenizer),
                max_tokens=self.config.max_tokens,
            )
            self._chunker = build_chunker(tokenizer)
        return self._chunker

    def parse(self, request: ParseRequest) -> ParseResult:
        from docling.datamodel.base_models import ConversionStatus

        try:
            chunker = self.chunker()
            result = self._converter(request.mode).convert(request.path, raises_on_error=False)
        except _model_errors():
            logger.exception("Docling models or tokenizer unavailable")
            raise ParseError(MODELS_UNAVAILABLE) from None
        except Exception:
            logger.exception("Docling conversion raised")
            raise ParseError(CORRUPT) from None
        if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
            logger.warning("Docling conversion status %s", result.status.value)
            raise ParseError(CORRUPT)
        document = result.document
        document.save_as_json(request.output_dir / "document.json")
        (request.output_dir / "document.md").write_text(
            document.export_to_markdown(), encoding="utf-8"
        )
        file_type = "pdf" if request.file_type == "pdf" else "docx"
        chunks = chunks_from_document(document, chunker, file_type)
        if not chunks:
            raise ParseError(EMPTY_ENHANCED if request.mode == "enhanced" else EMPTY_STANDARD)
        return ParseResult(
            chunks=chunks,
            parser=f"docling {version('docling')} / {request.mode}",
            page_count=len(document.pages) if file_type == "pdf" else None,
            has_markdown=True,
            has_structure=True,
        )


def serve(conn: Connection, config: DoclingConfig) -> None:
    """Worker main loop: ("parse", job_id, request) → (job_id, ok, result | message);
    ("stop",) ends the process."""
    from app.logging_setup import configure_logging

    configure_logging(config.log_level)
    runner = DoclingRunner(config)
    while True:
        try:
            message = conn.recv()
        except EOFError:
            return
        if message[0] == "stop":
            return
        _, job_id, request = message
        try:
            conn.send((job_id, True, runner.parse(request)))
        except ParseError as exc:
            conn.send((job_id, False, str(exc)))
        except Exception:
            logger.exception("Unexpected parse worker error")
            conn.send((job_id, False, CORRUPT))
