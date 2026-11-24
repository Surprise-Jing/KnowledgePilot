"""Parse artifacts on disk: {data_dir}/artifacts/{document_id}/document.md (+ document.json).

A parse writes into a fresh staging directory; only after its chunks are committed is the
staging directory swapped in, so a failed or interrupted reparse never leaves a half-written
artifact behind."""

import logging
import shutil
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from app.config import Settings

logger = logging.getLogger(__name__)

MARKDOWN = "document.md"
STRUCTURE = "document.json"


def artifacts_root(settings: Settings) -> Path:
    return settings.data_dir / "artifacts"


def final_dir(root: Path, document_id: str) -> Path:
    return root / document_id


def new_staging_dir(root: Path, document_id: str) -> Path:
    path = root / f"{document_id}.tmp-{uuid4().hex}"
    path.mkdir(parents=True)
    return path


def discard(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def commit(staging: Path, final: Path) -> None:
    """Replace `final` with `staging`; the old directory is moved aside first so a reader
    never sees a mix of old and new files."""
    old = None
    if final.exists():
        old = final.with_name(f"{final.name}.old-{uuid4().hex}")
        final.rename(old)
    try:
        staging.rename(final)
    except OSError:
        if old is not None:
            old.rename(final)  # keep the artifacts that match the still-committed chunks
        raise
    if old is not None:
        discard(old)


def remove_documents(root: Path, document_ids: Iterable[str]) -> None:
    """Delete artifacts of deleted documents. Failures only leave orphaned files, so they are
    logged instead of failing a committed delete."""
    for document_id in document_ids:
        try:
            shutil.rmtree(final_dir(root, document_id))
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning(
                "Failed to remove artifacts of document %s: %s", document_id, type(exc).__name__
            )


def cleanup_staging(root: Path) -> int:
    """Remove staging / replaced directories left by an interrupted process."""
    if not root.is_dir():
        return 0
    stale = [p for p in root.iterdir() if ".tmp-" in p.name or ".old-" in p.name]
    for path in stale:
        discard(path)
    return len(stale)
