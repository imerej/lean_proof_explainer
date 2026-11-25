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

from .contrast import DEFAULT_ALTERNATIVES, ContrastRunner, probe_targets, render_verdicts, verdict_summary
from .explain import decoy_explanation, explain, leakage, mask_identifiers, pick_donor
from .extract import find_declaration, parse_proof_tree
from .goals import GoalExtractor, render_goals, step_view
from .lean_check import LeanChecker
from .llm import LLM
from .reconstruct import reconstruct
from .stats import holm, mcnemar_exact, paired_bootstrap_ci, row_pass_at_k, wilson_interval

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
    goals = _goal_extractor(args) if args.strategy in ("grounded", "contrastive") else None
    runner = _contrast_runner(args) if args.strategy == "contrastive" else None
    for entry, decl in load_bench(Path(args.bench), args.project):
        if args.theorem and entry["name"] != args.theorem:
            continue
        states = goals.states(decl) if goals else None
        contrasts = runner.contrasts(decl, states, args.min_children) if runner else None
        ex = explain(decl, args.strategy, llm, min_children=args.min_children, states=states, contrasts=contrasts)
        leak = leakage(decl, ex.text)
        print(f"## {entry['name']}  [{args.strategy}, {ex.calls} calls, leakage {leak.rate:.0%}]\n")
        print(ex.text, "\n")


# ------------------------------------------------------------------ goals


def _goal_extractor(args) -> GoalExtractor:
    return GoalExtractor(args.project, timeout=args.timeout, cache_dir=Path(args.cache).parent / "goals")


def _contrast_runner(args) -> ContrastRunner:
    alternatives = [t.strip() for t in args.alternatives.split(",") if t.strip()]
    return ContrastRunner(
        args.project,
        alternatives=alternatives,
        heartbeats=args.heartbeats,
        timeout=args.timeout,
        cache_dir=Path(args.cache).parent / "contrast",
    )


def cmd_contrast(args) -> None:
    """Print, for each explained part of each proof, what routine alternatives do on its goal."""
    extractor = _goal_extractor(args)
    runner = _contrast_runner(args)
    for entry, decl in load_bench(Path(args.bench), args.project):
        if args.theorem and entry["name"] != args.theorem:
            continue
        states = extractor.states(decl)
        contrasts = runner.contrasts(decl, states, args.min_children)
        print(f"## {entry['name']}\n")
        if contrasts.error:
            print(f"failed:\n{contrasts.error}\n")
            continue
        targets = probe_targets(decl, states, args.min_children)
        lines = decl.proof.splitlines()
        for (line, _col), target in sorted(targets.items()):
            probe = contrasts.at(*target)
            where = "whole proof" if line < 0 else lines[line].strip()
            if probe is None:
                print(f"{where}\n  (no probe)\n")
                continue
            print(f"{where}\n  [{verdict_summary(probe)}]")
            for row in render_verdicts(probe).splitlines():
                print(f"  {row}")
            missing = [v.tactic for v in probe.verdicts if v.status == "unavailable"]
            if missing:
                print(f"  (not available in this file: {', '.join(missing)})")
            print()


def cmd_goals(args) -> None:
    """Print each proof step with Lean's goal state after it (what `grounded` shows the explainer)."""
    extractor = _goal_extractor(args)
    for entry, decl in load_bench(Path(args.bench), args.project):
        if args.theorem and entry["name"] != args.theorem:
            continue
        states = extractor.states(decl)
        print(f"## {entry['name']}  ({len(states.tactics)} tactic states)\n")
        if states.error:
            print(f"goal extraction failed:\n{states.error}\n")
            continue
        print(render_goals(states.initial, limit=10**9), "\n")

        def show(node, depth=0):
            for child in node.children:
                view = step_view(child, states)
                pad = "  " * depth
                print(pad + child.text)
                if view is None:
                    print(pad + "  │ (no state found)")
                else:
                    label, shown = view
                    print(f"{pad}  │ [{label}]")
                    for line in render_goals(shown, limit=args.limit).splitlines():
                        print(f"{pad}  │ {line}")
                show(child, depth + 1)

        show(parse_proof_tree(decl.proof))
        print()


# ------------------------------------------------------------------ run


