"""Report statistics: intervals, paired tests, pass@k, and per-sample outcomes from `run`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lpe import cli
from lpe.extract import find_declaration
from lpe.lean_check import CheckResult
from lpe.llm import LLM
from lpe.reconstruct import reconstruct
from lpe.stats import holm, mcnemar_exact, paired_bootstrap_ci, pass_at_k, row_pass_at_k, wilson_interval

FIXTURE = Path(__file__).parent / "fixtures" / "Toy.lean"


def test_wilson_interval():
    lo, hi = wilson_interval(0, 2)
    assert lo == 0.0 and hi == pytest.approx(0.6576, abs=1e-3)
    lo, hi = wilson_interval(6, 12)
    assert lo == pytest.approx(0.2538, abs=1e-3) and hi == pytest.approx(0.7462, abs=1e-3)
    assert wilson_interval(12, 12)[1] == 1.0
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_mcnemar_exact():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(5, 0) == pytest.approx(2 / 32)
    assert mcnemar_exact(0, 5) == mcnemar_exact(5, 0)  # two-sided
    assert mcnemar_exact(3, 3) == 1.0
    # 8 vs 1: 2 * (1 + 9) / 512
    assert mcnemar_exact(8, 1) == pytest.approx(20 / 512)


def test_holm():
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([0.5, 0.9]) == pytest.approx([1.0, 1.0])
    assert holm([]) == []


def test_paired_bootstrap_ci():
    assert paired_bootstrap_ci([(True, False)] * 5) == (1.0, 1.0)
    assert paired_bootstrap_ci([(True, True), (False, False)]) == (0.0, 0.0)
    pairs = [(True, False)] * 4 + [(False, True)] * 2 + [(True, True)] * 6
    lo, hi = paired_bootstrap_ci(pairs)
    assert lo < 2 / 12 < hi and lo < 0 < hi
    assert paired_bootstrap_ci(pairs) == (lo, hi)  # seeded, reproducible
    assert paired_bootstrap_ci([]) == (0.0, 0.0)


def test_pass_at_k():
    assert pass_at_k(4, 0, 2) == 0.0
    assert pass_at_k(4, 4, 1) == 1.0
    assert pass_at_k(4, 1, 1) == pytest.approx(0.25)
    assert pass_at_k(4, 1, 2) == pytest.approx(0.5)  # 1 - C(3,2)/C(4,2)
    assert pass_at_k(4, 3, 2) == 1.0
    with pytest.raises(ValueError):
        pass_at_k(2, 1, 3)


def test_row_pass_at_k():
    legacy = {"success": True}  # rows written before per-sample outcomes were recorded
    assert row_pass_at_k(legacy, 1) == 1.0 and row_pass_at_k(legacy, 2) is None
    full = {"success": True, "all_samples": True, "sample_success": [False, True, False, False]}
    assert row_pass_at_k(full, 1) == pytest.approx(0.25)
    assert row_pass_at_k(full, 4) == 1.0 and row_pass_at_k(full, 8) is None
    stopped = {"success": True, "all_samples": False, "sample_success": [False, False, True]}
    assert row_pass_at_k(stopped, 1) == 0.0 and row_pass_at_k(stopped, 3) == 1.0
    assert row_pass_at_k(stopped, 4) == 1.0  # a success within the first 3 is one within the first 4
    failed = {"success": False, "all_samples": False, "sample_success": [False, False]}
    assert row_pass_at_k(failed, 2) == 0.0 and row_pass_at_k(failed, 4) is None


# ------------------------------------------------------------ per-sample outcomes


class FakeChecker:
    """Accepts a candidate iff it is the original proof (whitespace-insensitive)."""

    def __init__(self, *args, **kwargs):
        pass

    def check(self, decl, proof):
        ok = proof.split() == decl.proof.split()
        return CheckResult(ok, "ok" if ok else "error", "" if ok else "error: type mismatch")


def _alternating(decl):
    """Rebuilder whose odd-numbered calls write the original proof."""
    calls = {"n": 0}

    def fn(system, messages):
        calls["n"] += 1
        return f"```lean\n{decl.proof}\n```" if calls["n"] % 2 == 0 else "```lean\nrfl\n```"

    return fn


@pytest.mark.parametrize(
    "all_samples, expected, attempts", [(False, [False, True], 2), (True, [False, True, False, True], 4)]
)
def test_reconstruct_records_each_sample(all_samples, expected, attempts):
    decl = find_declaration(FIXTURE, "Toy.add_comm'")
    llm = LLM("mock:x", cache_dir=None)
    llm.set_mock(_alternating(decl))
    rec = reconstruct(decl, "", llm, FakeChecker(), samples=4, feedback_rounds=0, all_samples=all_samples)
    assert rec.success and rec.first_success == 2
    assert rec.sample_success == expected and len(rec.attempts) == attempts


def test_reconstruct_sample_includes_its_repairs():
    decl = find_declaration(FIXTURE, "Toy.add_comm'")
    llm = LLM("mock:x", cache_dir=None)
    llm.set_mock(_alternating(decl))  # first try fails, its repair succeeds
    rec = reconstruct(decl, "", llm, FakeChecker(), samples=3, feedback_rounds=1)
    assert rec.sample_success == [True] and len(rec.attempts) == 2


def test_run_and_report_with_samples(tmp_path, monkeypatch, capsys):
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

    def rebuilder(system, messages):
        """Writes the original proof on every other call when shown an explanation, never without one."""
        user = messages[0]["content"]
        if "No explanation is available" in user:
            return "```lean\nrfl\n```"
        name = "add_comm'" if "theorem add_comm'" in user else "le_add_of_le"
        rebuilder.n = getattr(rebuilder, "n", 0) + 1
        proof = find_declaration(FIXTURE, f"Toy.{name}").proof
        return f"```lean\n{proof}\n```" if rebuilder.n % 2 == 0 else "```lean\nrfl\n```"

    def fake_llm(spec, cache_dir=None):
        llm = real_llm(spec, cache_dir=None)
        llm.set_mock((lambda s, m: "an explanation") if spec == "mock:explainer" else rebuilder)
        return llm

    monkeypatch.setattr(cli, "LLM", fake_llm)
    monkeypatch.setattr(cli, "LeanChecker", FakeChecker)
    out = tmp_path / "results.jsonl"
    cli.main([
        "--project", str(project), "run", str(bench),
        "--explainer", "mock:explainer", "--reconstructor", "mock:rebuilder",
        "--strategies", "none,flat", "--mask", "off", "--feedback", "0",
        "--samples", "4", "--all-samples", "--out", str(out),
    ])  # fmt: skip
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    flat = [r for r in rows if r["strategy"] == "flat"]
    none = [r for r in rows if r["strategy"] == "none"]
    assert all(r["sample_success"] == [False, True, False, True] for r in flat)
    assert all(r["sample_success"] == [False] * 4 for r in none)
    assert all(r["samples"] == 4 and r["feedback"] == 0 and r["all_samples"] for r in rows)

    capsys.readouterr()
    cli.main(["report", str(out)])
    report = capsys.readouterr().out
    assert "| flat | no | 2 | 2 | 100% | 34%–100% |" in report
    assert "| pass@1 | pass@2 | pass@4 |" in report
    assert "| flat | no | 50% (n=2) | 83% (n=2) | 100% (n=2) |" in report
    assert "| none | no | 0% (n=2) | 0% (n=2) | 0% (n=2) |" in report
    assert "stopped at the first success" not in report
    assert "- flat: +2 / -0  (n=2)  Δ=+100% [+100%, +100%]  p=0.5  p_Holm=0.5" in report


def test_overwrite_replaces_instead_of_appending(tmp_path, monkeypatch):
    project = tmp_path / "lean"
    mathlib = project / ".lake" / "packages" / "mathlib"
    mathlib.mkdir(parents=True)
    shutil.copy(FIXTURE, mathlib / "Toy.lean")
    bench = tmp_path / "bench.toml"
    bench.write_text('[[theorem]]\nname = "Toy.add_comm\'"\nfile = "Toy.lean"\n', encoding="utf-8")
    real_llm = cli.LLM

    def fake_llm(spec, cache_dir=None):
        llm = real_llm(spec, cache_dir=None)
        llm.set_mock(lambda s, m: "```lean\nrfl\n```")
        return llm

    monkeypatch.setattr(cli, "LLM", fake_llm)
    monkeypatch.setattr(cli, "LeanChecker", FakeChecker)
    out = tmp_path / "results.jsonl"
    argv = ["--project", str(project), "run", str(bench), "--reconstructor", "mock:r",
            "--strategies", "none", "--feedback", "0", "--out", str(out)]  # fmt: skip
    cli.main(argv)
    cli.main(argv)  # resumes: nothing left to do
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1
    cli.main(argv + ["--overwrite"])  # reruns, replacing the file
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1
