"""Tests. The Lean-dependent ones are skipped when `lake` is not on PATH."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lpe.explain import explain, leakage, mask_identifiers
from lpe.extract import find_declaration, lemma_identifiers, parse_proof_tree
from lpe.lean_check import LeanChecker, axioms_used, parse_messages
from lpe.llm import LLM
from lpe.reconstruct import extract_proof, reconstruct

FIXTURE = Path(__file__).parent / "fixtures" / "Toy.lean"
PROJECT = Path(__file__).resolve().parent.parent / "lean"
needs_lean = pytest.mark.skipif(shutil.which("lake") is None or not PROJECT.exists(), reason="needs lake")


# ------------------------------------------------------------ extraction


def test_find_declaration_splits_header_and_proof():
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    assert d.statement.startswith("theorem add_comm' (m n : Nat)")
    assert d.statement.endswith(":= by")
    assert d.proof.splitlines()[0] == "induction n with"
    assert "/-- Addition is commutative" in d.header  # docstring travels with the header
    assert "theorem le_add_of_le" not in d.original_source  # truncated after the declaration
    assert d.original_source.rstrip().endswith("omega")


def test_context_keeps_variables_in_scope():
    d = find_declaration(FIXTURE, "Toy.le_add_of_le")
    assert "variable {a b : Nat}" in d.context
    assert "namespace Toy" in d.context


def test_term_mode_proofs_are_rejected():
    with pytest.raises(ValueError, match="tactic proof"):
        find_declaration(FIXTURE, "Toy.term_mode")


def test_proof_tree_follows_indentation():
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    root = parse_proof_tree(d.proof)
    assert [c.text for c in root.children] == ["induction n with"]
    succ = root.children[0].children[1]
    assert succ.text.startswith("| succ")
    assert [c.text for c in succ.children][:1] == ["have h : m + (k + 1) = (m + k) + 1 := by"]
    assert succ.children[0].children[0].text == "rfl"


def test_lemma_identifiers_ignore_short_names():
    ids = lemma_identifiers("exact Nat.le_trans h hb\nrw [mul_comm, h]\nsimp")
    assert ids == {"Nat.le_trans", "mul_comm"}


# ------------------------------------------------------------ leakage


def test_leakage_and_masking():
    d = find_declaration(FIXTURE, "Toy.le_add_of_le")
    leaky = "Use Nat.le_trans with the fact le_add_right. Note $a \\le b$."
    assert leakage(d, leaky).identifiers_leaked == 2
    masked = mask_identifiers(d, leaky + "\n```lean\nexact foo\n```")
    assert "le_trans" not in masked and "le_add_right" not in masked
    assert "[code removed]" in masked
    assert "\\le b" in masked  # LaTeX untouched
    assert leakage(d, masked).identifiers_leaked == 0


# ------------------------------------------------------------ explanation


def _mock(responses: list[str]):
    calls: list[tuple[str, str]] = []

    def fn(system, messages):
        calls.append((system, messages[0]["content"]))
        return responses[min(len(calls) - 1, len(responses) - 1)]

    return fn, calls


def test_recursive_explanation_is_bottom_up():
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    llm = LLM("mock:x", cache_dir=None)
    fn, calls = _mock(["sub-explanation"])
    llm.set_mock(fn)
    ex = explain(d, "recursive", llm)
    # composite nodes: `have` (1 child), `| succ` (3 children), `induction` (2 children), root
    assert ex.calls == 4 == len(calls)
    assert "⟦step 1⟧" in calls[1][1]  # the parent sees its child collapsed
    assert "Combine them" in calls[-1][0]  # root prompt last
    assert ex.trace[-1]["node"] == "root"


def test_flat_and_none():
    d = find_declaration(FIXTURE, "Toy.le_add_of_le")
    llm = LLM("mock:x", cache_dir=None)
    llm.set_mock(lambda s, m: "flat explanation")
    assert explain(d, "flat", llm).text == "flat explanation"
    assert explain(d, "none", None).text == ""


def test_extract_proof_from_reply():
    reply = "Sure:\n```lean\ntheorem foo : True := by\n  trivial\n```"
    assert extract_proof(reply) == "trivial"
    assert extract_proof("```lean\n  intro h\n  exact h\n```") == "intro h\nexact h"


def test_cache_round_trip(tmp_path):
    llm = LLM("mock:x", cache_dir=tmp_path)
    llm.set_mock(lambda s, m: "hi")
    assert llm.chat("s", "u") == "hi"  # mock calls are never cached
    assert llm.usage.calls == 1


# ------------------------------------------------------------ Lean output parsing


def test_parse_messages_and_axioms():
    out = (
        "/tmp/x.lean:12:4: error: unsolved goals\n⊢ False\n"
        "/tmp/x.lean:20:0: warning: declaration uses 'sorry'\n"
        "'Toy.foo' depends on axioms: [propext, sorryAx]\n"
    )
    msgs = parse_messages(out)
    assert [(m.line, m.severity) for m in msgs] == [(12, "error"), (20, "warning")]
    assert axioms_used(out) == {"propext", "sorryAx"}
    assert axioms_used("'Toy.foo' does not depend on any axioms") == set()


# ------------------------------------------------------------ with Lean


@needs_lean
def test_checker_accepts_original_and_rejects_bad_proofs():
    checker = LeanChecker(PROJECT, timeout=300)
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    assert checker.check(d, d.proof).ok
    assert checker.check(d, "omega").ok  # any valid proof is fine
    bad = checker.check(d, "rfl")
    assert not bad.ok and bad.reason == "error" and bad.errors
    assert checker.check(d, "sorry").reason == "banned-token"
    # sneaking sorry past the token filter is still caught by #print axioms
    assert checker.check(d, "exact sorryAx _ false").reason == "axioms"


@needs_lean
def test_reconstruction_uses_lean_feedback():
    checker = LeanChecker(PROJECT, timeout=300)
    d = find_declaration(FIXTURE, "Toy.le_add_of_le")
    llm = LLM("mock:x", cache_dir=None)
    replies = ["```lean\nexact h\n```", "```lean\nexact Nat.le_trans h (Nat.le_add_right b c)\n```"]
    fn, calls = _mock(replies)
    llm.set_mock(fn)
    rec = reconstruct(d, "Go through b.", llm, checker, samples=1, feedback_rounds=1)
    assert rec.success and rec.first_success == 2
    assert "Lean said" in calls[1][1]


def test_notation_marks_flag_goal_display_but_not_latex():
    from lpe.explain import notation_marks

    d = find_declaration(FIXTURE, "Toy.le_add_of_le")
    copied = "From h₁ we get ⊢ ↑n ≤ m.succ, so hx₂ closes it."
    assert notation_marks(copied) == ["coercion", "hypothesis name", "projection", "turnstile"]
    assert leakage(d, copied).notation == notation_marks(copied)
    clean = r"Since $a \le b$ and $\langle x, y \rangle \ge 0$ (see Lemma 2.1), $h_1$ and $\hbar$ are fine."
    assert notation_marks(clean) == []
