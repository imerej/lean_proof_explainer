"""Goal-state extraction and the `grounded` strategy. Lean-dependent tests skip without `lake`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lpe.explain import explain
from lpe.extract import find_declaration, parse_proof_tree
from lpe.goals import GoalExtractor, ProofStates, TacticState, annotate, parse_records, proof_first_line, render_goals
from lpe.llm import LLM

FIXTURE = Path(__file__).parent / "fixtures" / "Toy.lean"
PROJECT = Path(__file__).resolve().parent.parent / "lean"
needs_lean = pytest.mark.skipif(shutil.which("lake") is None or not PROJECT.exists(), reason="needs lake")


def _record(line, col, kind, before, after, end_line=None, end_col=0):
    return json.dumps(
        {"line": line, "col": col, "endLine": end_line or line, "endCol": end_col, "kind": kind,
         "before": before, "after": after}
    )


def test_proof_first_line_points_into_spliced_source():
    d = find_declaration(FIXTURE, "Toy.le_add_of_le")
    lines = d.original_source.splitlines()
    assert lines[proof_first_line(d) - 1].strip() == d.proof.splitlines()[0]


def test_parse_records_converts_to_proof_coordinates():
    d = find_declaration(FIXTURE, "Toy.le_add_of_le")
    first = proof_first_line(d)
    out = "\n".join(
        [
            _record(first - 1, 58, "by", ["G"], []),  # header line: dropped
            _record(first, d.proof_indent, "Lean.Parser.Tactic.tacticSeq", ["G"], []),
            _record(first, d.proof_indent, "Lean.Parser.Tactic.tacticHave__", ["G"], ["G'"]),
            "some stray line",
            _record(first + 1, d.proof_indent, "Lean.Parser.Tactic.exact", ["G'"], []),
        ]
    )
    recs = parse_records(out, d)
    assert [(r.line, r.col, r.kind.split(".")[-1]) for r in recs] == [
        (0, 0, "tacticSeq"),
        (0, 0, "tacticHave__"),
        (1, 0, "exact"),
    ]
    states = ProofStates(recs)
    assert states.initial == ["G"]
    assert states.at(0).kind.endswith("tacticHave__")  # wrappers lose to the real tactic
    assert states.to_json() and ProofStates.from_json(states.to_json()).tactics == recs


def test_render_goals_shortens_long_states():
    assert render_goals([]) == "no goals remain"
    hyps = "\n".join(f"h{i} : x{i} = {i}" for i in range(200))
    text = render_goals([f"case pos\n{hyps}\n⊢ P"], limit=300)
    assert "case pos" in text and "⊢ P" in text and "h5 :" not in text and "omitted" in text
    wrapped = render_goals([f"{hyps}\n⊢ a * b =\n    b * a"], limit=300)
    assert wrapped.startswith("⊢ a * b =\n    b * a")  # a target spanning two lines is kept whole


def test_annotate_shows_case_goals_and_step_results():
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    root = parse_proof_tree(d.proof)
    induction = root.children[0]
    zero, succ = induction.children
    states = ProofStates(
        [
            TacticState(induction.line, induction.column, 6, 0, "Lean.Parser.Tactic.induction", ["⊢ m + n = n + m"], []),
            TacticState(zero.line, zero.column, zero.line, 9, "null", ["case zero\n⊢ m + 0 = 0 + m"], []),
            TacticState(succ.line, succ.column, succ.line, 9, "null", ["case succ\n⊢ S"], []),
        ]
    )
    text = annotate(induction, states)
    assert "Goal before this part" in text and "m + n = n + m" in text
    assert "goal of `| zero => simp`" in text and "m + 0 = 0 + m" in text  # case goal, not "no goals"
    assert annotate(root, states).startswith("Goal before this part")  # root falls back to the initial goal


def test_grounded_strategy_puts_goal_states_in_prompts():
    d = find_declaration(FIXTURE, "Toy.le_add_of_le")
    root = parse_proof_tree(d.proof)
    have, exact = root.children
    states = ProofStates(
        [
            TacticState(have.line, have.column, have.line, 40, "Lean.Parser.Tactic.tacticHave__", ["h : a ≤ b\n⊢ a ≤ b + c"],
                        ["h : a ≤ b\nhb : b ≤ b + c\n⊢ a ≤ b + c"]),
            TacticState(exact.line, exact.column, exact.line, 20, "Lean.Parser.Tactic.exact", ["…"], []),
        ]
    )
    llm = LLM("mock:x", cache_dir=None)
    prompts: list[str] = []
    llm.set_mock(lambda system, messages: prompts.append(messages[0]["content"]) or "expl")
    ex = explain(d, "grounded", llm, states=states)
    assert ex.strategy == "grounded" and ex.calls == 1  # two leaves under the root: one call
    assert "hb : b ≤ b + c" in prompts[0] and "Goal before this part" in prompts[0]

    plain: list[str] = []
    llm.set_mock(lambda system, messages: plain.append(messages[0]["content"]) or "expl")
    explain(d, "recursive", llm)
    assert "Goal before" not in plain[0]  # the plain recursive pass is unchanged


def test_grounded_without_states_is_an_error():
    d = find_declaration(FIXTURE, "Toy.le_add_of_le")
    llm = LLM("mock:x", cache_dir=None)
    llm.set_mock(lambda s, m: "x")
    with pytest.raises(ValueError, match="goal states"):
        explain(d, "grounded", llm)
    with pytest.raises(ValueError, match="boom"):
        explain(d, "grounded", llm, states=ProofStates(error="boom"))


@needs_lean
def test_extractor_reads_real_goal_states(tmp_path):
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    extractor = GoalExtractor(PROJECT, timeout=300, cache_dir=tmp_path)
    states = extractor.states(d)
    assert not states.error, states.error
    assert states.initial == ["m n : Nat\n⊢ m + n = n + m"]
    root = parse_proof_tree(d.proof)
    succ = root.children[0].children[1]
    have, rw, omega = succ.children
    assert "case succ" in states.for_node(succ).before[0]
    assert "h : m + (k + 1) = m + k + 1" in states.for_node(have).after[0]
    assert states.for_node(rw).after[0].endswith("⊢ k + m + 1 = k + 1 + m")
    assert states.for_node(omega).after == []
    assert list(tmp_path.glob("*.json"))  # cached
    assert extractor.states(d).tactics == states.tactics


# ------------------------------------------------------------ end to end, no Lean, no API


def test_run_with_grounded_strategy_offline(tmp_path, monkeypatch, capsys):
    from lpe import cli
    from lpe.lean_check import CheckResult

    project = tmp_path / "lean"
    mathlib = project / ".lake" / "packages" / "mathlib"
    mathlib.mkdir(parents=True)
    shutil.copy(FIXTURE, mathlib / "Toy.lean")
    bench = tmp_path / "bench.toml"
    bench.write_text(
        '[[theorem]]\nname = "Toy.add_comm\'"\nfile = "Toy.lean"\n\n'
        '[[theorem]]\nname = "Toy.le_add_of_le"\nfile = "Toy.lean"\n',
        encoding="utf-8",
    )

    class FakeChecker:
        def __init__(self, *args, **kwargs):
            pass

        def check(self, decl, proof):
            ok = proof.split() == decl.proof.split()
            return CheckResult(ok, "ok" if ok else "error", "" if ok else "error: type mismatch")

    class FakeExtractor:
        """States for add_comm' only; le_add_of_le simulates a failed extraction."""

        def __init__(self, *args, **kwargs):
            pass

        def states(self, decl):
            if "add_comm'" not in decl.name:
                return ProofStates(error="elaboration failed")
            return ProofStates([TacticState(0, 0, 6, 9, "Lean.Parser.Tactic.induction", ["GOAL-MARKER"], [])])

    def explainer(system, messages):
        return "GROUNDED" if "GOAL-MARKER" in messages[0]["content"] else "PLAIN"

    def rebuilder(system, messages):
        user = messages[0]["content"]
        if "GROUNDED" in user:
            return f"```lean\n{find_declaration(FIXTURE, 'Toy.add_comm' + chr(39)).proof}\n```"
        return "```lean\nrfl\n```"

    real_llm = cli.LLM

    def fake_llm(spec, cache_dir=None):
        llm = real_llm(spec, cache_dir=None)
        llm.set_mock(explainer if spec == "mock:explainer" else rebuilder)
        return llm

    monkeypatch.setattr(cli, "LLM", fake_llm)
    monkeypatch.setattr(cli, "LeanChecker", FakeChecker)
    monkeypatch.setattr(cli, "GoalExtractor", FakeExtractor)
    out = tmp_path / "results.jsonl"
    cli.main([
        "--project", str(project), "run", str(bench),
        "--explainer", "mock:explainer", "--reconstructor", "mock:rebuilder",
        "--strategies", "recursive,grounded", "--mask", "off", "--feedback", "0",
        "--out", str(out), "--cache", str(tmp_path / "cache" / "llm"),
    ])  # fmt: skip
    printed = capsys.readouterr().out
    assert "grounded   skipped, no goal states: elaboration failed" in printed
    rows = {(r["theorem"], r["strategy"]): r for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    assert set(rows) == {("Toy.add_comm'", "recursive"), ("Toy.le_add_of_le", "recursive"), ("Toy.add_comm'", "grounded")}
    assert rows[("Toy.add_comm'", "grounded")]["success"]
    assert not rows[("Toy.add_comm'", "recursive")]["success"]

    cli.main(["report", str(out)])
    assert "| grounded | no | 1 | 1 | 100% |" in capsys.readouterr().out

    cli.main(["--project", str(project), "goals", str(bench), "--theorem", "Toy.add_comm'"])
    shown = capsys.readouterr().out
    assert "GOAL-MARKER" in shown and "induction n with" in shown
