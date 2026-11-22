"""Locate a tactic-proved declaration in a Lean source file and split its proof
into a tree of steps.

The tree is built from indentation only. That is deliberately crude: it needs no
Lean metaprogramming, and for the tactic proofs found in Mathlib the indentation
structure (``have ... := by`` blocks, ``·`` bullets, ``case`` blocks, ``calc``
steps) is a faithful enough proxy for the logical structure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DECL_KEYWORDS = ("theorem", "lemma")
_DECL_RE = r"^(?P<indent>[ \t]*)(?:(?:private|protected|nonrec|noncomputable)\s+)*(?:{kw})\s+{name}(?=[\s:({{\[]|$)"


@dataclass
class Declaration:
    """A declaration split into the parts the experiment needs."""

    name: str
    path: Path
    prefix: str  # everything in the file before the declaration (imports, namespaces, variables, ...)
    header: str  # attributes/docstring + `theorem name ... :=` up to and including `by`
    statement: str  # header without docstring/attributes, for prompts
    proof: str  # the tactic block after `by`, dedented
    proof_indent: int  # indentation used when splicing a proof back in
    start_line: int  # 1-based line of the `theorem` keyword in the original file

    def splice(self, new_proof: str) -> str:
        """Source of the file truncated after this declaration, with `new_proof` in place."""
        body = indent(new_proof, self.proof_indent)
        return f"{self.prefix}{self.header}\n{body}\n"

    @property
    def context(self) -> str:
        """`namespace`/`section`/`open`/`variable` commands still in scope at the declaration.

        Statements in Mathlib often use variables declared earlier in the file, so a
        reader of the statement alone would not know what `P`, `m` or `s` are.
        """
        kept: list[str] = []
        in_variable = False
        for line in _strip_block_comments(self.prefix).splitlines():
            s = line.strip()
            if in_variable and line[:1].isspace() and s:
                kept.append(line)
                continue
            in_variable = False
            if re.match(r"(variable|open|namespace|section|end|universe)\b", s) or re.match(
                r"(noncomputable\s+)?section\b", s
            ):
                kept.append(line)
                in_variable = s.startswith("variable")
        return _drop_closed_scopes(kept)

    @property
    def statement_in_context(self) -> str:
        ctx = self.context.strip()
        return f"{ctx}\n\n{self.statement}" if ctx else self.statement

    @property
    def original_source(self) -> str:
        return self.splice(self.proof)

    @property
    def line_count(self) -> int:
        return len([line for line in self.proof.splitlines() if line.strip()])


@dataclass
class ProofNode:
    """One tactic step; `children` are the lines indented under it."""

    text: str  # the step's own first line (e.g. `have h : x ≤ y := by`)
    children: list["ProofNode"] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def source(self, depth: int = 0) -> str:
        pad = "  " * depth
        lines = [pad + self.text]
        for child in self.children:
            lines.append(child.source(depth + 1))
        return "\n".join(lines)

    def size(self) -> int:
        return 1 + sum(child.size() for child in self.children)


def indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


def dedent(lines: list[str]) -> list[str]:
    widths = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    cut = min(widths) if widths else 0
    return [line[cut:] if line.strip() else "" for line in lines]


def _strip_block_comments(text: str) -> str:
    return re.sub(r"/-.*?-/", "", text, flags=re.S)


def _drop_closed_scopes(lines: list[str]) -> str:
    """Remove `section`/`namespace` blocks (and their variables) that were closed by `end`."""
    stack: list[list[str]] = [[]]
    for line in lines:
        s = line.strip()
        if re.match(r"(noncomputable\s+)?section\b|namespace\b", s):
            stack.append([line])
        elif re.match(r"end\b", s):
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(line)
    return "\n".join(line for frame in stack for line in frame)


def _strip_comments(line: str) -> str:
    return line.split("--", 1)[0].rstrip()


def find_declaration(path: Path, name: str) -> Declaration:
    """Find `theorem name`/`lemma name` in `path` and split it.

    `name` may be the short name or a dotted name whose last component appears in the file.
    Raises ValueError when the declaration is missing or is not a tactic proof.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Try the longest dotted suffix first: `Commute.add_pow` may be written as such in the file,
    # while `Nat.choose_mul` is usually written `choose_mul` inside `namespace Nat`.
    parts = name.split(".")
    start = None
    for k in range(len(parts)):
        written = ".".join(parts[k:])
        pattern = re.compile(_DECL_RE.format(kw="|".join(DECL_KEYWORDS), name=re.escape(written)))
        start = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
        if start is not None:
            break
    if start is None:
        raise ValueError(f"{name}: no `theorem`/`lemma` named {parts[-1]!r} in {path}")
    base_indent = len(lines[start]) - len(lines[start].lstrip())

    # Pull attached docstring / attributes into the header so the prefix stays clean.
    head = start
    while head > 0:
        prev = lines[head - 1].strip()
        if prev.startswith("@["):
            head -= 1
        elif prev.endswith("-/") and not prev.startswith("/-!"):
            k = head - 1
            while k > 0 and "/-" not in lines[k]:
                k -= 1
            if lines[k].lstrip().startswith("/-!"):  # module doc, not attached to this decl
                break
            head = k
        else:
            break

    # The declaration ends at the next non-blank line indented at or left of the keyword.
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and (len(line) - len(line.lstrip())) <= base_indent and not line.lstrip().startswith("--"):
            break
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    decl_lines = lines[start:end]

    # Split at the first `:= by` (end of line or followed by tactics on the same line).
    for j, line in enumerate(decl_lines):
        code = _strip_comments(line)
        m = re.search(r":=\s*by\b", code)
        if m:
            break
    else:
        raise ValueError(f"{name}: not a tactic proof (no `:= by` found)")
    if any(_strip_comments(prev).lstrip().startswith("|") for prev in decl_lines[1 : j + 1]):
        raise ValueError(f"{name}: proved by pattern matching, not a single tactic block")
    if j and re.search(r":=\s*$|:=\s*\S", "\n".join(_strip_comments(p) for p in decl_lines[:j])):
        raise ValueError(f"{name}: the first `:= by` is nested inside a term proof")

    by_end = m.end()
    header_lines = decl_lines[:j] + [line[:by_end]]
    rest_same_line = line[by_end:].strip()
    proof_lines = ([rest_same_line] if rest_same_line else []) + decl_lines[j + 1 :]
    if not any(p.strip() for p in proof_lines):
        raise ValueError(f"{name}: empty proof")

    body_widths = [len(p) - len(p.lstrip()) for p in decl_lines[j + 1 :] if p.strip()]
    proof_indent = min(body_widths) if body_widths else base_indent + 2

    proof = "\n".join(dedent(proof_lines)).strip("\n")
    statement = "\n".join(dedent(header_lines)).strip()
    return Declaration(
        name=name,
        path=path,
        prefix="\n".join(lines[:head]) + ("\n" if head else ""),
        header="\n".join(lines[head:start] + header_lines),
        statement=statement,
        proof=proof,
        proof_indent=proof_indent,
        start_line=start + 1,
    )


