"""Alternative-tactic verdicts and the `contrastive` strategy. Lean-dependent tests skip without `lake`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lpe.contrast import (
    ContrastRunner,
    Contrasts,
    Probe,
    Verdict,
    parse_output,
    probe_targets,
    render_verdicts,
    start_of_part,
    verdict_summary,
)
from lpe.explain import explain
from lpe.extract import find_declaration, parse_proof_tree
from lpe.goals import ProofStates, TacticState, proof_first_line
from lpe.llm import LLM

FIXTURE = Path(__file__).parent / "fixtures" / "Toy.lean"
PROJECT = Path(__file__).resolve().parent.parent / "lean"
needs_lean = pytest.mark.skipif(shutil.which("lake") is None or not PROJECT.exists(), reason="needs lake")


def _states_for_add_comm(d):
    """Hand-written states for Toy.add_comm' at the positions the real extractor reports."""
    root = parse_proof_tree(d.proof)
    induction = root.children[0]
    zero, succ = induction.children
    have, rw, omega = succ.children
    rfl = have.children[0]
    st = lambda n, before, after=(): TacticState(n.line, n.column, n.line, 9, "x", list(before), list(after))  # noqa: E731
    return root, ProofStates(
        [
            st(induction, ["⊢ m + n = n + m"]),
            st(zero, ["case zero\n⊢ m + 0 = 0 + m"]),
            st(succ, ["case succ\n⊢ m + (k + 1) = k + 1 + m"]),
            st(have, ["case succ\n⊢ m + (k + 1) = k + 1 + m"], ["case succ\nh : H\n⊢ G"]),
            st(rfl, ["⊢ m + (k + 1) = m + k + 1"]),
            st(rw, ["h : H\n⊢ G"]),
            st(omega, ["⊢ k + m + 1 = k + 1 + m"]),
        ]
    )


def test_start_of_part_uses_the_claim_of_a_have():
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    root = parse_proof_tree(d.proof)
    induction = root.children[0]
    succ = induction.children[1]
    have = succ.children[0]
    assert start_of_part(root, root) is induction
    assert start_of_part(induction, root) is induction
    assert start_of_part(succ, root) is succ  # a case alternative starts from its own case goal
    assert start_of_part(have, root) is have.children[0]  # `have … := by`: the goal of its first step


def test_probe_targets_pick_the_goal_each_part_starts_from():
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    root, states = _states_for_add_comm(d)
    induction = root.children[0]
    succ = induction.children[1]
    have = succ.children[0]
    targets = probe_targets(d, states)
    assert targets[(-1, 0)][2] == "⊢ m + n = n + m"
    assert targets[(have.line, have.column)][2] == "⊢ m + (k + 1) = m + k + 1"
    assert targets[(succ.line, succ.column)][2].startswith("case succ")
    zero = induction.children[0]
    assert (zero.line, zero.column) not in targets  # a leaf is not explained on its own
    assert set(probe_targets(d, states, min_children=3)) == {(-1, 0), (succ.line, succ.column)}


def test_parse_output_groups_by_position_and_goal():
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    first = proof_first_line(d)
    rec = lambda line, goal, tac, status, **kw: json.dumps(  # noqa: E731
        {"line": first + line, "col": d.proof_indent, "goal": goal, "tactic": tac, "status": status, **kw}
    )
    out = "\n".join(
        [
            rec(0, "G", "omega", "closed"),
            "noise",
            rec(0, "G", "simp", "failed", error="`simp` made no progress"),
            rec(0, "G2", "omega", "failed", error="omega could not prove the goal"),
        ]
    )
    probes = parse_output(out, d)
    assert [(p.line, p.col, p.goal, [v.tactic for v in p.verdicts]) for p in probes] == [
        (0, 0, "G", ["omega", "simp"]),
        (0, 0, "G2", ["omega"]),
    ]
    c = Contrasts(probes)
    assert c.at(0, 0, "G2").verdicts[0].status == "failed"
    assert c.at(0, 0, "missing") is None
    assert Contrasts.from_json(c.to_json()) == c