def cmd_run(args) -> None:
    project = args.project
    checker = LeanChecker(project, timeout=args.timeout)
    explainer = LLM(args.explainer, cache_dir=Path(args.cache)) if args.explainer else None
    rebuilder = LLM(args.reconstructor, cache_dir=Path(args.cache))
    strategies = args.strategies.split(",")
    goals = _goal_extractor(args) if {"grounded", "contrastive"} & set(strategies) else None
    runner = _contrast_runner(args) if "contrastive" in strategies else None
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists() and not args.overwrite:
        for line in out.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["theorem"], r["strategy"], r["masked"]))

    bench = list(load_bench(Path(args.bench), project))
    files = [entry["file"] for entry, _ in bench]
    with out.open("w" if args.overwrite else "a", encoding="utf-8") as fh:
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
                    elif strategy in ("grounded", "contrastive"):
                        states = goals.states(decl)
                        if states.error:
                            first = (states.error.strip().splitlines() or ["?"])[0]
                            print(f"! {entry['name']:<40} {strategy:<10} skipped, no goal states: {first}")
                            continue
                        contrasts = None
                        if strategy == "contrastive":
                            contrasts = runner.contrasts(decl, states, args.min_children)
                            if contrasts.error:
                                first = (contrasts.error.strip().splitlines() or ["?"])[0]
                                print(f"! {entry['name']:<40} {strategy:<10} skipped, no verdicts: {first}")
                                continue
                        ex = explain(
                            decl, strategy, explainer, min_children=args.min_children, states=states, contrasts=contrasts
                        )
                    else:
                        ex = explain(decl, strategy, explainer, min_children=args.min_children)
                    text = mask_identifiers(decl, ex.text) if masked else ex.text
                    leak = leakage(decl, text)
                    rec = reconstruct(
                        decl,
                        text,
                        rebuilder,
                        checker,
                        samples=args.samples,
                        feedback_rounds=args.feedback,
                        all_samples=args.all_samples,
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
                        "samples": args.samples,
                        "feedback": args.feedback,
                        "all_samples": args.all_samples,
                        "sample_success": rec.sample_success,
                        "attempts": len(rec.attempts),
                        "failure_reasons": [a.result.reason for a in rec.attempts if not a.result.ok],
                        "explainer_calls": ex.calls,
                        "explanation_chars": len(text),
                        "leak_rate": round(leak.rate, 3),
                        "leaked_identifiers": leak.identifiers_leaked,
                        "proof_identifiers": leak.identifiers_total,
                        "lean_notation": leak.notation,
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


STRATEGY_ORDER = {"none": 0, "decoy": 1, "flat": 2, "recursive": 3, "grounded": 4, "contrastive": 5}


def _label(key: tuple) -> str:
    return f"{key[0]}{' (masked)' if key[1] else ''}"


def cmd_report(args) -> None:
    rows = [json.loads(line) for p in args.results for line in Path(p).read_text(encoding="utf-8").splitlines()]
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["strategy"], r["masked"])].append(r)
    ordered = sorted(groups.items(), key=lambda kv: (STRATEGY_ORDER.get(kv[0][0], 9), kv[0][1]))
    print(
        "| strategy | masked | n | rebuilt | success | 95% CI | mean leak | mean chars | explainer calls | Lean notation |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|")
    for (strategy, masked), rs in ordered:
        n = len(rs)
        ok = sum(r["success"] for r in rs)
        lo, hi = wilson_interval(ok, n)
        print(
            f"| {strategy} | {'yes' if masked else 'no'} | {n} | {ok} | {ok / n:.0%} | {lo:.0%}–{hi:.0%} | "
            f"{sum(r['leak_rate'] for r in rs) / n:.0%} | {sum(r['explanation_chars'] for r in rs) / n:.0f} | "
            f"{sum(r['explainer_calls'] for r in rs) / n:.1f} | "
            f"{sum(bool(r.get('lean_notation')) for r in rs) / n:.0%} |"
        )
    print("\n95% CI: Wilson score interval over theorems.")

    # pass@k, when some rows drew more than one sample.
    max_k = max((len(r.get("sample_success") or [None]) for r in rows), default=1)
    if max_k > 1:
        ks = [k for k in (1, 2, 4, 8, 16, 32, 64) if k <= max_k]
        if max_k not in ks:
            ks.append(max_k)
        print("\npass@k (mean over theorems; n = theorems with enough samples for that k):\n")
        print("| strategy | masked | " + " | ".join(f"pass@{k}" for k in ks) + " |")
        print("|---|---|" + "---|" * len(ks))
        for (strategy, masked), rs in ordered:
            cells = []
            for k in ks:
                vals = [v for v in (row_pass_at_k(r, k) for r in rs) if v is not None]
                cells.append(f"{sum(vals) / len(vals):.0%} (n={len(vals)})" if vals else "–")
            print(f"| {strategy} | {'yes' if masked else 'no'} | " + " | ".join(cells) + " |")
        if not all(r.get("all_samples") for r in rows if r.get("sample_success")):
            print(
                "\nSome rows stopped at the first success; their pass@k is the indicator of a success within the"
                " first k samples (unbiased, but noisier than drawing all samples with `--all-samples`)."
            )

    # Paired view: which theorems does each strategy rebuild that the baseline does not?
    by_thm: dict[str, dict] = defaultdict(dict)
    for r in rows:
        by_thm[r["theorem"]][(r["strategy"], r["masked"])] = r["success"]
    baselines = [
        (("none", False), "Gain over the no-explanation baseline (theorems rebuilt only with an explanation):"),
        (("decoy", False), "Gain over the decoy control (an explanation of a different theorem):"),
    ]
    printed = False
    for base, title in baselines:
        if not any(base in v for v in by_thm.values()):
            continue
        print(f"\n{title}")
        keys = sorted(
            {k for v in by_thm.values() for k in v if k != base and k[0] != "none"},
            key=lambda k: (STRATEGY_ORDER.get(k[0], 9), k[1]),
        )
        comparisons = []
        for key in keys:
            # Only theorems that have both rows are compared.
            pairs = [(v[key], v[base]) for v in by_thm.values() if key in v and base in v]
            gained = sum(1 for a, b in pairs if a and not b)
            lost = sum(1 for a, b in pairs if b and not a)
            comparisons.append((key, pairs, gained, lost, mcnemar_exact(gained, lost)))
        adjusted = holm([c[4] for c in comparisons])
        for (key, pairs, gained, lost, p), p_holm in zip(comparisons, adjusted):
            lo, hi = paired_bootstrap_ci(pairs)
            diff = sum(int(a) - int(b) for a, b in pairs) / len(pairs) if pairs else 0.0
            print(
                f"- {_label(key)}: +{gained} / -{lost}  (n={len(pairs)})  "
                f"Δ={diff:+.0%} [{lo:+.0%}, {hi:+.0%}]  p={p:.3g}  p_Holm={p_holm:.3g}"
            )
        printed = True
    if printed:
        print(
            "\nΔ: difference in success rate on the paired theorems, with a 95% paired bootstrap interval over"
            " theorems. p: exact McNemar test on the theorems where the two disagree; p_Holm: Holm-adjusted"
            " within each section. With few theorems the bootstrap interval is too narrow; trust p. A result"
            " that is not significant is unresolved, not evidence of no effect."
        )