def parse_proof_tree(proof: str) -> ProofNode:
    """Build a tree from a dedented tactic block. The root's text is a placeholder."""
    root = ProofNode(text="<proof>")
    stack: list[tuple[int, ProofNode]] = [(-1, root)]
    last: ProofNode | None = None
    for raw in proof.splitlines():
        if not raw.strip() or raw.strip().startswith("--"):
            continue
        width = len(raw) - len(raw.lstrip())
        text = raw.strip()
        # A line that continues an unfinished term (open brackets, trailing operator) joins it.
        if last is not None and _unfinished(last.text):
            last.text += " " + text
            continue
        # `| case =>` alternatives belong to the `induction/cases/match ... with` line above them,
        # even though Lean style puts them at the same indentation.
        # They are pushed with width + 0.5 so a sibling `|` closes them but the `with` line stays open.
        if text.startswith("|"):
            while stack[-1][0] > width + 0.5 or (stack[-1][0] == width + 0.5 and stack[-1][1].text.startswith("|")):
                stack.pop()
            parent_is_with = stack[-1][0] == width and _strip_comments(stack[-1][1].text).endswith("with")
            key = width + 0.5 if parent_is_with else width
            if not parent_is_with:
                while stack[-1][0] >= width:
                    stack.pop()
        else:
            while stack[-1][0] >= width:
                stack.pop()
            key = width
        node = ProofNode(text=text)
        stack[-1][1].children.append(node)
        stack.append((key, node))
        last = node
    return root


def _unfinished(text: str) -> bool:
    code = _strip_comments(text)
    depth = sum(code.count(o) for o in "([{⟨") - sum(code.count(c) for c in ")]}⟩")
    return depth > 0 or code.rstrip().endswith((",", ":", "<;>", "←", "=", "+", "*", "∧", "∨", "→", "↔"))


IDENT_RE = re.compile(r"(?<![\w.'])([A-Za-z_][\w']*(?:\.[A-Za-z_][\w']*)+|[a-z][a-z0-9']*(?:_[a-z0-9']+)+)(?![\w'])")


def lemma_identifiers(proof: str) -> set[str]:
    """Identifiers in a proof that look like library lemma names.

    Dotted names (`Nat.succ_le_iff`) and snake_case names with an underscore
    (`mul_comm`) are counted; single words (`h`, `simp`, `x`) are not.
    """
    return {m.group(1) for m in IDENT_RE.finditer(proof)}
