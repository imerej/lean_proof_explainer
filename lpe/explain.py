"""Explanation strategies.

  none       no explanation; the reconstructor sees only the statement (baseline)
  flat       one call: the whole proof in, one explanation out
  recursive  bottom-up over the proof tree. Each composite step is explained from
             its own code plus the already-written explanations of its sub-steps,
             with those sub-steps collapsed to one line. The root's explanation is
             the final one. This is the proof analogue of the "explain the position
             from the explanations of the positions after it" update used to train
             chess explainers.
  grounded   the recursive pass, with Lean's goal state before each explained part and
             after each of its sub-steps added to every prompt (see `goals.py`). Needs the
             states from `GoalExtractor`; the tree is the same as for `recursive`, so the two
             differ only in what the explainer sees.
  contrastive  the grounded pass, plus Lean's verdict on routine alternatives (`simp`, `omega`,
             `linarith`, ...) tried on the goal each explained part starts from (see
             `contrast.py`), so the explainer can say why the step is needed, or that it is
             routine. Same tree and goal states as `grounded`, so the pair isolates the verdicts.
  decoy      control: the flat explanation of a *different* theorem from the same
             benchmark. Rebuild success with a decoy measures how much an explanation
             helps through its form alone (a longer prompt, a confident tone, generic
             proof advice) rather than through its content. A strategy that does not
             beat `decoy` is not conveying anything about the proof. See `decoy_explanation`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import Declaration, ProofNode, lemma_identifiers, parse_proof_tree
from .contrast import Contrasts, probe_targets, render_verdicts, verdict_summary
from .goals import ProofStates, annotate
from .llm import LLM

STYLE_RULES = """\
Write for a mathematician who does not read Lean.
- Use ordinary mathematical language and LaTeX-style notation ($...$).
- Do not quote Lean code, tactic names (simp, linarith, rw, omega, ...), or library lemma names. \
Describe the fact a lemma states instead of naming it.
- Say WHY each move is made: what it reduces the goal to, and why the obvious alternatives would not work as well.
- State every intermediate claim precisely enough that a reader could re-prove it."""

FINAL_FORMAT = """\
Structure the answer as:
1. **Statement.** The theorem in words.
2. **Idea.** The key idea of the proof in two or three sentences, and why this route was chosen.
3. **Proof outline.** Numbered steps. For each: the claim, and the argument for it in a sentence or two.
4. **Where the difficulty is.** The step a reader would most likely get stuck on, and the trick that resolves it."""

FLAT_SYSTEM = f"You explain formal proofs written in Lean 4 with Mathlib.\n\n{STYLE_RULES}\n\n{FINAL_FORMAT}"

NODE_SYSTEM = f"""You explain one part of a formal Lean 4 proof. Sub-steps that were already explained are \
collapsed to a single line marked ⟦step k⟧, and their explanations are given to you.
Explain what this part establishes and how, building on the sub-step explanations rather than repeating them.
Be concise: a short paragraph, or a few numbered points for a long part.
When Lean's goal states are given, use them to state precisely what is known and what remains \
to be shown at each point, but translate them into ordinary mathematics; do not copy them.

{STYLE_RULES}"""

ROOT_SYSTEM = f"""You explain a formal Lean 4 proof. Sub-steps that were already explained are collapsed \
to a single line marked ⟦step k⟧, and their explanations are given to you. Combine them into one coherent \
explanation of the whole proof. Keep every intermediate claim; drop Lean-specific bookkeeping.
When Lean's goal states are given, use them to state intermediate claims precisely, in ordinary mathematics.

{STYLE_RULES}

{FINAL_FORMAT}"""

CONTRAST_RULES = """\
When Lean's verdicts on routine alternatives are given for this part, use them to explain why the part \
is needed. If no routine method closes the goal, say in mathematical terms what makes it non-routine \
(for instance: it is not linear arithmetic, it needs an induction, it needs a specific identity or an \
auxiliary bound); if one does close it, say briefly that the claim is routine and do not invent a deeper \
reason. Report only what the verdicts show; do not name the tactics."""


@dataclass
class Explanation:
    strategy: str
    text: str
    calls: int = 0
    trace: list[dict] = field(default_factory=list)  # per-node explanations for the recursive strategy


