"""Check-result cache, environment failures, and parallel `run`/`sanity`.

The checker tests use a stand-in for `lake` (a small script that answers like `lake env lean`
and counts its invocations), so they need neither Lean nor Mathlib.
"""

from __future__ import annotations

import json
import shutil
import stat
import sys
import threading
from pathlib import Path

import pytest

from lpe import cli
from lpe.cachefile import atomic_write_text
from lpe.extract import find_declaration
from lpe.lean_check import CheckResult, LeanChecker

FIXTURE = Path(__file__).parent / "fixtures" / "Toy.lean"
PROJECT = Path(__file__).resolve().parent.parent / "lean"
needs_lean = pytest.mark.skipif(shutil.which("lake") is None or not PROJECT.exists(), reason="needs lake")

FAKE_LAKE = r"""#!{python}
# Stand-in for `lake env lean FILE`: behaves according to markers in the candidate proof.
import pathlib, sys, time
log = pathlib.Path(__file__).with_name("calls.log")
with log.open("a") as fh:
    fh.write("x\n")
path = sys.argv[-1]
src = pathlib.Path(path).read_text()
lines = src.splitlines()
decl = next(i for i, l in enumerate(lines, 1) if l.lstrip().startswith("theorem le_add_of_le"))
if "SLOW" in src:
    time.sleep(5)
if "BROKEN_ENV" in src:
    print(f"{path}:1:0: error: object file ./.lake/build/lib/Foo.olean of module Foo does not exist")
    sys.exit(1)
if "CRASH" in src:
    sys.exit(134)
if "WRONG" in src:
    print(f"{path}:{decl + 1}:2: error: type mismatch")
    sys.exit(1)
print("'Toy.le_add_of_le' depends on axioms: [propext]")
"""


@pytest.fixture
def fake_project(tmp_path):
    project = tmp_path / "lean"
    project.mkdir()
    (project / "lean-toolchain").write_text("leanprover/lean4:v4.36.0\n")
    (project / "lake-manifest.json").write_text('{"packages": [{"name": "mathlib", "rev": "aaa"}]}\n')
    lake = project / "fake-lake"
    lake.write_text(FAKE_LAKE.replace("{python}", sys.executable))
    lake.chmod(lake.stat().st_mode | stat.S_IEXEC)
    return project


def _calls(project: Path) -> int:
    log = project / "calls.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def _checker(project: Path, cache: Path | None, timeout: int = 60) -> LeanChecker:
    return LeanChecker(project, timeout=timeout, lake=str(project / "fake-lake"), cache_dir=cache)


DECL = find_declaration(FIXTURE, "Toy.le_add_of_le")


def test_identical_candidates_are_checked_once(fake_project, tmp_path):
    cache = tmp_path / "check"
    checker = _checker(fake_project, cache)
    first = checker.check(DECL, DECL.proof)
    second = checker.check(DECL, DECL.proof)
    assert first.ok and not first.cached
    assert second.ok and second.cached
    assert _calls(fake_project) == 1
    assert (checker.stats.lean_runs, checker.stats.cache_hits) == (1, 1)

    # Failures with a real diagnostic are cached too, with their errors (they feed repair prompts).
    bad = checker.check(DECL, "exact WRONG")
    again = checker.check(DECL, "exact WRONG")
    assert (bad.reason, again.reason) == ("error", "error")
    assert again.cached and again.errors == bad.errors and "type mismatch" in again.errors
    assert _calls(fake_project) == 2

    # A fresh checker (a later run) reuses the cache on disk.
    assert _checker(fake_project, cache).check(DECL, DECL.proof).cached
    assert _calls(fake_project) == 2


def test_cache_is_invalidated_by_the_environment(fake_project, tmp_path):
    cache = tmp_path / "check"
    _checker(fake_project, cache).check(DECL, DECL.proof)
    (fake_project / "lake-manifest.json").write_text('{"packages": [{"name": "mathlib", "rev": "bbb"}]}\n')
    assert not _checker(fake_project, cache).check(DECL, DECL.proof).cached
    (fake_project / "lean-toolchain").write_text("leanprover/lean4:v4.37.0\n")
    assert not _checker(fake_project, cache).check(DECL, DECL.proof).cached
    assert _calls(fake_project) == 3


def test_machine_failures_are_reported_apart_and_never_cached(fake_project, tmp_path):
    cache = tmp_path / "check"
    checker = _checker(fake_project, cache, timeout=1)
    env = checker.check(DECL, "exact BROKEN_ENV")
    assert env.reason == "environment" and "before the declaration" in env.errors and ".olean" in env.errors
    crash = checker.check(DECL, "exact CRASH")
    assert crash.reason == "crash"
    slow = checker.check(DECL, "exact SLOW")
    assert slow.reason == "timeout"
    for proof in ("exact BROKEN_ENV", "exact CRASH"):
        assert not checker.check(DECL, proof).cached
    assert list(cache.glob("*.json")) == []


def test_banned_tokens_never_reach_lean(fake_project, tmp_path):
    checker = _checker(fake_project, tmp_path / "check")
    assert checker.check(DECL, "sorry").reason == "banned-token"
    assert _calls(fake_project) == 0


def test_without_cache_dir_every_candidate_runs_lean(fake_project):
    checker = _checker(fake_project, None)
    checker.check(DECL, DECL.proof)
    checker.check(DECL, DECL.proof)
    assert _calls(fake_project) == 2


