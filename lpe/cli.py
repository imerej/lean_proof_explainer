"""Command-line entry point: `python -m lpe <command> ...` (or `lpe` once installed)."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import tomllib
from collections import defaultdict
from pathlib import Path

from .explain import decoy_explanation, explain, leakage, mask_identifiers, pick_donor
from .extract import find_declaration
from .lean_check import LeanChecker
from .llm import LLM
from .reconstruct import reconstruct

DEFAULT_PROJECT = Path(__file__).resolve().parent.parent / "lean"


def mathlib_root(project: Path) -> Path:
    return project / ".lake" / "packages" / "mathlib"


def load_bench(path: Path, project: Path):
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    root = mathlib_root(project)
    for entry in data["theorem"]:
        yield entry, find_declaration(root / entry["file"], entry["name"])


# ------------------------------------------------------------------ select


def cmd_select(args) -> None:
    """Sample tactic-proved theorems of a given proof length from Mathlib."""
    root = mathlib_root(args.project)
    files = sorted((root / "Mathlib").rglob("*.lean"))
    if args.under:
        files = [f for f in files if str(f.relative_to(root)).startswith(args.under)]
    if args.max_imports:
        from .imports import ImportGraph, module_of

        graph = ImportGraph(root.parent)
        files = [f for f in files if len(graph.closure(module_of(str(f.relative_to(root))))) <= args.max_imports]
        print(f"{len(files)} files have at most {args.max_imports} package modules in their import closure")
    rng = random.Random(args.seed)
    rng.shuffle(files)
    import re

    picked = []
    name_re = re.compile(r"^(?:theorem|lemma)\s+([^\s:({\[]+)", re.M)
    for f in files:
        text = f.read_text(encoding="utf-8")
        for name in name_re.findall(text):
            try:
                decl = find_declaration(f, name)
            except ValueError:
                continue
            if args.min_lines <= decl.line_count <= args.max_lines:
                picked.append((str(f.relative_to(root)), name, decl.line_count))
                break  # at most one per file, for variety
        if len(picked) >= args.n:
            break
    lines = [f"# {len(picked)} theorems sampled by `lpe select` (seed {args.seed}, {args.min_lines}-{args.max_lines} proof lines)\n"]
    for file, name, n in picked:
        lines.append(f'[[theorem]]\nname = "{name}"\nfile = "{file}"\nproof_lines = {n}\n')
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {len(picked)} theorems to {args.out}")


# ------------------------------------------------------------------ sanity


def cmd_sanity(args) -> None:
    """Check that each original proof compiles through the same splice-and-check path."""
    checker = LeanChecker(args.project, timeout=args.timeout)
    bad = 0
    for entry, decl in load_bench(Path(args.bench), args.project):
        res = checker.check(decl, decl.proof)
        status = "ok " if res.ok else "BAD"
        bad += not res.ok
        print(f"{status} {res.seconds:6.1f}s  {entry['name']}  ({decl.line_count} lines)  {res.reason}")
        if not res.ok and args.verbose:
            print(res.errors[:2000])
    sys.exit(1 if bad else 0)


# ------------------------------------------------------------------ explain


def cmd_explain(args) -> None:
    llm = LLM(args.explainer, cache_dir=Path(args.cache))
    for entry, decl in load_bench(Path(args.bench), args.project):
        if args.theorem and entry["name"] != args.theorem:
            continue
        ex = explain(decl, args.strategy, llm, min_children=args.min_children)
        leak = leakage(decl, ex.text)
        print(f"## {entry['name']}  [{args.strategy}, {ex.calls} calls, leakage {leak.rate:.0%}]\n")
        print(ex.text, "\n")


# ------------------------------------------------------------------ run


def cmd_run(args) -> None:
    project = args.project
    checker = LeanChecker(project, timeout=args.timeout)
    explainer = LLM(args.explainer, cache_dir=Path(args.cache)) if args.explainer else None
    rebuilder = LLM(args.reconstructor, cache_dir=Path(args.cache))
    strategies = args.strategies.split(",")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists() and not args.overwrite:
        for line in out.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["theorem"], r["strategy"], r["masked"]))

    bench = list(load_bench(Path(args.bench), project))
    files = [entry["file"] for entry, _ in bench]
    with out.open("a", encoding="utf-8") as fh:
        for i, (entry, decl) in enumerate(bench):
            for strategy in strategies:
                for masked in ([False, True] if args.mask == "both" else [args.mask == "on"]):
                    # Masking removes the theorem's own lemma names; it is meaningless for the
                    # baseline and for a decoy explanation written about another theorem.
                    if strategy in ("none", "decoy") and masked:
                        continue
                    key = (entry["name"], strategy, masked)
                    if key in done:
                        continue
                    t0 = time.monotonic()
                    if strategy == "decoy":
                        ex = decoy_explanation(bench[pick_donor(files, i)][1], explainer)
                    else:
                        ex = explain(decl, strategy, explainer, min_children=args.min_children)
                    text = mask_identifiers(decl, ex.text) if masked else ex.text
                    leak = leakage(decl, text)
                    rec = reconstruct(
                        decl, text, rebuilder, checker, samples=args.samples, feedback_rounds=args.feedback
                    )
                    row = {
                        "theorem": entry["name"],
                        "file": entry["file"],
                        "proof_lines": decl.line_count,
                        "strategy": strategy,
                        "masked": masked,
                        "explainer": args.explainer if strategy != "none" else None,
                        "reconstructor": args.reconstructor,
                        "success": rec.success,
                        "first_success": rec.first_success,
                        "attempts": len(rec.attempts),
                        "failure_reasons": [a.result.reason for a in rec.attempts if not a.result.ok],
                        "explainer_calls": ex.calls,
                        "explanation_chars": len(text),
                        "leak_rate": round(leak.rate, 3),
                        "leaked_identifiers": leak.identifiers_leaked,
                        "proof_identifiers": leak.identifiers_total,
                        "explanation": text,
                        "trace": ex.trace,
                        "final_proof": rec.attempts[-1].proof if rec.attempts else "",
                        "seconds": round(time.monotonic() - t0, 1),
                    }
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                    mark = "✓" if rec.success else "✗"
                    print(f"{mark} {entry['name']:<40} {strategy:<10} masked={masked!s:<5} leak={leak.rate:.0%} attempts={len(rec.attempts)}")
    print(f"explainer usage: {explainer.usage if explainer else '-'}")
    print(f"reconstructor usage: {rebuilder.usage}")


# ------------------------------------------------------------------ report


def cmd_report(args) -> None:
    rows = [json.loads(line) for p in args.results for line in Path(p).read_text(encoding="utf-8").splitlines()]
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["strategy"], r["masked"])].append(r)
    order = {"none": 0, "decoy": 1, "flat": 2, "recursive": 3}
    print("| strategy | masked | n | rebuilt | success | mean leak | mean chars | explainer calls |")
    print("|---|---|---|---|---|---|---|---|")
    for (strategy, masked), rs in sorted(groups.items(), key=lambda kv: (order.get(kv[0][0], 9), kv[0][1])):
        n = len(rs)
        ok = sum(r["success"] for r in rs)
        print(
            f"| {strategy} | {'yes' if masked else 'no'} | {n} | {ok} | {ok / n:.0%} | "
            f"{sum(r['leak_rate'] for r in rs) / n:.0%} | {sum(r['explanation_chars'] for r in rs) / n:.0f} | "
            f"{sum(r['explainer_calls'] for r in rs) / n:.1f} |"
        )
    # Paired view: which theorems does each strategy rebuild that the baseline does not?
    by_thm: dict[str, dict] = defaultdict(dict)
    for r in rows:
        by_thm[r["theorem"]][(r["strategy"], r["masked"])] = r["success"]
    baselines = [
        (("none", False), "Gain over the no-explanation baseline (theorems rebuilt only with an explanation):"),
        (("decoy", False), "Gain over the decoy control (an explanation of a different theorem):"),
    ]
    for base, title in baselines:
        if not any(base in v for v in by_thm.values()):
            continue
        print(f"\n{title}")
        for key in sorted({k for v in by_thm.values() for k in v if k != base and k[0] != "none"}):
            # Only theorems that have both rows are compared.
            pairs = [(v[key], v[base]) for v in by_thm.values() if key in v and base in v]
            gained = sum(1 for a, b in pairs if a and not b)
            lost = sum(1 for a, b in pairs if b and not a)
            print(f"- {key[0]}{' (masked)' if key[1] else ''}: +{gained} / -{lost}  (n={len(pairs)})")


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="lpe", description=__doc__)
    p.add_argument("--project", type=Path, default=DEFAULT_PROJECT, help="Lean project with Mathlib (default: ./lean)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("select", help="sample theorems from Mathlib into a benchmark file")
    s.add_argument("--out", required=True)
    s.add_argument("--n", type=int, default=20)
    s.add_argument("--min-lines", type=int, default=12)
    s.add_argument("--max-lines", type=int, default=40)
    s.add_argument("--under", default="", help="only files under this path, e.g. Mathlib/NumberTheory")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument(
        "--max-imports", type=int, default=0, help="only files whose import closure has at most this many modules"
    )
    s.set_defaults(fn=cmd_select)

    s = sub.add_parser("sanity", help="check original proofs compile through the checker")
    s.add_argument("bench")
    s.add_argument("--timeout", type=int, default=900)
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_sanity)

    s = sub.add_parser("explain", help="print explanations without reconstructing")
    s.add_argument("bench")
    s.add_argument("--theorem")
    s.add_argument("--strategy", default="recursive", choices=["flat", "recursive"])
    s.add_argument("--explainer", required=True, help="e.g. anthropic:claude-opus-5-5")
    s.add_argument("--min-children", type=int, default=1)
    s.add_argument("--cache", default=".cache/llm")
    s.set_defaults(fn=cmd_explain)

    s = sub.add_parser("run", help="explain, reconstruct, and check")
    s.add_argument("bench")
    s.add_argument(
        "--strategies",
        default="none,flat,recursive",
        help="comma-separated: none, decoy, flat, recursive (decoy = another theorem's flat explanation)",
    )
    s.add_argument("--explainer", help="model for explanations, e.g. anthropic:claude-opus-5-5")
    s.add_argument("--reconstructor", required=True, help="model that rebuilds proofs")
    s.add_argument("--mask", choices=["off", "on", "both"], default="both", help="mask Lean identifiers in explanations")
    s.add_argument("--samples", type=int, default=1)
    s.add_argument("--feedback", type=int, default=1, help="repair rounds per sample using Lean's errors")
    s.add_argument("--min-children", type=int, default=1)
    s.add_argument("--timeout", type=int, default=900)
    s.add_argument("--cache", default=".cache/llm")
    s.add_argument("--out", default="results/run.jsonl")
    s.add_argument("--overwrite", action="store_true")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("report", help="summarise result files")
    s.add_argument("results", nargs="+")
    s.set_defaults(fn=cmd_report)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