def test_render_verdicts_and_summary():
    probe = Probe(
        0,
        0,
        "⊢ P",
        [
            Verdict("omega", "failed", error="omega could not prove the goal:\na possible counterexample …"),
            Verdict("simp", "progress", goals=["x : ℕ\n⊢ Q x"]),
            Verdict("ring", "unchanged", goals=["⊢ P"]),
            Verdict("positivity", "unavailable", error="parse error: unknown tactic"),
        ],
    )
    text = render_verdicts(probe)
    assert "- `omega` fails: omega could not prove the goal\n" in text and "counterexample" not in text
    assert "positivity" not in text  # not importable in this file: not a verdict about the goal
    assert "- `simp` does not finish; leaves ⊢ Q x" in text
    assert "- `ring` runs but changes nothing" in text
    assert verdict_summary(probe).startswith("non-routine")
    probe.verdicts.append(Verdict("linarith", "closed"))
    assert verdict_summary(probe) == "routine: closed directly by linarith"


def test_contrastive_strategy_adds_verdicts_to_prompts():
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    root, states = _states_for_add_comm(d)
    have = root.children[0].children[1].children[0]
    contrasts = Contrasts(
        [
            Probe(-9, 0, "unused", [Verdict("simp", "closed")]),
            Probe(have.children[0].line, have.children[0].column, "⊢ m + (k + 1) = m + k + 1",
                  [Verdict("omega", "closed")]),
            Probe(root.children[0].line, 0, "⊢ m + n = n + m", [Verdict("omega", "failed", error="NOT-LINEAR")]),
        ]
    )  # fmt: skip
    llm = LLM("mock:x", cache_dir=None)
    calls: list[tuple[str, str]] = []
    llm.set_mock(lambda system, messages: calls.append((system, messages[0]["content"])) or "expl")
    ex = explain(d, "contrastive", llm, states=states, contrasts=contrasts)
    assert ex.strategy == "contrastive"
    prompts = {e["node"]: user for e, (_, user) in zip(ex.trace, calls)}
    assert "`omega` closes this goal on its own" in calls[0][1]  # the `have`, explained first
    assert "NOT-LINEAR" in prompts["root"] and "Goal before this part" in prompts["root"]
    assert all("Lean's verdicts on routine alternatives" in system for system, _ in calls)
    assert ex.trace[0]["contrast"].startswith("routine") and ex.trace[-1]["contrast"].startswith("non-routine")

    grounded: list[tuple[str, str]] = []
    llm.set_mock(lambda system, messages: grounded.append((system, messages[0]["content"])) or "expl")
    explain(d, "grounded", llm, states=states)
    assert len(grounded) == len(calls)
    assert all("routine alternatives" not in s and "routine alternatives" not in u for s, u in grounded)
    # Apart from the verdict block, the grounded prompts are the same.
    assert [u for _, u in grounded] == [u.split("\n\nLean's verdict")[0] for _, u in calls]


def test_contrastive_needs_states_and_verdicts():
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    _, states = _states_for_add_comm(d)
    llm = LLM("mock:x", cache_dir=None)
    llm.set_mock(lambda s, m: "x")
    with pytest.raises(ValueError, match="goal states"):
        explain(d, "contrastive", llm, contrasts=Contrasts())
    with pytest.raises(ValueError, match="verdicts"):
        explain(d, "contrastive", llm, states=states)
    with pytest.raises(ValueError, match="boom"):
        explain(d, "contrastive", llm, states=states, contrasts=Contrasts(error="boom"))


def test_parts_with_only_unavailable_alternatives_get_no_verdict_block():
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    root, states = _states_for_add_comm(d)
    contrasts = Contrasts([Probe(root.children[0].line, 0, "⊢ m + n = n + m", [Verdict("linarith", "unavailable")])])
    llm = LLM("mock:x", cache_dir=None)
    prompts: list[str] = []
    llm.set_mock(lambda system, messages: prompts.append(messages[0]["content"]) or "expl")
    ex = explain(d, "contrastive", llm, states=states, contrasts=contrasts)
    assert not any("Lean's verdict" in p for p in prompts)
    assert all("contrast" not in e for e in ex.trace)


def test_runner_refuses_without_states(tmp_path):
    d = find_declaration(FIXTURE, "Toy.add_comm'")
    runner = ContrastRunner(tmp_path, alternatives=["omega"])
    assert "no goal states" in runner.contrasts(d, ProofStates(error="x")).error


