"""Docling pipeline configuration of the parse worker; builds option objects only, no models."""

from dataclasses import replace

from docling.datamodel.pipeline_options import RapidOcrOptions, TableFormerMode

from app.parsers import docling_runner
from app.parsers.base import DoclingConfig

CONFIG = DoclingConfig("", "cpu", 2, "rapidocr", True, "BAAI/bge-m3", 512)


def test_standard_mode_has_tables_without_ocr_or_enrichment():
    options = docling_runner.pipeline_options(CONFIG, "standard")
    assert options.do_ocr is False
    assert options.do_table_structure is True
    assert options.table_structure_options.mode == TableFormerMode.ACCURATE
    assert options.do_formula_enrichment is False
    assert options.do_picture_description is False
    assert options.accelerator_options.num_threads == 2


def test_enhanced_mode_enables_full_page_rapidocr():
    options = docling_runner.pipeline_options(CONFIG, "enhanced")
    assert options.do_ocr is True
    assert isinstance(options.ocr_options, RapidOcrOptions)
    assert options.ocr_options.force_full_page_ocr is True


def test_configured_artifacts_path_wins(tmp_path):
    config = replace(CONFIG, artifacts_path=str(tmp_path))
    assert docling_runner.artifacts_path(config) == str(tmp_path)
    assert docling_runner.pipeline_options(config, "standard").artifacts_path == str(tmp_path)


def test_prefetched_default_cache_is_used_offline(tmp_path, monkeypatch):
    from docling.datamodel.settings import settings

    monkeypatch.setattr(settings, "cache_dir", tmp_path)
    assert docling_runner.artifacts_path(CONFIG) is None
    (tmp_path / "models").mkdir()
    assert docling_runner.artifacts_path(CONFIG) == str(tmp_path / "models")