# ------------------------------------------------------------------ main


def _contrast_args(s: argparse.ArgumentParser) -> None:
    s.add_argument(
        "--alternatives",
        default=",".join(DEFAULT_ALTERNATIVES),
        help="comma-separated tactics tried on each part's goal (contrastive strategy)",
    )
    s.add_argument("--heartbeats", type=int, default=50000, help="per alternative, in thousands (contrastive)")


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
    s.add_argument("--strategy", default="recursive", choices=["flat", "recursive", "grounded", "contrastive"])
    s.add_argument("--explainer", required=True, help="e.g. anthropic:claude-opus-5-5")
    s.add_argument("--min-children", type=int, default=1)
    s.add_argument("--timeout", type=int, default=900, help="for goal-state extraction (grounded, contrastive)")
    s.add_argument("--cache", default=".cache/llm")
    _contrast_args(s)
    s.set_defaults(fn=cmd_explain)

    s = sub.add_parser("contrast", help="print what routine tactics do on the goal of each explained part")
    s.add_argument("bench")
    s.add_argument("--theorem")
    s.add_argument("--min-children", type=int, default=1)
    s.add_argument("--timeout", type=int, default=1800)
    s.add_argument("--cache", default=".cache/llm", help="results are cached next to it, in <parent>/contrast")
    _contrast_args(s)
    s.set_defaults(fn=cmd_contrast)

    s = sub.add_parser("goals", help="print Lean's goal state after each proof step")
    s.add_argument("bench")
    s.add_argument("--theorem")
    s.add_argument("--limit", type=int, default=1500, help="shorten states longer than this many characters")
    s.add_argument("--timeout", type=int, default=900)
    s.add_argument("--cache", default=".cache/llm", help="goal states are cached next to it, in <parent>/goals")
    s.set_defaults(fn=cmd_goals)

    s = sub.add_parser("run", help="explain, reconstruct, and check")
    s.add_argument("bench")
    s.add_argument(
        "--strategies",
        default="none,flat,recursive",
        help="comma-separated: none, decoy, flat, recursive, grounded, contrastive "
        "(decoy = another theorem's flat explanation; grounded = recursive + Lean goal states; "
        "contrastive = grounded + Lean's verdict on routine alternative tactics)",
    )
    s.add_argument("--explainer", help="model for explanations, e.g. anthropic:claude-opus-5-5")
    s.add_argument("--reconstructor", required=True, help="model that rebuilds proofs")
    s.add_argument("--mask", choices=["off", "on", "both"], default="both", help="mask Lean identifiers in explanations")
    s.add_argument("--samples", type=int, default=1)
    s.add_argument("--feedback", type=int, default=1, help="repair rounds per sample using Lean's errors")
    s.add_argument(
        "--all-samples",
        action="store_true",
        help="draw every sample even after one succeeds (costlier; gives unbiased pass@k in the report)",
    )
    s.add_argument("--min-children", type=int, default=1)
    s.add_argument("--timeout", type=int, default=900)
    s.add_argument("--cache", default=".cache/llm")
    s.add_argument("--out", default="results/run.jsonl")
    _contrast_args(s)
    s.add_argument("--overwrite", action="store_true", help="replace the output file instead of resuming it")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("report", help="summarise result files")
    s.add_argument("results", nargs="+")
    s.set_defaults(fn=cmd_report)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