def explain(
    decl: Declaration,
    strategy: str,
    llm: LLM | None,
    min_children: int = 1,
    states: ProofStates | None = None,
    contrasts: Contrasts | None = None,
) -> Explanation:
    if strategy == "none":
        return Explanation("none", "")
    if llm is None:
        raise ValueError(f"strategy {strategy!r} needs an explainer model")
    if strategy == "flat":
        user = f"Theorem:\n```lean\n{decl.statement_in_context}\n```\n\nProof:\n```lean\n{decl.proof}\n```"
        return Explanation("flat", llm.chat(FLAT_SYSTEM, user), calls=1)
    if strategy == "recursive":
        return _recursive(decl, llm, min_children)
    if strategy == "grounded":
        if states is None or not states.tactics:
            reason = states.error if states is not None else "no goal states given"
            raise ValueError(f"strategy 'grounded' needs goal states for {decl.name}: {reason}")
        return _recursive(decl, llm, min_children, states=states, name="grounded")
    if strategy == "contrastive":
        if states is None or not states.tactics:
            reason = states.error if states is not None else "no goal states given"
            raise ValueError(f"strategy 'contrastive' needs goal states for {decl.name}: {reason}")
        if contrasts is None or contrasts.error:
            reason = contrasts.error if contrasts is not None else "no verdicts given"
            raise ValueError(f"strategy 'contrastive' needs alternative-tactic verdicts for {decl.name}: {reason}")
        return _recursive(decl, llm, min_children, states=states, contrasts=contrasts, name="contrastive")
    if strategy == "decoy":
        raise ValueError("the decoy strategy needs a donor theorem; use decoy_explanation()")
    raise ValueError(f"unknown strategy {strategy!r}")


def decoy_explanation(donor: Declaration, llm: LLM | None) -> Explanation:
    """The flat explanation of `donor`, to be shown to the rebuilder of another theorem.

    Uses exactly the flat prompt, so it shares the cache with the donor's own `flat` row and
    has the same length and style distribution as a real flat explanation.
    """
    if llm is None:
        raise ValueError("strategy 'decoy' needs an explainer model")
    ex = explain(donor, "flat", llm)
    return Explanation("decoy", ex.text, calls=ex.calls, trace=[{"node": "donor", "name": donor.name}])


def pick_donor(files: list[str], index: int) -> int:
    """Index of the theorem whose explanation serves as the decoy for theorem `index`.

    The next theorem (cyclically) from a different file, so that the donor is unlikely to be a
    sibling lemma whose explanation happens to help; falls back to the next theorem when every
    theorem comes from the same file. Deterministic, so reruns pair the same theorems.
    """
    n = len(files)
    if n < 2:
        raise ValueError("the decoy strategy needs at least two theorems in the benchmark")
    for step in range(1, n):
        j = (index + step) % n
        if files[j] != files[index]:
            return j
    return (index + 1) % n


def _recursive(
    decl: Declaration,
    llm: LLM,
    min_children: int,
    states: ProofStates | None = None,
    name: str = "recursive",
    contrasts: Contrasts | None = None,
) -> Explanation:
    root = parse_proof_tree(decl.proof)
    targets = probe_targets(decl, states, min_children) if contrasts is not None and states else {}
    node_system = f"{NODE_SYSTEM}\n\n{CONTRAST_RULES}" if contrasts is not None else NODE_SYSTEM
    root_system = f"{ROOT_SYSTEM}\n\n{CONTRAST_RULES}" if contrasts is not None else ROOT_SYSTEM

    def probe_for(node: ProofNode):
        target = targets.get((node.line, node.column))
        probe = contrasts.at(*target) if contrasts is not None and target else None
        tried = probe is not None and any(v.status != "unavailable" for v in probe.verdicts)
        return probe if tried else None

    def contrast_text(node: ProofNode) -> str:
        probe = probe_for(node)
        if probe is None:
            return ""
        return f"\n\nLean's verdict on routine alternatives at the start of this part:\n\n{render_verdicts(probe)}"

    def record(entry: dict, node: ProofNode) -> dict:
        probe = probe_for(node)
        if probe is not None:
            entry["contrast"] = verdict_summary(probe)
        return entry

    out = Explanation(name, "")
    counter = [0]

    def visit(node: ProofNode, depth: int) -> tuple[str, str]:
        """Return (collapsed code for this node, explanation or '' if not explained separately)."""
        child_codes: list[str] = []
        notes: list[str] = []
        for child in node.children:
            code, expl = visit(child, depth + 1)
            child_codes.append(code)
            if expl:
                notes.append(expl)
        is_root = node is root
        worth_explaining = is_root or len(node.children) >= min_children
        own_code = node.text
        body = "\n".join("  " + line for code in child_codes for line in code.splitlines())
        code_here = body if is_root else f"{own_code}\n{body}" if body else own_code
        if not worth_explaining:
            return code_here, ""

        notes_text = "\n\n".join(notes) if notes else "(none)"
        goal_text = f"\n\nLean's goal states for this part:\n\n{annotate(node, states)}" if states else ""
        goal_text += contrast_text(node)
        if is_root:
            user = (
                f"Theorem:\n```lean\n{decl.statement_in_context}\n```\n\n"
                f"Proof, with sub-steps collapsed:\n```lean\n{_dedent(code_here)}\n```\n\n"
                f"Explanations of the collapsed sub-steps:\n\n{notes_text}{goal_text}"
            )
            text = llm.chat(root_system, user)
            out.calls += 1
            out.trace.append(record({"node": "root", "code": code_here, "explanation": text}, node))
            return code_here, text

        counter[0] += 1
        k = counter[0]
        user = (
            f"The theorem being proved:\n```lean\n{decl.statement_in_context}\n```\n\n"
            f"This part of the proof:\n```lean\n{code_here}\n```\n\n"
            f"Explanations of its collapsed sub-steps:\n\n{notes_text}{goal_text}"
        )
        text = llm.chat(node_system, user)
        out.calls += 1
        out.trace.append(record({"node": f"step {k}", "code": code_here, "explanation": text}, node))
        return f"{own_code}  -- ⟦step {k}⟧ (details collapsed)", f"⟦step {k}⟧ {text}"

    _, out.text = visit(root, 0)
    return out


