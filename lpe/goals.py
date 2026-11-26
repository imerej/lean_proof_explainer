"""Goal states before and after each tactic of a proof, read from Lean's info trees.

`lean/GoalStates.lean` elaborates a file with info trees enabled and prints one JSON object
per tactic (position, syntax kind, goals before, goals after). This module runs it on the
declaration's own file (truncated after the declaration, exactly as the checker sees it),
keeps the records that belong to the proof, and attaches them to the indentation-based proof
tree of `extract.py`: the state of a tree node is the outermost tactic that starts where the
node's line starts.

This gives the explainer what a reader of the proof in an editor would see, instead of the
code alone (cf. Herald and the step-wise informalization of Hattori et al., which both
translate tactic by tactic from proof states; see docs/literature.md).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .cachefile import atomic_write_text
from .extract import Declaration, ProofNode

SCRIPT = "GoalStates.lean"

# Wrapper syntax that spans whole tactic blocks; the tactic inside it is the informative node.
_WRAPPERS = {
    "null",
    "by",
    "Lean.Parser.Term.byTactic",
    "Lean.Parser.Tactic.tacticSeq",
    "Lean.Parser.Tactic.tacticSeq1Indented",
    "Lean.Parser.Tactic.tacticSeqBracketed",
}


@dataclass
class TacticState:
    line: int  # 0-based line in the dedented proof (`Declaration.proof`)
    col: int  # column in the dedented proof
    end_line: int
    end_col: int
    kind: str
    before: list[str]
    after: list[str]


@dataclass
class ProofStates:
    tactics: list[TacticState] = field(default_factory=list)
    error: str = ""  # non-empty when extraction failed; the explainer then runs without states

    @property
    def initial(self) -> list[str]:
        """Goals at the start of the proof (hypotheses + target of the theorem)."""
        return self.tactics[0].before if self.tactics else []

    def at(self, line: int, column: int | None = None) -> TacticState | None:
        """The outermost tactic starting on `line` (at `column`, if given).

        Records come in pre-order, so the first match is the outermost. Wrapper syntax is used
        only when nothing more specific starts there (e.g. a `| succ k ih =>` alternative), and a
        record with no goals before it is never used.
        """
        here = [t for t in self.tactics if t.line == line and (column is None or t.col == column)]
        # Records without goals before them (e.g. the `calcSteps` syntax) carry no information.
        useful = [t for t in here if t.before]
        specific = [t for t in useful if t.kind not in _WRAPPERS]
        return (specific or useful or [None])[0]

    def for_node(self, node: ProofNode) -> TacticState | None:
        if node.line < 0:
            return None
        return self.at(node.line, node.column) or self.at(node.line)

    def to_json(self) -> str:
        return json.dumps({"tactics": [asdict(t) for t in self.tactics], "error": self.error}, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "ProofStates":
        data = json.loads(text)
        return cls([TacticState(**t) for t in data["tactics"]], data.get("error", ""))


def proof_first_line(decl: Declaration) -> int:
    """1-based line of the proof's first line in `decl.original_source`."""
    return decl.prefix.count("\n") + decl.header.count("\n") + 2


def parse_records(output: str, decl: Declaration) -> list[TacticState]:
    """Convert GoalStates.lean output (file coordinates) to proof coordinates."""
    first = proof_first_line(decl)
    out = []
    for raw in output.splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        r = json.loads(raw)
        if r["line"] < first:  # the `by` / byTactic nodes on the header line
            continue
        out.append(
            TacticState(
                line=r["line"] - first,
                col=r["col"] - decl.proof_indent,
                end_line=r["endLine"] - first,
                end_col=r["endCol"] - decl.proof_indent,
                kind=r["kind"],
                before=r["before"],
                after=r["after"],
            )
        )
    return out


class GoalExtractor:
    """Runs `lean/GoalStates.lean` on a declaration's file; results are cached on disk."""

    def __init__(self, project_dir: Path, timeout: int = 900, lake: str = "lake", cache_dir: Path | None = None):
        self.project_dir = Path(project_dir)
        self.timeout = timeout
        self.lake = lake
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def states(self, decl: Declaration) -> ProofStates:
        source = decl.original_source
        key = hashlib.sha256(source.encode()).hexdigest()
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path and path.exists():
            return ProofStates.from_json(path.read_text(encoding="utf-8"))
        result = self._run(decl, source)
        if path and not result.error:
            atomic_write_text(path, result.to_json())
        return result

    def _run(self, decl: Declaration, source: str) -> ProofStates:
        first = proof_first_line(decl)
        last = source.count("\n") + 1
        with tempfile.NamedTemporaryFile("w", suffix=".lean", dir=self.project_dir, delete=False, encoding="utf-8") as fh:
            fh.write(source)
            tmp = Path(fh.name)
        try:
            proc = subprocess.run(
                [self.lake, "env", "lean", "--run", SCRIPT, str(tmp), str(first), str(last)],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return ProofStates(error=f"goal extraction timed out after {self.timeout}s")
        finally:
            tmp.unlink(missing_ok=True)
        tactics = parse_records(proc.stdout, decl)
        if proc.returncode != 0 or not tactics:
            return ProofStates(tactics, error=(proc.stderr or proc.stdout)[-2000:] or "no tactic states found")
        return ProofStates(tactics)


# ---------------------------------------------------------------- rendering for prompts


def _target_only(goal: str) -> str:
    """The case tag and the target of a pretty-printed goal (the target may span several lines)."""
    lines = goal.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("⊢")), len(lines))
    return "\n".join([line for line in lines[:start] if line.startswith("case ")] + lines[start:])


def render_goals(goals: list[str], limit: int = 1500) -> str:
    """Goals as prompt text. Long states keep only case tags and targets (`⊢ ...`)."""
    if not goals:
        return "no goals remain"
    text = "\n\n".join(goals)
    if len(text) <= limit:
        return text
    short = "\n\n".join(_target_only(g) for g in goals)
    if len(short) > limit:
        short = short[:limit] + " …"
    return short + "\n(hypotheses omitted for length)"


# Steps whose interesting state is the goal they start from, not what is left after them:
# case alternatives and focusing bullets (the case to prove), and `calc` steps (`_ = … := by`,
# whose inner proof starts from exactly the claim of the step).
_CASE_OPENERS = ("|", "·", ".", "case ", "next ", "_ ")


def step_view(node: ProofNode, states: ProofStates) -> tuple[str, list[str]] | None:
    """("goal of"|"after", goals) for one step, or None when no state is attached to it."""
    st = states.for_node(node)
    if st is None:
        return None
    if node.text.startswith(_CASE_OPENERS):
        return "goal of", st.before
    return "after", st.after


def annotate(node: ProofNode, states: ProofStates) -> str:
    """Goal before a node, and the goal after each of its direct sub-steps, for prompts."""
    own = states.for_node(node)
    before = own.before if own else (states.initial if node.line < 0 else None)
    parts = []
    if before is not None:
        parts.append(f"Goal before this part:\n```\n{render_goals(before)}\n```")
    steps = []
    for child in node.children:
        view = step_view(child, states)
        if view is not None:
            label, goals = view
            steps.append(f"{label} `{child.text}`:\n```\n{render_goals(goals, limit=600)}\n```")
    if steps:
        parts.append("Goals along the steps of this part:\n\n" + "\n\n".join(steps))
    return "\n\n".join(parts)
