"""Rebuild a Lean proof from the statement and an explanation, then check it with Lean."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import Declaration
from .lean_check import CheckResult, LeanChecker
from .llm import LLM

SYSTEM = """You write Lean 4 proofs that compile against Mathlib.
You are given a theorem statement and, possibly, an informal explanation of a proof.
Follow the explanation's strategy. Reply with ONLY the tactic block that goes after `:= by`, \
inside one ```lean code block. Do not restate the theorem. Never use `sorry`, `admit` or `native_decide`."""

FEEDBACK = """Your previous attempt failed to compile.

Attempt:
```lean
{proof}
```

Lean said:
```
{errors}
```

Fix the proof. Reply with ONLY the corrected tactic block in one ```lean code block."""


@dataclass
class Attempt:
    proof: str
    result: CheckResult


@dataclass
class Reconstruction:
    success: bool
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def first_success(self) -> int | None:
        """1-based index of the first compiling attempt."""
        for i, a in enumerate(self.attempts, 1):
            if a.result.ok:
                return i
        return None


def build_prompt(decl: Declaration, explanation: str) -> str:
    parts = [f"Theorem (the proof goes after `:= by`):\n```lean\n{decl.statement_in_context}\n```"]
    if explanation.strip():
        parts.append(f"Informal explanation of a proof:\n\n{explanation}")
    else:
        parts.append("No explanation is available; find a proof yourself.")
    return "\n\n".join(parts)


def extract_proof(reply: str) -> str:
    blocks = re.findall(r"```(?:lean4?|)\s*\n(.*?)```", reply, flags=re.S)
    code = max(blocks, key=len) if blocks else reply
    m = re.search(r":=\s*by\b", code)
    if m and re.search(r"\b(theorem|lemma|example)\b", code[: m.start()]):
        code = code[m.end() :]
    lines = code.strip("\n").splitlines()
    widths = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    cut = min(widths) if widths else 0
    return "\n".join(line[cut:] for line in lines).strip("\n")


def reconstruct(
    decl: Declaration,
    explanation: str,
    llm: LLM,
    checker: LeanChecker,
    samples: int = 1,
    feedback_rounds: int = 1,
    temperature: float = 0.7,
) -> Reconstruction:
    """Up to `samples` independent tries, each with up to `feedback_rounds` repairs from Lean errors."""
    prompt = build_prompt(decl, explanation)
    rec = Reconstruction(False)
    for s in range(samples):
        user = prompt
        for r in range(feedback_rounds + 1):
            reply = llm.chat(SYSTEM, user, temperature=temperature if samples > 1 else 0.0, sample=s * 100 + r)
            proof = extract_proof(reply)
            result = checker.check(decl, proof)
            rec.attempts.append(Attempt(proof, result))
            if result.ok:
                rec.success = True
                return rec
            user = prompt + "\n\n" + FEEDBACK.format(proof=proof, errors=result.errors[:3000])
    return rec
