"""Training-data export: rewards, pooling, best-of-n, preference pairs, and `lpe export`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lpe import cli, dataset
from lpe.explain import FLAT_SYSTEM, flat_prompt
from lpe.extract import find_declaration

from test_stats import FakeChecker

FIXTURE = Path(__file__).parent / "fixtures" / "Toy.lean"


def row(theorem="T", strategy="flat", text="why", success=True, leak=0.0, masked=False, **kw):
    r = {
        "theorem": theorem, "file": "F.lean", "strategy": strategy, "masked": masked,
        "explainer": "mock:e", "reconstructor": "mock:r", "success": success,
        "leak_rate": leak, "lean_notation": [], "explanation": text,
        "statement": f"theorem {theorem} : True := by", "proof": "trivial",
    }  # fmt: skip
    r.update(kw)
    return r


def test_success_score_uses_all_samples_only():
    assert dataset.success_score(row(sample_success=[True, False, False, True], all_samples=True)) == 0.5
    # Stopped at the first success: the fraction would be biased, so the 0/1 outcome is used.
    assert dataset.success_score(row(sample_success=[False, True], all_samples=False)) == 1.0
    assert dataset.success_score(row(success=False)) == 0.0


def test_reward_penalises_leakage_and_notation():
    cfg = dataset.RewardConfig(lam=0.5, notation_penalty=0.25)
    assert dataset.reward(1.0, 0.4, [], cfg) == pytest.approx(0.8)
    assert dataset.reward(1.0, 0.4, ["turnstile"], cfg) == pytest.approx(0.55)
    assert dataset.reward(0.0, 0.0, [], cfg) == 0.0
    gated = dataset.RewardConfig(lam=0.5, leak_gate=0.5)
    assert dataset.reward(1.0, 0.5, [], gated) == pytest.approx(0.75)  # at the gate: still credited
    assert dataset.reward(1.0, 0.6, [], gated) == pytest.approx(-0.3)  # above it: only the penalty


def test_candidates_pool_identical_text_and_skip_non_explanations():
    rows = [
        row(strategy="none", text="", success=False),
        row(strategy="decoy", text="about another theorem"),
        row(strategy="flat", text="A", success=True, leak=0.2),
        row(strategy="recursive", text="A", success=False, leak=0.2, reconstructor="mock:r2"),
        row(strategy="recursive", text="[lemma] B", masked=True),
        row(strategy="grounded", text="C", success=False),
    ]
    cands = dataset.candidates(rows, dataset.RewardConfig(lam=0.5))
    assert [c.text for c in cands] == ["A", "C"]
    a = cands[0]
    assert a.scores == [1.0, 0.0] and a.strategies == ["flat", "recursive"]
    assert a.reconstructors == ["mock:r", "mock:r2"]
    assert a.reward == pytest.approx(0.5 - 0.1)
    assert a.baseline is False
    with_masked = dataset.candidates(rows, include_masked=True)
    assert [c.text for c in with_masked] == ["A", "[lemma] B", "C"]


def test_best_of_n_and_pairs():
    rows = [
        row("T", "flat", "long explanation that works", True, 0.0),
        row("T", "recursive", "short, works", True, 0.0),
        row("T", "grounded", "leaky but works", True, 0.8),
        row("T", "contrastive", "fails", False, 0.0),
        row("U", "flat", "fails too", False),
        row("V", "flat", "works", True),
        row("V", "none", "", True),  # V is rebuilt without any explanation
    ]
    cands = dataset.candidates(rows, dataset.RewardConfig(lam=0.5))
    best = dataset.best_of_n(cands, min_reward=0.5)
    assert [(c.theorem, c.text) for c in best] == [("T", "short, works"), ("V", "works")]  # tie -> shorter
    assert [c.theorem for c in dataset.best_of_n(cands, 0.5, skip_easy=True)] == ["T"]
    # Rewards for T: 1.0, 1.0, 0.6 (leak 0.8 at lambda 0.5), 0.0.
    pairs = dataset.preference_pairs(cands, min_margin=0.5)
    assert [(a.text, b.text) for a, b in pairs] == [
        ("short, works", "fails"),
        ("long explanation that works", "fails"),
        ("leaky but works", "fails"),
    ]
    pairs = dataset.preference_pairs(cands, min_margin=0.3)
    assert [(a.text, b.text) for a, b in pairs] == [
        ("short, works", "leaky but works"),
        ("short, works", "fails"),
        ("long explanation that works", "leaky but works"),
        ("long explanation that works", "fails"),
        ("leaky but works", "fails"),
    ]
    # Equal rewards (the two working explanations of T) never form a pair.
    assert len(dataset.preference_pairs(cands, min_margin=0.0)) == 5


def test_sft_example_uses_the_flat_prompt():
    decl = find_declaration(FIXTURE, "Toy.le_add_of_le")
    r = row("Toy.le_add_of_le", "recursive", "the explanation", statement=decl.statement_in_context, proof=decl.proof)
    (cand,) = dataset.candidates([r])
    ex = dataset.sft_example(cand)
    system, user = flat_prompt(decl)
    assert ex["messages"] == [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
        {"role": "assistant", "content": "the explanation"},
    ]
    assert system == FLAT_SYSTEM and ex["meta"]["strategies"] == ["recursive"]


def test_old_rows_need_a_lookup():
    r = row()
    del r["statement"], r["proof"]
    with pytest.raises(ValueError, match="--bench"):
        dataset.candidates([r])
    (cand,) = dataset.candidates([r], lookup=lambda t, f: ("stmt", "pf"))
    assert (cand.statement, cand.proof) == ("stmt", "pf")


def test_run_then_export(tmp_path, monkeypatch, capsys):
    """Offline end to end: mock explainer and rebuilder, fake checker, then all three formats."""
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
    real_llm = cli.LLM

    def explainer(system, messages):
        return "one-pass explanation" if system == FLAT_SYSTEM else "bottom-up explanation"

    def rebuilder(system, messages):
        """Rebuilds only from the bottom-up explanation."""
        user = messages[0]["content"]
        if "bottom-up explanation" not in user:
            return "```lean\nrfl\n```"
        name = "add_comm'" if "theorem add_comm'" in user else "le_add_of_le"
        return f"```lean\n{find_declaration(FIXTURE, f'Toy.{name}').proof}\n```"

    def fake_llm(spec, cache_dir=None):
        llm = real_llm(spec, cache_dir=None)
        llm.set_mock(explainer if spec == "mock:explainer" else rebuilder)
        return llm

    monkeypatch.setattr(cli, "LLM", fake_llm)
    monkeypatch.setattr(cli, "LeanChecker", FakeChecker)
    results = tmp_path / "results.jsonl"
    cli.main([
        "--project", str(project), "run", str(bench),
        "--explainer", "mock:explainer", "--reconstructor", "mock:rebuilder",
        "--strategies", "none,flat,recursive", "--mask", "both", "--feedback", "0", "--out", str(results),
    ])  # fmt: skip
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    assert all(r["statement"].startswith("namespace Toy") or "theorem" in r["statement"] for r in rows)

    def export(*extra):
        out = tmp_path / "out.jsonl"
        cli.main(["--project", str(project), "export", str(results), "--out", str(out), *extra])
        return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

    records = export("--format", "records")
    assert sorted((r["theorem"], r["strategies"][0], r["reward"]) for r in records) == [
        ("Toy.add_comm'", "flat", 0.0), ("Toy.add_comm'", "recursive", 1.0),
        ("Toy.le_add_of_le", "flat", 0.0), ("Toy.le_add_of_le", "recursive", 1.0),
    ]  # fmt: skip
    assert all(r["baseline_success"] is False and not r["masked"] for r in records)

    sft = export("--format", "sft")
    assert len(sft) == 2
    assert all(ex["messages"][-1]["content"] == "bottom-up explanation" for ex in sft)
    assert all(ex["messages"][0]["content"] == FLAT_SYSTEM for ex in sft)

    pairs = export("--format", "pairs")
    assert [(p["chosen"], p["rejected"]) for p in pairs] == [("bottom-up explanation", "one-pass explanation")] * 2

    gated = export("--format", "records", "--leak-gate", "0.0", "--lam", "0")
    assert sorted(r["reward"] for r in gated) == [0.0, 0.0, 1.0, 1.0]  # no leakage: the gate never fires
    assert len(export("--format", "records", "--include-masked")) == 4  # mock text has no names: masking is a no-op
    assert "4 candidate explanations of 2 theorems" in capsys.readouterr().out

    # Rows from before `run` stored statement and proof are completed from the benchmark.
    old = tmp_path / "old.jsonl"
    old.write_text(
        "".join(json.dumps({k: v for k, v in r.items() if k not in ("statement", "proof")}) + "\n" for r in rows),
        encoding="utf-8",
    )
    out = tmp_path / "from_old.jsonl"
    cli.main(["--project", str(project), "export", str(old), "--format", "sft", "--bench", str(bench), "--out", str(out)])
    assert [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()] == sft
