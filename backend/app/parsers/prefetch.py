"""Download the Docling layout / table / OCR models and the chunking tokenizer so later
parsing works offline:

    uv run --directory backend python -m app.parsers.prefetch
"""

from pathlib import Path

from app.config import Settings


def main() -> None:
    from docling.utils.model_downloader import download_models
    from transformers import AutoTokenizer

    settings = Settings()
    target = Path(settings.docling_artifacts_path) if settings.docling_artifacts_path else None
    path = download_models(
        output_dir=target,
        progress=True,
        with_layout=True,
        with_tableformer=True,
        with_rapidocr=True,
        with_code_formula=False,
        with_picture_classifier=False,
    )
    AutoTokenizer.from_pretrained(settings.chunk_tokenizer)
    print(f"Docling 模型已就绪：{path}")
    print(f"分词器已就绪：{settings.chunk_tokenizer}")


if __name__ == "__main__":
    main()
