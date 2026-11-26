"""Lean's verdict on routine alternatives at the start of each explained part of a proof.

A chess annotator explains a move partly by showing why the obvious alternatives fail. The proof
analogue: at the goal where a part of the proof starts, try the standard closers (`simp`, `omega`,
`linarith`, ...) and record what Lean says. If none closes the goal, the part is doing real work,
and the explainer can say what makes the goal non-routine; if one does, the explainer can say the
step is routine instead of inventing a reason for it. This is the "contrastive why" of
docs/ROADMAP.md, with the verdicts coming from Lean rather than from the explainer's guess
(cf. the engine-checked contrasts of BinProp and ACT-Eval in docs/literature.md).

`lean/TryTactics.lean` does the work: it elaborates the theorem's file (truncated after the
theorem, as the checker sees it) and, for each requested position, runs each alternative on the
goal before the tactic at that position, in a fresh copy of the proof state.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .cachefile import atomic_write_text
from .extract import Declaration, ProofNode, parse_proof_tree
from .goals import ProofStates, proof_first_line

SCRIPT = "TryTactics.lean"

# Closers a reader would try first. All are Mathlib or core tactics that either finish the goal or
# fail quickly; anything that searches the library (`exact?`, `apply?`) is left out because it is
# slow and its success says little about whether the step is routine for a person.
DEFAULT_ALTERNATIVES = ("simp", "omega", "linarith", "nlinarith", "norm_num", "ring", "positivity", "aesop")


@dataclass
class Verdict:
    tactic: str
    status: str  # "closed" | "progress" | "unchanged" | "failed" | "unavailable" (not imported here)
    goals: list[str] = field(default_factory=list)  # remaining goals for "progress"
    error: str = ""


@dataclass
class Probe:
    """Alternatives tried on one goal: `line`/`col` in proof coordinates (as `TacticState`)."""

    line: int
    col: int
    goal: str
    verdicts: list[Verdict]


@dataclass
class Contrasts:
    probes: list[Probe] = field(default_factory=list)
    error: str = ""

    def at(self, line: int, col: int, goal: str | None = None) -> Probe | None:
        """The probe at a position; with `goal`, the one whose goal text matches it."""
        here = [p for p in self.probes if p.line == line and p.col == col]
        if goal is not None:
            exact = [p for p in here if p.goal == goal]
            return exact[0] if exact else None
        return here[0] if here else None

    def to_json(self) -> str:
        return json.dumps({"probes": [asdict(p) for p in self.probes], "error": self.error}, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Contrasts":
        data = json.loads(text)
        probes = [Probe(p["line"], p["col"], p["goal"], [Verdict(**v) for v in p["verdicts"]]) for p in data["probes"]]
        return cls(probes, data.get("error", ""))


def start_of_part(node: ProofNode, root: ProofNode) -> ProofNode | None:
    """The proof-tree node whose goal-before is the goal this part of the proof starts from.

    The root starts at its first step. A `have …/show …/suffices … := by` line opens a sub-proof
    whose goal is the claim itself, i.e. the goal before its first child. Anything else (a case
    alternative, a bullet, `induction … with`, `calc`, …) starts from its own goal-before.
    """
    if node is root:
        return node.children[0] if node.children else None
    if node.children and node.text.rstrip().endswith("by"):
        return node.children[0]
    return node


def probe_targets(
    decl: Declaration, states: ProofStates, min_children: int = 1
) -> dict[tuple[int, int], tuple[int, int, str]]:
    """For each explained node, keyed by its (line, column), the (line, col, goal text) to probe.

    The root is keyed (-1, 0). Explained nodes are the ones the recursive pass calls the explainer
    for: the root and nodes with at least `min_children` children.

    The goal text is the one `goals.py` attached to the starting node, so the probe matches the
    state the explainer is shown. Nodes without a state are skipped.
    """
    root = parse_proof_tree(decl.proof)
    out: dict[tuple[int, int], tuple[int, int, str]] = {}

    def walk(node: ProofNode) -> None:
        for child in node.children:
            walk(child)
        if node is not root and len(node.children) < min_children:
            return
        start = start_of_part(node, root)
        st = states.for_node(start) if start is not None else None
        if st is not None and st.before:
            out[(node.line, node.column)] = (st.line, st.col, st.before[0])

    walk(root)
    return out


def parse_output(output: str, decl: Declaration) -> list[Probe]:
    """Group TryTactics.lean output (file coordinates) into probes in proof coordinates."""
    first = proof_first_line(decl)
    probes: dict[tuple[int, int, str], Probe] = {}
    for raw in output.splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        r = json.loads(raw)
        key = (r["line"] - first, r["col"] - decl.proof_indent, r["goal"])
        probe = probes.setdefault(key, Probe(*key, verdicts=[]))
        probe.verdicts.append(Verdict(r["tactic"], r["status"], r.get("goals", []), r.get("error", "")))
    return list(probes.values())


class ContrastRunner:
    """Runs `lean/TryTactics.lean` for a declaration; results are cached on disk."""

    def __init__(
        self,
        project_dir: Path,
        alternatives: tuple[str, ...] | list[str] = DEFAULT_ALTERNATIVES,
        heartbeats: int = 50000,
        timeout: int = 1800,
        lake: str = "lake",
        cache_dir: Path | None = None,
    ):
        self.project_dir = Path(project_dir)
        self.alternatives = list(alternatives)
        self.heartbeats = heartbeats
        self.timeout = timeout
        self.lake = lake
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def contrasts(self, decl: Declaration, states: ProofStates, min_children: int = 1) -> Contrasts:
        if states.error or not states.tactics:
            return Contrasts(error=f"no goal states: {states.error or 'empty'}")
        targets = sorted({(line, col) for line, col, _ in probe_targets(decl, states, min_children).values()})
        if not targets:
            return Contrasts()
        source = decl.original_source
        spec = {"tactics": self.alternatives, "heartbeats": self.heartbeats, "positions": targets}
        script = (self.project_dir / SCRIPT).read_text(encoding="utf-8") if (self.project_dir / SCRIPT).exists() else ""
        key = hashlib.sha256((source + json.dumps(spec, sort_keys=True) + script).encode()).hexdigest()
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path and path.exists():
            return Contrasts.from_json(path.read_text(encoding="utf-8"))
        result = self._run(decl, source, targets)
        if path and not result.error:
            atomic_write_text(path, result.to_json())
        return result

    def _run(self, decl: Declaration, source: str, targets: list[tuple[int, int]]) -> Contrasts:
        first = proof_first_line(decl)
        positions = [[line + first, col + decl.proof_indent] for line, col in targets]
        probe = {"positions": positions, "tactics": self.alternatives, "maxHeartbeats": self.heartbeats}
        tmp_files: list[Path] = []
        try:
            for suffix, text in ((".lean", source), (".json", json.dumps(probe))):
                with tempfile.NamedTemporaryFile(
                    "w", suffix=suffix, dir=self.project_dir, delete=False, encoding="utf-8"
                ) as fh:
                    fh.write(text)
                    tmp_files.append(Path(fh.name))
            proc = subprocess.run(
                [self.lake, "env", "lean", "--run", SCRIPT, str(tmp_files[0]), str(tmp_files[1])],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return Contrasts(error=f"alternative tactics timed out after {self.timeout}s")
        finally:
            for f in tmp_files:
                f.unlink(missing_ok=True)
        probes = parse_output(proc.stdout, decl)
        if proc.returncode != 0 or not probes:
            return Contrasts(probes, error=(proc.stderr or proc.stdout)[-2000:] or "no probes returned")
        return Contrasts(probes)


# ---------------------------------------------------------------- rendering for prompts


def _first_line(text: str, limit: int = 160) -> str:
    line = next((s.strip() for s in text.splitlines() if s.strip()), "").rstrip(":")
    return line if len(line) <= limit else line[:limit] + " …"


def _target(goal: str) -> str:
    lines = goal.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("⊢")), len(lines))
    return " ".join(s.strip() for s in lines[start:])


def render_verdicts(probe: Probe) -> str:
    """One line per alternative: closes it / leaves … / fails (first line of Lean's error).

    Alternatives that are not available in the theorem's file are left out: the rebuilder cannot
    use them either, so they say nothing about this goal.
    """
    lines = []
    for v in probe.verdicts:
        if v.status == "unavailable":
            continue
        if v.status == "closed":
            what = "closes this goal on its own"
        elif v.status == "progress":
            left = "; ".join(_target(g) for g in v.goals[:2]) + (" …" if len(v.goals) > 2 else "")
            what = f"does not finish; leaves {left}"
        elif v.status == "unchanged":
            what = "runs but changes nothing"
        else:
            what = f"fails: {_first_line(v.error)}"
        lines.append(f"- `{v.tactic}` {what}")
    return "\n".join(lines)


def verdict_summary(probe: Probe) -> str:
    closers = [v.tactic for v in probe.verdicts if v.status == "closed"]
    if closers:
        return f"routine: closed directly by {', '.join(closers)}"
    return "non-routine: no standard closer finishes it"