@needs_lean
def test_runner_tries_alternatives_in_lean(tmp_path):
    from lpe.goals import GoalExtractor

    d = find_declaration(FIXTURE, "Toy.add_comm'")
    states = GoalExtractor(PROJECT, timeout=300).states(d)
    assert not states.error, states.error
    runner = ContrastRunner(PROJECT, alternatives=["omega", "simp", "no_such_tactic"], timeout=300, cache_dir=tmp_path)
    contrasts = runner.contrasts(d, states)
    assert not contrasts.error, contrasts.error
    targets = probe_targets(d, states)
    root_probe = contrasts.at(*targets[(-1, 0)])
    verdicts = {v.tactic: v for v in root_probe.verdicts}
    assert root_probe.goal == "m n : Nat\n⊢ m + n = n + m"
    assert verdicts["omega"].status == "closed"
    assert verdicts["simp"].status == "failed" and "no progress" in verdicts["simp"].error
    assert verdicts["no_such_tactic"].status == "unavailable"
    succ = parse_proof_tree(d.proof).children[0].children[1]
    succ_probe = contrasts.at(*targets[(succ.line, succ.column)])
    assert succ_probe is not None and succ_probe.goal.startswith("case succ")
    assert list(tmp_path.glob("*.json"))  # cached
    assert runner.contrasts(d, states) == contrasts


# ------------------------------------------------------------ end to end, no Lean, no API


def test_run_and_contrast_commands_offline(tmp_path, monkeypatch, capsys):
    from lpe import cli
    from lpe.lean_check import CheckResult

    project = tmp_path / "lean"
    mathlib = project / ".lake" / "packages" / "mathlib"
    mathlib.mkdir(parents=True)
    shutil.copy(FIXTURE, mathlib / "Toy.lean")
    bench = tmp_path / "bench.toml"
    bench.write_text('[[theorem]]\nname = "Toy.add_comm\'"\nfile = "Toy.lean"\n', encoding="utf-8")
    decl = find_declaration(FIXTURE, "Toy.add_comm'")
    _, states = _states_for_add_comm(decl)

    class FakeChecker:
        def __init__(self, *args, **kwargs):
            pass

        def check(self, d, proof):
            ok = proof.split() == d.proof.split()
            return CheckResult(ok, "ok" if ok else "error", "" if ok else "error: type mismatch")

    class FakeExtractor:
        def __init__(self, *args, **kwargs):
            pass

        def states(self, d):
            return states

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            self.alternatives = kwargs["alternatives"]

        def contrasts(self, d, st, min_children=1):
            return Contrasts([Probe(0, 0, "⊢ m + n = n + m", [Verdict(t, "failed", error="VERDICT") for t in self.alternatives])])

    def explainer(system, messages):
        return "CONTRASTIVE" if "VERDICT" in messages[0]["content"] else "PLAIN"

    def rebuilder(system, messages):
        if "CONTRASTIVE" in messages[0]["content"]:
            return f"```lean\n{decl.proof}\n```"
        return "```lean\nrfl\n```"

    real_llm = cli.LLM

    def fake_llm(spec, cache_dir=None):
        llm = real_llm(spec, cache_dir=None)
        llm.set_mock(explainer if spec == "mock:explainer" else rebuilder)
        return llm

    monkeypatch.setattr(cli, "LLM", fake_llm)
    monkeypatch.setattr(cli, "LeanChecker", FakeChecker)
    monkeypatch.setattr(cli, "GoalExtractor", FakeExtractor)
    monkeypatch.setattr(cli, "ContrastRunner", FakeRunner)
    out = tmp_path / "results.jsonl"
    cache = str(tmp_path / "cache" / "llm")
    cli.main([
        "--project", str(project), "run", str(bench),
        "--explainer", "mock:explainer", "--reconstructor", "mock:rebuilder",
        "--strategies", "grounded,contrastive", "--mask", "off", "--feedback", "0",
        "--alternatives", "omega,simp", "--out", str(out), "--cache", cache,
    ])  # fmt: skip
    rows = {r["strategy"]: r for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    assert rows["contrastive"]["success"] and not rows["grounded"]["success"]
    assert any(e.get("contrast", "").startswith("non-routine") for e in rows["contrastive"]["trace"])
    capsys.readouterr()

    cli.main(["report", str(out)])
    assert "| contrastive | no | 1 | 1 | 100% |" in capsys.readouterr().out

    cli.main(["--project", str(project), "contrast", str(bench), "--alternatives", "omega,simp", "--cache", cache])
    shown = capsys.readouterr().out
    assert "whole proof\n  [non-routine" in shown and "- `simp` fails: VERDICT" in shown
