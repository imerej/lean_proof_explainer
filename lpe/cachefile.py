"""Small helpers for the on-disk caches (LLM replies, goal states, verdicts, check results)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` so that a concurrent reader never sees a half-written file.

    Several workers may finish the same cache entry at once (`run --jobs`); each writes its own
    temporary file and renames it into place, which is atomic on POSIX file systems.
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