def test_corrupt_cache_entry_is_rechecked(fake_project, tmp_path):
    cache = tmp_path / "check"
    checker = _checker(fake_project, cache)
    checker.check(DECL, DECL.proof)
    (entry,) = cache.glob("*.json")
    entry.write_text("{not json")
    assert not checker.check(DECL, DECL.proof).cached
    assert json.loads(entry.read_text())["ok"] is True


def test_atomic_write_from_many_threads(tmp_path):
    target = tmp_path / "entry.json"
    texts = [json.dumps({"n": i, "pad": "x" * 100_000}) for i in range(16)]
    threads = [threading.Thread(target=atomic_write_text, args=(target, t)) for t in texts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert target.read_text() in texts  # one complete version, never a mix
    assert [p.name for p in tmp_path.iterdir()] == ["entry.json"]  # no temporary files left


@needs_lean
def test_real_checker_cache_round_trip(tmp_path):
    checker = LeanChecker(PROJECT, timeout=300, cache_dir=tmp_path / "check")
    first = checker.check(DECL, DECL.proof)
    second = checker.check(DECL, DECL.proof)
    assert first.ok and second.ok and second.cached
    assert checker.stats.lean_runs == 1


# ------------------------------------------------------------ parallel run / sanity


class FakeChecker:
    """Accepts a candidate iff it is the original proof; records the threads it was called from."""

    threads: set[str] = set()

    def __init__(self, *args, **kwargs):
        pass

    def check(self, decl, proof):
        FakeChecker.threads.add(threading.current_thread().name)
        ok = proof.split() == decl.proof.split()
        return CheckResult(ok, "ok" if ok else "error", "" if ok else "error: type mismatch")


def _bench(tmp_path: Path, copies: int = 3) -> tuple[Path, Path]:
    project = tmp_path / "lean"
    mathlib = project / ".lake" / "packages" / "mathlib"
    mathlib.mkdir(parents=True)
    entries = []
    for k in range(copies):
        shutil.copy(FIXTURE, mathlib / f"Toy{k}.lean")
        for name in ("Toy.add_comm'", "Toy.le_add_of_le"):
            entries.append(f'[[theorem]]\nname = "{name}"\nfile = "Toy{k}.lean"\n')
    bench = tmp_path / "bench.toml"
    bench.write_text("\n".join(entries), encoding="utf-8")
    return project, bench


def _fake_llm_factory():
    real_llm = cli.LLM

    def rebuilder(system, messages):
        user = messages[0]["content"]
        if "No explanation is available" in user:
            return "```lean\nrfl\n```"
        name = "add_comm'" if "theorem add_comm'" in user else "le_add_of_le"
        return f"```lean\n{find_declaration(FIXTURE, f'Toy.{name}').proof}\n```"

    def fake_llm(spec, cache_dir=None):
        llm = real_llm(spec, cache_dir=None)
        llm.set_mock((lambda s, m: "an explanation") if spec == "mock:explainer" else rebuilder)
        return llm

    return fake_llm


def _run(project, bench, out, jobs):
    cli.main([
        "--project", str(project), "run", str(bench),
        "--explainer", "mock:explainer", "--reconstructor", "mock:rebuilder",
        "--strategies", "none,flat,recursive", "--mask", "both", "--feedback", "0",
        "--jobs", str(jobs), "--out", str(out), "--cache", str(out.parent / "cache" / "llm"),
    ])  # fmt: skip
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    for r in rows:
        r.pop("seconds")
    return rows


def test_parallel_run_writes_the_same_rows(tmp_path, monkeypatch, capsys):
    project, bench = _bench(tmp_path)
    monkeypatch.setattr(cli, "LLM", _fake_llm_factory())
    monkeypatch.setattr(cli, "LeanChecker", FakeChecker)
    FakeChecker.threads = set()
    serial = _run(project, bench, tmp_path / "serial" / "r.jsonl", jobs=1)
    FakeChecker.threads = set()
    parallel = _run(project, bench, tmp_path / "parallel" / "r.jsonl", jobs=3)
    assert len(FakeChecker.threads) > 1  # the work really was spread over workers

    def key(r):
        return (r["file"], r["theorem"], r["strategy"], r["masked"])

    assert len(serial) == 6 * 5  # 6 theorems × (none + flat×2 + recursive×2)
    assert sorted(serial, key=key) == sorted(parallel, key=key)
    # Rows of one theorem stay in strategy order, since a worker owns the whole theorem.
    for thm_file in {key(r)[:2] for r in parallel}:
        order = [(r["strategy"], r["masked"]) for r in parallel if key(r)[:2] == thm_file]
        assert order == [("none", False), ("flat", False), ("flat", True), ("recursive", False), ("recursive", True)]
    out = capsys.readouterr().out
    assert out.count("✓") == 2 * 4 * 6  # (masked) explanations rebuild; `none` does not

    # Resuming a parallel run skips everything already written.
    again = _run(project, bench, tmp_path / "parallel" / "r.jsonl", jobs=3)
    assert len(again) == len(parallel)


def test_parallel_sanity_keeps_bench_order(tmp_path, monkeypatch, capsys):
    project, bench = _bench(tmp_path, copies=2)
    monkeypatch.setattr(cli, "LeanChecker", FakeChecker)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--project", str(project), "sanity", str(bench), "--jobs", "4"])
    assert exc.value.code == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[2] for line in lines] == ["Toy.add_comm'", "Toy.le_add_of_le"] * 2
    assert all(line.startswith("ok ") for line in lines)
