"""Check a candidate proof with the real Lean compiler.

The candidate replaces the original proof inside a copy of the original Mathlib
file, truncated right after the declaration. This keeps the exact context
(imports, namespaces, `variable`s, `open`s) without having to re-elaborate the
statement, and it means the candidate cannot cite lemmas that come later in the
same file. A `#print axioms` line at the end rejects proofs that lean on
`sorry` or on non-standard axioms.

Results can be cached on disk, keyed by the exact file Lean sees plus the toolchain and the
Lake manifest (which pins the Mathlib revision). Rebuilders often propose the same candidate
more than once (across samples, masked and unmasked runs, reruns after an interruption), and
each check otherwise costs a full `lake env lean` start with the imports loaded. Timeouts and
failures of the environment itself (errors outside the declaration, e.g. a missing `.olean`)
are never cached, since they say nothing about the candidate.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .cachefile import atomic_write_text
from .extract import Declaration

# Verdicts about the machine rather than the candidate; never cached.
UNCACHED = {"timeout", "environment", "crash"}
# Bump when the checking logic changes in a way that changes verdicts, to invalidate cached results.
CHECKER_VERSION = 2

STANDARD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
BANNED = re.compile(r"\b(sorry|admit|axiom|native_decide|implemented_by|extern|run_tac|elab|macro|syntax)\b|set_option\s+debug|#exit")


@dataclass
class CheckResult:
    ok: bool
    reason: str  # "ok", "banned-token", "error", "timeout", "axioms", "environment", "crash"
    errors: str = ""  # compiler messages in the declaration's span, for feedback prompts
    seconds: float = 0.0  # time Lean took (for a cached result: when it was first checked)
    cached: bool = False


@dataclass
class CheckStats:
    lean_runs: int = 0
    cache_hits: int = 0
    lean_seconds: float = 0.0


class LeanChecker:
    def __init__(self, project_dir: Path, timeout: int = 600, lake: str = "lake", cache_dir: Path | None = None):
        self.project_dir = Path(project_dir)
        self.timeout = timeout
        self.lake = lake
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.stats = CheckStats()
        self._lock = threading.Lock()  # `check` may be called from several worker threads
        self._env_key = self._environment_key()

    def _environment_key(self) -> str:
        """What, besides the source, decides a verdict: toolchain, pinned dependencies, checker logic."""
        parts = [f"checker {CHECKER_VERSION}"]
        for name in ("lean-toolchain", "lake-manifest.json"):
            f = self.project_dir / name
            parts.append(f.read_text(encoding="utf-8") if f.exists() else f"(no {name})")
        return "\n".join(parts)

    def check(self, decl: Declaration, proof: str) -> CheckResult:
        if BANNED.search(proof):
            return CheckResult(False, "banned-token", f"proof uses a banned token: {BANNED.search(proof).group(0)}")
        source = decl.splice(proof)
        full_name = self._full_name(decl, source)
        source += f"\n#print axioms {full_name}\n"

        path = None
        if self.cache_dir:
            key = hashlib.sha256((self._env_key + "\n" + source).encode()).hexdigest()
            path = self.cache_dir / f"{key}.json"
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    data["cached"] = True
                    result = CheckResult(**data)
                except (ValueError, TypeError):
                    result = None  # unreadable entry: check again and overwrite it
                if result is not None:
                    with self._lock:
                        self.stats.cache_hits += 1
                    return result
        result = self._run_lean(decl, source)
        with self._lock:
            self.stats.lean_runs += 1
            self.stats.lean_seconds += result.seconds
        if path and result.reason not in UNCACHED:
            atomic_write_text(path, json.dumps(asdict(result) | {"cached": False}, ensure_ascii=False))
        return result

    def _run_lean(self, decl: Declaration, source: str) -> CheckResult:
        decl_first_line = source[: source.find(decl.header)].count("\n") + 1
        with tempfile.NamedTemporaryFile("w", suffix=".lean", dir=self.project_dir, delete=False, encoding="utf-8") as fh:
            fh.write(source)
            tmp = Path(fh.name)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                [self.lake, "env", "lean", str(tmp)],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(False, "timeout", f"Lean timed out after {self.timeout}s", self.timeout)
        finally:
            tmp.unlink(missing_ok=True)
        elapsed = time.monotonic() - started

        output = proc.stdout + proc.stderr
        messages = parse_messages(output)
        outside = [m for m in messages if m.severity == "error" and m.line < decl_first_line]
        if outside:
            # The file around the declaration did not elaborate (e.g. an import is not built); the
            # candidate was never really checked. Reported apart from candidate errors, never cached.
            text = "\n".join(f"line {m.line} (before the declaration): {m.text}" for m in outside)
            return CheckResult(False, "environment", text[:4000], elapsed)
        errors = [m for m in messages if m.severity == "error" and m.line >= decl_first_line]
        if errors:
            text = "\n".join(
                f"line {m.line - decl_first_line + 1}: {re.sub(r'^.*depends on axioms:.*$', '', m.text, flags=re.M).strip()}"
                for m in errors
            )
            return CheckResult(False, "error", text, elapsed)
        if proc.returncode != 0 and not messages:
            # Lean or Lake died without a diagnostic (out of memory, a crash, a Lake failure).
            return CheckResult(False, "crash", output[-4000:], elapsed)
        axioms = axioms_used(output)
        if axioms is None or not axioms <= STANDARD_AXIOMS:
            return CheckResult(False, "axioms", f"axioms used: {sorted(axioms or [])}", elapsed)
        return CheckResult(True, "ok", "", elapsed)

    @staticmethod
    def _full_name(decl: Declaration, source: str) -> str:
        """Fully qualified name: enclosing `namespace`s + the name as written in the header."""
        m = re.search(r"\b(?:theorem|lemma)\s+([^\s:({\[]+)", decl.header)
        written = m.group(1) if m else decl.name.split(".")[-1]
        if written.startswith("_root_."):
            return written.removeprefix("_root_.")
        stack: list[str] = []
        for line in source[: source.find(decl.header)].splitlines():
            s = line.strip()
            if s.startswith("namespace "):
                stack.extend(s.split()[1].split("."))
            elif s.startswith("end ") and stack:
                parts = s.split()[1].split(".")
                if stack[-len(parts) :] == parts:
                    del stack[-len(parts) :]
        return ".".join(stack + [written])


@dataclass
class Message:
    line: int
    severity: str
    text: str


_MSG_RE = re.compile(r"^.*?\.lean:(\d+):(\d+): (error|warning|info)(?:\([^)]*\))?: ?", re.M)


def parse_messages(output: str) -> list[Message]:
    matches = list(_MSG_RE.finditer(output))
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(output)
        out.append(Message(int(m.group(1)), m.group(3), output[m.end() : end].strip()))
    return out


def axioms_used(output: str) -> set[str] | None:
    if "does not depend on any axioms" in output:
        return set()
    m = re.search(r"depends on axioms: \[([^\]]*)\]", output)
    if not m:
        return None
    return {a.strip() for a in m.group(1).split(",") if a.strip()}
