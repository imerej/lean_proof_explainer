"""Result files: one JSON row per (theorem, strategy, masked), appended by `lpe run`.

`run` writes each row with a single `write` and a flush, but a row with its trace can be larger than
the file buffer, so an interrupted run (Ctrl-C, a killed job, a full disk) can leave half a row at the
end of the file. `read_rows` skips such a tail with a warning, and `repair_tail` cuts it off before
`run` appends to the file again, so that the next row does not get glued onto it. Any other line that
is not JSON is an error, reported with its file and line number.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .explain import Explanation, Leakage
from .extract import Declaration
from .reconstruct import Reconstruction


class ResultFileError(ValueError):
    pass


def _parse(line: str) -> dict | None:
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) else None


def read_rows(path: Path | str, warn=None) -> list[dict]:
    """The rows of one result file. Blank lines are ignored; an unparsable last line without a
    trailing newline is treated as an interrupted write and skipped (with a message to `warn`)."""
    path = Path(path)
    warn = warn or (lambda msg: print(msg, file=sys.stderr))
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    rows = []
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        row = _parse(line)
        if row is not None:
            rows.append(row)
        elif i == len(lines):  # last piece and no newline after it
            warn(f"{path}:{i}: skipping an incomplete last row (interrupted write?)")
        else:
            raise ResultFileError(f"{path}:{i}: not a JSON object: {line[:80]!r}")
    return rows


def read_all(paths, warn=None) -> list[dict]:
    return [row for p in paths for row in read_rows(p, warn)]


def repair_tail(path: Path | str) -> str | None:
    """Make `path` end with a complete line before appending. A complete JSON row that only lacks
    its newline gets one; a partial row is removed. Returns what was done, or None."""
    path = Path(path)
    if not path.exists():
        return None
    data = path.read_bytes()
    if not data or data.endswith(b"\n"):
        return None
    cut = data.rfind(b"\n") + 1  # 0 when the file is a single unterminated line
    tail = data[cut:].decode("utf-8", errors="replace")
    if _parse(tail) is not None:
        with path.open("ab") as fh:
            fh.write(b"\n")
        return "added a missing newline after the last row"
    with path.open("r+b") as fh:
        fh.truncate(cut)
    return f"removed an incomplete last row ({len(data) - cut} bytes)"


def row_key(row: dict) -> tuple:
    return (row["theorem"], row["strategy"], row["masked"])


def make_row(
    entry: dict,
    decl: Declaration,
    strategy: str,
    masked: bool,
    ex: Explanation,
    text: str,
    leak: Leakage,
    rec: Reconstruction,
    *,
    explainer: str | None,
    reconstructor: str,
    samples: int,
    feedback: int,
    all_samples: bool,
    seconds: float,
) -> dict:
    """The row `run` writes for one rebuild. `text` is the explanation as the rebuilder saw it
    (after masking); `ex.text` is what the explainer wrote."""
    return {
        "theorem": entry["name"],
        "file": entry["file"],
        "proof_lines": decl.line_count,
        "strategy": strategy,
        "masked": masked,
        "explainer": explainer if strategy != "none" else None,
        "reconstructor": reconstructor,
        "success": rec.success,
        "first_success": rec.first_success,
        "samples": samples,
        "feedback": feedback,
        "all_samples": all_samples,
        "sample_success": rec.sample_success,
        "attempts": len(rec.attempts),
        "failure_reasons": [a.result.reason for a in rec.attempts if not a.result.ok],
        "explainer_calls": ex.calls,
        "explanation_chars": len(text),
        "leak_rate": round(leak.rate, 3),
        "leaked_identifiers": leak.identifiers_leaked,
        "proof_identifiers": leak.identifiers_total,
        "lean_notation": leak.notation,
        "explanation": text,
        "statement": decl.statement_in_context,
        "proof": decl.proof,
        "trace": ex.trace,
        "final_proof": rec.attempts[-1].proof if rec.attempts else "",
        "seconds": round(seconds, 1),
    }
