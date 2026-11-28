"""Result files: reading rows, recovering from an interrupted write, and resuming `lpe run`."""

from __future__ import annotations

import json
import shutil

import pytest
from test_decoy import FIXTURE, FakeChecker, _explainer, _rebuilder

from lpe import cli, results


def _rows_text(*rows) -> str:
    return "".join(json.dumps(r) + "\n" for r in rows)


def test_read_rows_skips_blank_lines_and_a_partial_last_row(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(_rows_text({"a": 1}) + "\n" + _rows_text({"a": 2}) + '{"a": 3, "expl', encoding="utf-8")
    warnings = []
    assert results.read_rows(p, warn=warnings.append) == [{"a": 1}, {"a": 2}]
    assert len(warnings) == 1 and "r.jsonl:4" in warnings[0]


def test_read_rows_rejects_garbage_in_the_middle(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(_rows_text({"a": 1}) + "oops\n" + _rows_text({"a": 2}), encoding="utf-8")
    with pytest.raises(results.ResultFileError, match="r.jsonl:2"):
        results.read_rows(p)
    p.write_text(_rows_text({"a": 1}) + '{"a": 2\n', encoding="utf-8")  # broken but terminated
    with pytest.raises(results.ResultFileError, match="r.jsonl:2"):
        results.read_rows(p)


def test_repair_tail(tmp_path):
    p = tmp_path / "r.jsonl"
    assert results.repair_tail(p) is None  # missing file
    p.write_text(_rows_text({"a": 1}), encoding="utf-8")
    assert results.repair_tail(p) is None  # already clean
    p.write_text(_rows_text({"a": 1}) + '{"a": 2}', encoding="utf-8")
    assert "newline" in results.repair_tail(p)
    assert p.read_text(encoding="utf-8") == _rows_text({"a": 1}, {"a": 2})
    p.write_text(_rows_text({"a": 1}) + '{"a": 2, "b"', encoding="utf-8")
    assert "incomplete" in results.repair_tail(p)
    assert p.read_text(encoding="utf-8") == _rows_text({"a": 1})
    p.write_text('{"a": ', encoding="utf-8")  # a single partial row
    results.repair_tail(p)
    assert p.read_text(encoding="utf-8") == ""


def test_run_resumes_after_an_interrupted_write(tmp_path, monkeypatch, capsys):
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
    calls = {"rebuild": 0}

    def rebuilder(system, messages):
        calls["rebuild"] += 1
        return _rebuilder(system, messages)

    def fake_llm(spec, cache_dir=None):
        llm = real_llm(spec, cache_dir=None)
        llm.set_mock(_explainer if spec == "mock:explainer" else rebuilder)
        return llm

    monkeypatch.setattr(cli, "LLM", fake_llm)
    monkeypatch.setattr(cli, "LeanChecker", FakeChecker)
    out = tmp_path / "results.jsonl"

    def run():
        cli.main([
            "--project", str(project), "run", str(bench),
            "--explainer", "mock:explainer", "--reconstructor", "mock:rebuilder",
            "--strategies", "none,flat", "--mask", "off", "--feedback", "0", "--out", str(out),
        ])  # fmt: skip

    run()
    full = out.read_text(encoding="utf-8")
    lines = full.splitlines(keepends=True)
    assert len(lines) == 4 and calls["rebuild"] == 4
    # Interrupt the last row half-way through its write.
    out.write_text("".join(lines[:3]) + lines[3][: len(lines[3]) // 2], encoding="utf-8")
    capsys.readouterr()
    cli.main(["report", str(out)])  # report still works on the damaged file
    captured = capsys.readouterr()
    assert "incomplete last row" in captured.err
    assert "| none | no | 2 |" in captured.out and "| flat | no | 1 |" in captured.out

    run()  # resume: the partial row is dropped and only that one row is redone
    assert "removed an incomplete last row" in capsys.readouterr().out
    assert calls["rebuild"] == 5
    rows = results.read_rows(out)
    assert sorted(map(results.row_key, rows)) == sorted(map(results.row_key, map(json.loads, lines)))
    assert out.read_text(encoding="utf-8").endswith("\n")
