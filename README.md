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
| `grounded` | as `recursive`, but every explainer call also sees Lean's goal state before that part of the proof and after each of its sub-steps |
| `contrastive` | as `grounded`, plus Lean's verdict on routine alternatives (`simp`, `omega`, `linarith`, `nlinarith`, `norm_num`, `ring`, `positivity`, `aesop`) tried on the goal each explained part starts from, so the explanation can say why the part is needed or that it is routine |

and, for `flat`/`recursive`/`grounded`/`contrastive`, a **masked** variant in which every library lemma name from the original
proof (and any code block) is removed from the explanation.

Reported per strategy: rebuild success rate, theorems gained/lost against the `none` baseline
and against the `decoy` control,
**leakage** (fraction of the proof's lemma names that appear verbatim in the explanation), explanation length,
the number of explainer calls, and how often Lean-only notation (`⊢`, coercion arrows, hypothesis names
like `h₁`, `.succ`-style projections) appears in the explanation, which matters once goal states are in the prompts.

Because the benchmark is small, `lpe report` also gives the uncertainty: a 95% Wilson interval for each
success rate, and, for every gain over `none` and over `decoy`, the theorems gained and lost, the
difference in success rate with a paired bootstrap interval over theorems, an exact McNemar p-value and
its Holm adjustment within the section. With 12 theorems a strategy needs at least 6 gained and none lost
to reach p < 0.05, so the pilot can only detect large effects. When `--samples` is above 1, the report
adds pass@k; rows record the outcome of every sample drawn (`sample_success`), and `--all-samples`
keeps sampling after the first success so that pass@k uses the unbiased estimator instead of
"success within the first k samples".

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
    --strategies none,decoy,flat,recursive,grounded,contrastive --mask both \
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
python -m lpe goals benchmarks/pilot.toml --theorem Commute.add_pow
                                                   # each proof step with Lean's goal state after it
python -m lpe contrast benchmarks/pilot.toml --theorem Commute.add_pow
                                                   # what routine tactics do on each part's goal
python -m lpe select --out benchmarks/my_set.toml --n 50 --min-lines 12 --max-lines 40 \
    --under Mathlib/NumberTheory                   # sample a new benchmark
```

Training data. `export` turns result files into data for training an explainer:

```bash
python -m lpe export results/pilot.jsonl --format records --out data/candidates.jsonl
python -m lpe export results/pilot.jsonl --format sft   --out data/sft.jsonl   --skip-easy
python -m lpe export results/pilot.jsonl --format pairs --out data/pairs.jsonl --min-margin 0.5
```

Each explanation of a theorem (unmasked rows of `flat`, `recursive`, `grounded`, `contrastive`; rows with
identical text are pooled) gets **reward = success − λ·leak_rate − μ·[Lean notation]** (`--lam`, default
0.5; `--notation-penalty`, default 0). Success is the fraction of successful samples when the run used
`--all-samples`, otherwise 0/1. `--leak-gate g` gives no success credit to an explanation that leaks more
than a fraction g of the proof's lemma names. `records` lists every candidate with its reward; `sft` keeps
the best explanation per theorem (if its reward reaches `--min-reward`, default 0.5) as a chat example
whose input is the one-pass `flat` prompt, so explanations built bottom-up can be distilled into a
one-pass explainer; `pairs` writes (chosen, rejected) explanations of the same theorem whose rewards
differ by at least `--min-margin`. `--skip-easy` drops theorems the rebuilder proved with no
explanation. Result rows written by older versions lack the statement and proof; pass `--bench` for them.
No training is done here, and no real export exists yet: the pilot has not been run with real models.

Goal states come from `lean/GoalStates.lean`, a small Lean program that elaborates the theorem's file
(truncated after the theorem, as the checker sees it) with info trees enabled and prints the goals before
and after every tactic, the same data the editor's infoview shows. They are cached in `.cache/goals/`.

The verdicts for `contrastive` come from `lean/TryTactics.lean`, which elaborates the same truncated file
and, at the goal where each explained part starts (the claim of a `have … := by`, the goal of a case, the
theorem itself for the whole proof), runs each alternative in a fresh copy of the proof state with a
heartbeat limit (`--heartbeats`, default 50000). A tactic "closes" the goal, "does not finish" (the
remaining goal is shown), or "fails" (with the first line of Lean's error). Change the list with
`--alternatives` (comma-separated, so tactics containing commas cannot be given). Results are cached in
`.cache/contrast/`.

All model calls are cached in `.cache/llm/`, so re-running or re-reporting is free. Each result row in
the JSONL file keeps the full explanation, the per-node trace of the recursive pass, and the final
candidate proof.

Lean's verdict on a candidate is cached in `.cache/check/`, keyed by the exact file Lean compiles, the
toolchain and `lean/lake-manifest.json` (so a Mathlib bump invalidates it). A rebuilder that proposes
the same candidate twice (another sample, the masked run, a rerun after an interruption) costs one Lean
run. Timeouts, crashes and failures of the surrounding file (`environment`, e.g. a missing `.olean`) are
never cached and are reported under their own reason rather than as a wrong proof. `--no-check-cache`
turns this off; `sanity` never uses it. At the end of a run the checker prints how many Lean runs it
made and how many candidates the cache answered.

`run --jobs N` (and `sanity --jobs N`) works on N theorems at once; each worker owns a whole theorem
(all its strategies, in order), and rows are appended as they finish, so their order in the file differs
from a serial run but the rows themselves do not. Every worker starts its own Lean process with the
theorem's imports loaded, so memory, not cores, is usually the limit.

## Layout

```
lpe/extract.py      find a declaration in a Lean file, split statement/proof, build the proof tree
lpe/explain.py      explanation strategies (none / decoy / flat / recursive / grounded), leakage and masking
lpe/goals.py        goal states per tactic (runs lean/GoalStates.lean), attached to proof-tree nodes
lpe/contrast.py     routine alternatives tried at each part's goal (runs lean/TryTactics.lean)
lpe/reconstruct.py  rebuild a proof from statement + explanation, with Lean-error feedback
lpe/lean_check.py   splice a candidate into its original file and compile it; verdict cache
lpe/cachefile.py    atomic writes for the on-disk caches (safe with `run --jobs`)
lpe/stats.py        intervals, paired tests and pass@k for `lpe report`
lpe/dataset.py      rewards, best-of-n and preference pairs for `lpe export`
lpe/llm.py          small Anthropic / OpenAI-compatible clients with an on-disk cache
lpe/imports.py      import closures, to pick theorems that are cheap to check
lpe/cli.py          select · sanity · explain · goals · contrast · run · report · export
benchmarks/         theorem lists (TOML)
scripts/            setup_lean.sh: toolchain + Mathlib build without the release/cache servers
docs/               ROADMAP.md (planned work), LOG.md (what changed when), literature.md
lean/               Lean project pinned to Mathlib v4.36.0; GoalStates.lean (info-tree goal dump),
                    TryTactics.lean (alternative tactics on recorded goals)
tests/              unit tests and a core-Lean fixture (no Mathlib needed)
```

## Limitations of the first version

- The proof tree comes from indentation, not from Lean's elaboration. Goal states (the `grounded`
  strategy) are attached to tree nodes by the position of each node's first line, so a step whose tactic
  does not start at the beginning of its line (e.g. after `<;>` on a continuation) gets no state of its own.
  Whether goal states improve rebuild success has not been measured yet.
- Automation (`simp`, `omega`, `aesop`) is treated as a single opaque step.
- The `contrastive` alternatives are a fixed list of closers; they say whether a goal is routine, not
  why a particular lemma or case split was needed. Neither goal states nor contrasts have been tested with
  real models yet.
- Only tactic proofs are supported; term-mode and pattern-matching proofs are skipped.

## Roadmap

The prioritised plan lives in [docs/ROADMAP.md](docs/ROADMAP.md); related work, and what each paper
suggests for this project, in [docs/literature.md](docs/literature.md). In short: run the pilot with
real models (including the goal-state-grounded explanations), add stronger faithfulness controls
(corrupted steps, step-level rebuilds), and use rebuild success minus leakage as a training signal.
