"""The decoy control, and an offline end-to-end run of `lpe run` + `lpe report`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lpe import cli
from lpe.explain import decoy_explanation, explain, pick_donor
from lpe.extract import find_declaration
from lpe.lean_check import CheckResult
from lpe.llm import LLM

FIXTURE = Path(__file__).parent / "fixtures" / "Toy.lean"


def test_pick_donor_prefers_another_file():
    files = ["A.lean", "A.lean", "B.lean", "C.lean"]
    assert pick_donor(files, 0) == 2
    assert pick_donor(files, 1) == 2
    assert pick_donor(files, 2) == 3
    assert pick_donor(files, 3) == 0  # wraps around
    assert pick_donor(["A.lean", "A.lean", "A.lean"], 2) == 0  # one file only: next theorem
    with pytest.raises(ValueError):
        pick_donor(["A.lean"], 0)


def test_decoy_is_the_donors_flat_explanation():
    target = find_declaration(FIXTURE, "Toy.le_add_of_le")
    donor = find_declaration(FIXTURE, "Toy.add_comm'")
    llm = LLM("mock:x", cache_dir=None)
    llm.set_mock(lambda system, messages: "about add_comm" if "add_comm'" in messages[0]["content"] else "other")
    ex = decoy_explanation(donor, llm)
    assert ex.strategy == "decoy" and ex.text == "about add_comm" == explain(donor, "flat", llm).text
    assert ex.trace == [{"node": "donor", "name": donor.name}]
    assert target.name not in ex.text
    with pytest.raises(ValueError, match="donor"):
        explain(target, "decoy", llm)


# ------------------------------------------------------------ end to end, no Lean, no API


class FakeChecker:
    """Accepts a candidate iff it is the original proof (whitespace-insensitive)."""

    def __init__(self, *args, **kwargs):
        pass

    def check(self, decl, proof):
        ok = proof.split() == decl.proof.split()
        return CheckResult(ok, "ok" if ok else "error", "" if ok else "error: type mismatch")


def _explainer(system, messages):
    user = messages[0]["content"]
    return "EXPLAINS-ADD-COMM" if "add_comm'" in user else "EXPLAINS-LE-ADD"


def _rebuilder(system, messages):
    """Writes the right proof only when the explanation in the prompt is about this theorem."""
    user = messages[0]["content"]
    for name, marker in (("add_comm'", "EXPLAINS-ADD-COMM"), ("le_add_of_le", "EXPLAINS-LE-ADD")):
        if f"theorem {name}" in user and marker in user:
            proof = find_declaration(FIXTURE, f"Toy.{name}").proof
            return f"```lean\n{proof}\n```"
    return "```lean\nrfl\n```"


def test_run_and_report_with_decoy(tmp_path, monkeypatch, capsys):
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

    def fake_llm(spec, cache_dir=None):
        llm = real_llm(spec, cache_dir=None)
        llm.set_mock(_explainer if spec == "mock:explainer" else _rebuilder)
        return llm

    monkeypatch.setattr(cli, "LLM", fake_llm)
    monkeypatch.setattr(cli, "LeanChecker", FakeChecker)
    out = tmp_path / "results.jsonl"
    cli.main([
        "--project", str(project), "run", str(bench),
        "--explainer", "mock:explainer", "--reconstructor", "mock:rebuilder",
        "--strategies", "none,decoy,flat", "--mask", "both", "--feedback", "0",
        "--out", str(out),
    ])  # fmt: skip
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    got = {(r["theorem"], r["strategy"], r["masked"]): r for r in rows}
    # decoy rows are never masked; flat gets both variants
    assert len(rows) == 2 * (1 + 1 + 2)
    assert not any(r["strategy"] == "decoy" and r["masked"] for r in rows)
    decoy = got[("Toy.add_comm'", "decoy", False)]
    assert decoy["explanation"] == "EXPLAINS-LE-ADD"  # the other theorem's explanation
    assert decoy["trace"] == [{"node": "donor", "name": "Toy.le_add_of_le"}]
    assert not decoy["success"]
    assert got[("Toy.add_comm'", "flat", False)]["success"]
    assert not got[("Toy.le_add_of_le", "none", False)]["success"]

    capsys.readouterr()
    cli.main(["report", str(out)])
    report = capsys.readouterr().out
    assert "| decoy | no | 2 | 0 | 0% |" in report
    assert "Gain over the decoy control" in report
    assert "- flat: +2 / -0  (n=2)" in report
