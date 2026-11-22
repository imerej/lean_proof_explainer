# lean-proof-explainer

**Can a language model explain a Lean proof well enough that someone else can rebuild it from the explanation alone?**

Machine-checked proofs are becoming large and machine-generated. Lean certifies that they are *correct*,
but not that anyone *understands* them. This project measures the quality of natural-language proof
explanations with a test that cannot be gamed by fluent prose:

```
Lean proof ──explain──▶ informal explanation ──rebuild──▶ new Lean proof ──▶ Lean compiler: ✓ / ✗
```

An explanation is good if a second model, given only the theorem statement and the explanation,
writes a proof that Lean accepts.

## Idea

The explainer borrows the recursive scheme used to train chess models that explain their moves:
a position is explained from the explanations of the positions that follow it, and the consolidated
explanation becomes training data. For proofs, the analogue is a bottom-up pass over the proof tree:

1. Split the tactic proof into a tree (`have` blocks, `·` bullets, `induction ... with` cases, …).
2. Explain each composite step from its own code plus the *already written explanations* of its
   sub-steps, which are collapsed to one line (`⟦step k⟧`).
3. The root's explanation, built from all of them, is the explanation of the whole proof.

Explanations are asked to say **why** each move is made, not only what it does, and must not quote Lean.

## What is measured

For each theorem and each strategy:

| strategy | what the rebuilding model sees |
|---|---|
| `none` | the statement only (baseline: how much can it prove unaided, or from memory?) |
| `decoy` | the statement + the `flat` explanation of a *different* benchmark theorem (control) |
| `flat` | the statement + an explanation written in one pass from the whole proof |
| `recursive` | the statement + an explanation built bottom-up over the proof tree |

and, for `flat`/`recursive`, a **masked** variant in which every library lemma name from the original
proof (and any code block) is removed from the explanation.

Reported per strategy: rebuild success rate, theorems gained/lost against the `none` baseline
and against the `decoy` control,
**leakage** (fraction of the proof's lemma names that appear verbatim in the explanation), explanation length,
and the number of explainer calls.

Why these controls:

- **Memorisation.** Mathlib is in every model's training data. The `none` baseline measures what the
  rebuilder can do without help; the interesting number is the gain over it. For a cleaner test, use
  theorems added to Mathlib after the models' training cut-off.
- **Form vs. content.** A rebuilder may do better simply because the prompt is longer or sounds
  confident, or it may ignore the explanation altogether. The `decoy` control gives it a real
  explanation of the wrong theorem (the next benchmark theorem from a different file, so the pairing
  is deterministic and reuses the cached `flat` explanation). An explanation strategy is only
  conveying something about *this* proof if it beats `decoy`, not just `none`.
- **Leakage.** An "explanation" that lists the exact lemmas is a disguised copy of the code. Leakage is
  reported, and the masked condition tests whether the explanation still works once names are gone.
- **Honest checking.** A candidate proof replaces the original inside a copy of its own Mathlib file,
  truncated right after the theorem, so the context (namespaces, `variable`s, `open`s) is exact and later
  lemmas in the file cannot be used. A `#print axioms` check rejects anything relying on `sorry` or
  non-standard axioms, and `sorry`/`admit`/`native_decide` are refused outright.

## Setup

Requirements: Python ≥ 3.11, [elan](https://github.com/leanprover/elan), ~10 GB of disk.

```bash
git clone https://github.com/imerej/lean_proof_explainer && cd lean_proof_explainer
pip install -e .            # or just run `python -m lpe` from the repo root

cd lean
lake update                 # fetches Mathlib v4.36.0 (pinned in lean/lakefile.toml)
lake exe cache get          # downloads prebuilt Mathlib; skip if it fails, see below
cd ..
```

On a machine where `releases.lean-lang.org` or the Mathlib cache are unreachable but GitHub is not,
`scripts/setup_lean.sh [bench.toml ...]` does all of this: it installs elan, fetches the pinned toolchain
from the GitHub release assets, runs `lake update`, and builds only the modules the benchmark needs.

If the Mathlib cache is unavailable, build only what the benchmark needs. The pilot set was chosen
from files with small import closures (about 1,100 modules in total):

```bash
cd lean && lake build $(python3 -m lpe.modules ../benchmarks/pilot.toml) && cd ..
```

Then check that every original proof goes through the checker unchanged:

```bash
python -m lpe sanity benchmarks/pilot.toml
```

## Running the experiment

Set an API key (`ANTHROPIC_API_KEY`, or `OPENAI_API_KEY` with optional `OPENAI_BASE_URL` for any
OpenAI-compatible endpoint), then:

```bash
python -m lpe run benchmarks/pilot.toml \
    --explainer anthropic:claude-opus-5-5 \
    --reconstructor anthropic:claude-haiku-5-5 \
    --strategies none,decoy,flat,recursive --mask both \
    --samples 1 --feedback 1 \
    --out results/pilot.jsonl

python -m lpe report results/pilot.jsonl
```

A weaker rebuilder than explainer is deliberate: a strong rebuilder can often prove mid-sized lemmas
without help, which hides the effect of the explanation.

Other commands:

```bash
python -m lpe explain benchmarks/pilot.toml --theorem Commute.add_pow --strategy recursive \
    --explainer anthropic:claude-opus-5-5          # just print an explanation
python -m lpe select --out benchmarks/my_set.toml --n 50 --min-lines 12 --max-lines 40 \
    --under Mathlib/NumberTheory                   # sample a new benchmark
```

All model calls are cached in `.cache/llm/`, so re-running or re-reporting is free. Each result row in
the JSONL file keeps the full explanation, the per-node trace of the recursive pass, and the final
candidate proof.

## Layout

```
lpe/extract.py      find a declaration in a Lean file, split statement/proof, build the proof tree
lpe/explain.py      explanation strategies (none / decoy / flat / recursive), leakage and masking
lpe/reconstruct.py  rebuild a proof from statement + explanation, with Lean-error feedback
lpe/lean_check.py   splice a candidate into its original file and compile it
lpe/llm.py          small Anthropic / OpenAI-compatible clients with an on-disk cache
lpe/imports.py      import closures, to pick theorems that are cheap to check
lpe/cli.py          select · sanity · explain · run · report
benchmarks/         theorem lists (TOML)
scripts/            setup_lean.sh: toolchain + Mathlib build without the release/cache servers
docs/               ROADMAP.md (planned work), LOG.md (what changed when), literature.md
lean/               Lean project pinned to Mathlib v4.36.0
tests/              unit tests and a core-Lean fixture (no Mathlib needed)
```

## Limitations of the first version

- The proof tree comes from indentation, not from Lean's elaboration. It does not see goal states,
  so explanations are inferred from code alone. Feeding the intermediate goals (via Lean's info trees
  or a REPL) is the obvious next step.
- Automation (`simp`, `omega`, `aesop`) is treated as a single opaque step.
- Only tactic proofs are supported; term-mode and pattern-matching proofs are skipped.

## Roadmap

The prioritised plan lives in [docs/ROADMAP.md](docs/ROADMAP.md); related work, and what each paper
suggests for this project, in [docs/literature.md](docs/literature.md). In short: run the pilot with
real models, then ground explanations in Lean goal states, add stronger faithfulness controls
(corrupted steps, step-level rebuilds), and use rebuild success minus leakage as a training signal.