def _dedent(text: str) -> str:
    lines = text.splitlines()
    widths = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    cut = min(widths) if widths else 0
    return "\n".join(line[cut:] for line in lines)


# ---------------------------------------------------------------- leakage


@dataclass
class Leakage:
    identifiers_total: int
    identifiers_leaked: int
    code_blocks: int
    notation: list[str] = field(default_factory=list)  # Lean-only notation found in the text

    @property
    def rate(self) -> float:
        return self.identifiers_leaked / self.identifiers_total if self.identifiers_total else 0.0


def leakage(decl: Declaration, text: str) -> Leakage:
    """How much of the proof's Lean vocabulary survives verbatim in the explanation.

    A high rate means the explanation may be a disguised copy of the code, so
    reconstruction success would say little about understanding.
    """
    idents = lemma_identifiers(decl.proof)
    leaked = {i for i in idents if any(_mentions(text, form) for form in _forms(i))}
    return Leakage(len(idents), len(leaked), text.count("```"), notation_marks(text))


# Notation that belongs to Lean's goal display rather than to mathematical writing. Goal states
# (`grounded`, `contrastive`) put it in front of the explainer, and copying it is a form of leakage
# that lemma-name matching does not see: the turnstile, coercion arrows, subscripted hypothesis
# names (`h₁`, `hx₂`) and `.succ`-style projections. Angle brackets are not counted, since
# mathematicians use them too (inner products, generated subgroups).
_NOTATION = {
    "turnstile": re.compile(r"⊢"),
    "coercion": re.compile(r"[↑⇑↥]"),
    "hypothesis name": re.compile(r"(?<![\w\\])h[A-Za-z]*[₀-₉]+(?![\w'])"),
    "projection": re.compile(r"\b\w+\.(?:succ|pred|val|toNat|card|natAbs|symm|trans|mp|mpr)\b"),
}


def notation_marks(text: str) -> list[str]:
    """Kinds of Lean-only notation that occur in `text`, sorted (each kind listed once)."""
    return sorted(kind for kind, rx in _NOTATION.items() if rx.search(text))


def mask_identifiers(decl: Declaration, text: str) -> str:
    """Replace any library identifier from the original proof (and any code block) in `text`."""
    text = re.sub(r"```.*?```", "[code removed]", text, flags=re.S)
    for ident in sorted(lemma_identifiers(decl.proof), key=len, reverse=True):
        for form in _forms(ident):
            text = re.sub(rf"(?<![\w'\\]){re.escape(form)}(?![\w'])", "[lemma]", text)
    return text


def _forms(ident: str) -> list[str]:
    """The full name, plus the last component when it is a distinctive snake_case name."""
    last = ident.split(".")[-1]
    return [ident, last] if "_" in last and last != ident else [ident]


def _mentions(text: str, form: str) -> bool:
    # The backslash guard keeps LaTeX such as \le or \sum from counting as a mention.
    return re.search(rf"(?<![\w'\\]){re.escape(form)}(?![\w'])", text) is not None
