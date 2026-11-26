# Log

One entry per working session, newest last. Dates are Korea time.

## 2026-11-23 (Mon) — literature, setup script, decoy control

**Changed**

- `scripts/setup_lean.sh`: one command from a fresh machine to a checker that works on the pilot set.
  It installs elan, fetches the pinned toolchain from the GitHub release assets (for networks where
  `releases.lean-lang.org` and `cache.mathlib.org` are blocked), links it into elan, runs `lake update`,
  and builds only the import closure of the benchmark (`python3 -m lpe.modules`). On this machine the
  pilot closure was 2,669 build jobs and finished without errors.
- New `decoy` strategy: the rebuilder gets the `flat` explanation of a *different* benchmark theorem
  (the next one from another file, deterministic, so it reuses the cached flat explanation).
  `lpe report` now has a paired "gain over the decoy control" section next to the gain over `none`,
  and paired counts only use theorems that have both rows.
  Why: robustness studies of proof autoformalization find that models often prove the original
  problem instead of following the informal text they are given (Gui et al. 2026), and "it compiles"
  overstates faithfulness once Lean feedback is in the loop (Zhang et al. 2026). Comparing against
  `none` alone cannot tell content from form (longer prompt, confident tone, generic advice); an
  explanation strategy that does not beat `decoy` conveys nothing about the proof.
- `docs/literature.md` started with six papers read tonight (chess explanation via iterative search
  distillation; round-trip verification with stage diagnosis; faithfulness beyond compilation;
  robustness of proof autoformalization; Lean-probe faithfulness certification; Herald's
  dependency-ordered informalization of Mathlib; LeanSide's step-level checking of student proofs).
- `docs/ROADMAP.md` with ten prioritised items derived from them, replacing the short list in the
  README (which now links to it).

**Verified**

- `python3 -m pytest -q`: 16 passed (the two Lean tests run now that Lean is installed). New tests
  cover donor selection, the decoy explanation, and an offline `run` + `report` end to end with a fake
  checker and mock models.
- `python3 -m lpe sanity benchmarks/pilot.toml`: all 12 ok.
- Smoke run against the real Lean checker on the first two pilot theorems with mock models (the
  rebuilder writes the original proof only when shown that theorem's own explanation): `flat` 2/2,
  `decoy` 0/2, `none` 0/2, and the report shows +2 for `flat` over `decoy`. This tests the plumbing
  only; it is not an experimental result.
- No model API key was available, so no real explain/rebuild numbers exist yet.

**Next**

- Tuesday focus is proof-state grounding (ROADMAP item 2): extract goals before/after each tactic
  and feed them to the recursive explainer. The Lean setup above now makes that testable here.
- When an API key is available: run the pilot with `none,decoy,flat,recursive` (ROADMAP item 1).
- Follow up the unread references at the end of `docs/literature.md`.

## 2026-11-24 (Tue) — goal states from Lean's info trees, `grounded` strategy

**Changed**

- `lean/GoalStates.lean`: a ~70-line Lean program that elaborates a file with info trees enabled and
  prints, for every tactic in a line range, its position, syntax kind, and the pretty-printed goals
  before and after it (one JSON object per line). It runs with `lake env lean --run`, so it needs no
  extra build step and no dependency beyond the Mathlib already used by the checker.
- `lpe/goals.py`: runs it on the theorem's own file truncated after the theorem (the same source the
  checker compiles), converts positions to proof coordinates, and attaches states to the
  indentation-based proof tree: a node gets the outermost tactic starting where its line starts.
  Case alternatives, bullets and `calc` steps show the goal they start from; other steps show what is
  left after them. Long states keep only case tags and targets. Results are cached in `.cache/goals/`.
- New `grounded` strategy: the recursive pass unchanged, except that every explainer call also gets the
  goal before that part and the goal along each of its sub-steps. Because the tree and prompts are
  otherwise identical to `recursive`, the pair isolates the effect of the states (ROADMAP item 2).
  Motivation: Herald (Gao et al. 2025) and Hattori et al. 2025 both informalize tactic by tactic from
  proof states, the latter with a recursive summary over the `have` tree very close to ours; neither
  tests whether the result lets anyone rebuild the proof.
- `lpe goals bench.toml [--theorem X]` prints each step with its state, for inspecting what the
  explainer sees. `lpe run` skips (with a message) `grounded` rows whose states could not be extracted.
- `docs/literature.md`: three new entries (Hattori et al. 2025 step-wise informalization with recursive
  summarization; Wang et al. 2025 chain of states; Pantograph). `docs/ROADMAP.md`: item 2 is now
  "measure grounding"; new items 11 (waypoint agreement between explanation claims and the real goal
  chain) and 12 (template descriptions of stereotyped leaves).

**Verified**

- `python3 -m pytest -q`: 24 passed (new: record parsing and coordinate conversion, node lookup,
  state shortening, prompt contents of `grounded` vs. `recursive`, an offline `run`/`report`/`goals`
  end to end, and a test that reads real goal states from the core-Lean fixture).
- `python3 -m lpe sanity benchmarks/pilot.toml`: all 12 ok, after rebuilding the pilot closure from a
  fresh checkout with `scripts/setup_lean.sh` (2,669 jobs, under an hour on 2 cores).
- Goal extraction on all 12 pilot theorems: no failures, about 5 s per theorem; 174 of 183 proof-tree
  nodes get a state. The 9 without one are term-mode `calc` steps and continuation lines of term
  arguments, which contain no tactic.
- Smoke run with the real checker and the real goal extractor on the first two pilot theorems, with
  mock models (the rebuilder writes the original proof only if the explanation came from a prompt that
  contained goal states): `grounded` 2/2 masked and unmasked, `recursive` 0/2, `none` 0/2. This tests
  the plumbing only; it is not an experimental result.
- No model API key was available, so there are still no real explain/rebuild numbers.

**Next**

- Wednesday (explanation strategies): with goal states in hand, contrastive "why" at a node is now
  feasible: try one or two alternative tactics on the node's goal and give the explainer Lean's verdict.
- Goal states may carry Lean notation into explanations; extend `leakage` with notation markers
  (`⊢`, `↑`, `h₁`-style names) before the first real `grounded` run (ROADMAP item 2).
- When an API key is available: run the pilot with `none,decoy,flat,recursive,grounded`.

## 2026-11-25 (Wed) — contrastive "why" from Lean's verdicts, notation leakage

**Changed**

- `lean/TryTactics.lean`: elaborates the theorem's file (truncated after the theorem, as the checker
  sees it) with info trees enabled and, at requested positions, runs a list of alternative tactics on
  the goal *before* the tactic there, each in a fresh copy of the proof state and under a heartbeat
  limit. Each try is reported as `closed`, `progress` (with the remaining goals), `unchanged`,
  `failed` (with Lean's error), or `unavailable` (the tactic does not exist in this file's imports;
  the rebuilder could not use it either, so it is not shown to the explainer). Successes that only
  come from error recovery (an error message or a `sorry` in the term) count as failures.
- `lpe/contrast.py`: chooses, for each part the recursive pass explains, the goal the part starts
  from (the claim of a `have … := by`, the goal of a case or `calc` step, the theorem for the whole
  proof), matching the goal text that `goals.py` attaches, runs the Lean program once per theorem,
  and renders the verdicts for prompts. Cached in `.cache/contrast/`, keyed by the source, the probe
  list and the Lean program itself.
- New `contrastive` strategy: the `grounded` pass plus, in every explainer call, Lean's verdict on the
  routine alternatives (default `simp, omega, linarith, nlinarith, norm_num, ring, positivity, aesop`)
  at the start of that part, and an instruction to use it to say why the part is needed, or that it is
  routine. Prompts are otherwise identical to `grounded`, so the pair isolates the verdicts (tested).
  Trace rows record a `contrast` summary per node ("routine: closed directly by …" / "non-routine").
  Motivation: chess explanations are contrastive (best move vs. an alternative), and the strongest
  recent work decides the contrast with the engine rather than the explainer (Press & Golubev 2026;
  claims checked with engine tools in Hebbar et al. 2026).
- `lpe contrast bench.toml [--theorem X]` prints the verdict table per part; `run`/`explain` accept
  `--alternatives` and `--heartbeats`.
- Leakage: every result row now has `lean_notation`, the kinds of Lean-only notation found in the
  explanation (turnstile, coercion arrows, subscripted hypothesis names, `.succ`-style projections),
  and `lpe report` has a "Lean notation" column (share of rows with any). Goal states and verdicts put
  this notation in front of the explainer; lemma-name matching did not catch it (ROADMAP item 2).
- `docs/literature.md`: three new entries (BinProp contrastive chess/Go explanations; ACT-Eval
  tool-verified chess commentary; a causal test of "why not the rival" reasons). `docs/ROADMAP.md`:
  new items 13 (measure contrast, with a "why not" deletion ablation), 14 (verdict faithfulness),
  15 (richer contrasts: drop a premise, skip a case split); near-miss corruptions added to item 3.

**Verified**

- `python3 -m pytest -q`: 35 passed (new: choice of the starting goal per part, output parsing and
  grouping, rendering, prompt contents of `contrastive` vs. `grounded`, unavailable tactics, offline
  `run`/`report`/`contrast` end to end, and a test that runs alternatives in real Lean on the
  core-Lean fixture).
- `python3 -m lpe sanity benchmarks/pilot.toml`: all 12 ok, after rebuilding the pilot closure from a
  fresh checkout with `scripts/setup_lean.sh` (2,669 jobs).
- `lpe contrast` on all 12 pilot theorems: 62 explained parts, every one matched to a probe, about 70 s
  in total from an empty contrast cache. 13 parts are closed directly by a routine tactic (mostly
  `aesop`), 49 are not. Many pilot files import too little of Mathlib for `linarith`, `ring` or
  `positivity` to exist there (36 of 62 parts lack at least `norm_num`), so the effective set of
  alternatives varies by file; this is a property of the benchmark files, not a failure.
- Smoke run with the real checker, real goal states and real verdicts on the first two pilot theorems,
  with mock models (the rebuilder writes the original proof only when the explanation came from a
  prompt that contained verdicts): `contrastive` 2/2 masked and unmasked, `grounded` 0/2, `none` 0/2.
  This tests the plumbing only; it is not an experimental result.
- No model API key was available, so there are still no real explain/rebuild numbers.

**Next**

- Thursday (evaluation rigour): paired statistics in `lpe report` (ROADMAP item 8) are now the
  bottleneck for comparing six strategies on 12 theorems; then the verdict-faithfulness check
  (item 14), which needs no API key for its plumbing.
- Consider whether the alternatives should run with a fixed extra import (e.g. `Mathlib.Tactic`) so
  every file gets the same closers; that changes what "routine" means and would need its own build.
- When an API key is available: run the pilot with `none,decoy,flat,recursive,grounded,contrastive`.

## 2026-11-26 (Thu) — statistics in `lpe report`, per-sample outcomes

**Changed**

- `lpe/stats.py` (pure Python, no new dependency): Wilson score intervals, the exact McNemar test on
  discordant theorems, a seeded paired percentile bootstrap over theorems, Holm's step-down adjustment,
  and the unbiased pass@k estimator.
- `lpe report`: a 95% CI column (Wilson) for each strategy's success rate; every line of the
  "gain over `none`" and "gain over `decoy`" sections now has the paired difference in success rate with
  its bootstrap interval, the exact McNemar p-value and the Holm-adjusted p-value within that section;
  a pass@k table when rows have more than one sample. With six strategies (plus masked variants) on
  12 theorems the raw +gained/−lost counts invited reading noise as signal (ROADMAP item 8).
- `reconstruct` records whether each independent sample (including its repair rounds) compiled, and
  result rows carry `sample_success`, `samples`, `feedback` and `all_samples`. New `run --all-samples`
  keeps sampling after the first success, which costs more but gives per-theorem success counts for the
  unbiased pass@k estimator; without it the report uses "a success within the first k samples", which is
  also unbiased but noisier. Rows written before this change count as one sample.
- Choices follow the small-benchmark practice of Li & Li 2026 (paired problem-level contrasts, exact
  tests, Holm) and Miller 2024 (inference on per-question paired differences); Hariri et al. 2025 is the
  reason pass@k is shown as a description, not used for the tests.
- `docs/literature.md`: new section on evaluation statistics (Miller 2024; Hariri et al. 2025; Li & Li
  2026 on Lean feedback vs. fresh samples). `docs/ROADMAP.md`: item 8 done; new items 16 (repair rounds
  vs. fresh samples at equal budget) and 17 (size the next benchmark from a power calculation, with
  file-clustered intervals).

**Observed while testing the report**

- On synthetic 12-theorem data, the percentile bootstrap interval excluded zero for a +5/−0 gain while
  the exact test gave p = 0.0625: at this size the bootstrap is too narrow, and the report says to go by
  p. The smallest gain the pilot can call significant is 6 theorems with none lost (p = 0.031); the
  pilot can only detect very large effects, which is the argument for ROADMAP item 17.

- Fixed: `run --overwrite` ignored the existing rows but still appended to the file, so a rerun
  duplicated every row and inflated `n` in the report. It now replaces the file.

**Verified**

- `python3 -m pytest -q`: 46 passed. New tests check the statistics against hand-computed values,
  per-sample recording with and without `--all-samples` and with repair rounds, an offline
  `run --samples 4 --all-samples` + `report` end to end with mock models and a fake checker, and that
  `--overwrite` replaces rather than appends (this test fails on the previous code).
- `python3 -m lpe sanity benchmarks/pilot.toml`: all 12 ok, after rebuilding the pilot closure from a
  fresh checkout with `scripts/setup_lean.sh`.
- Smoke run with the real Lean checker on the first three pilot theorems, `none,flat`,
  `--samples 2 --all-samples --feedback 0`, with mock models (the rebuilder writes the original proof
  on every second call, and only when shown an explanation): `flat` 3/3 with pass@1 50% and pass@2 100%,
  `none` 0/3, and the paired line `+3 / -0 (n=3) p=0.25`. This tests the plumbing only; it is not an
  experimental result.
- No model API key was available, so there are still no real explain/rebuild numbers.

**Next**

- Friday (robustness & engineering): the checker starts one `lake env lean` per candidate; with
  `--all-samples` the number of checks grows with the samples, so reusing one Lean process (REPL) or
  checking candidates in parallel is now worth doing.
- ROADMAP item 14 (verdict faithfulness) still has no plumbing; item 16 needs only an API key and the
  flags added tonight.
- When an API key is available: run the pilot with `none,decoy,flat,recursive,grounded,contrastive`,
  then use its discordance rates for the power calculation of item 17.

## 2026-11-27 (Fri) — checker cache, `environment` failures, `--jobs`

**Changed**

- `lpe/lean_check.py`: verdicts are cached in `.cache/check/`, keyed by the exact file Lean compiles
  (the candidate spliced into the truncated original file, plus the `#print axioms` line), the toolchain,
  `lean/lake-manifest.json` and a checker version number. A cached result carries its original errors, so
  repair prompts are unchanged. `run` uses it by default (`--no-check-cache` to turn it off); `sanity`
  never does, since its job is to test the current build. The checker counts Lean runs and cache hits,
  and `run` prints them at the end. Rebuilders repeat themselves across samples, masked/unmasked runs and
  resumed runs, and each repeat used to cost a full `lake env lean` with Mathlib imports loaded.
- Fixed: errors in the file *before* the declaration (in practice, an import whose `.olean` is not built)
  were filtered out as "not the candidate's", after which the missing `#print axioms` output made the
  check fail with `axioms used: []`. Such failures are now reported with reason `environment` and the
  real message; a Lean/Lake exit without any diagnostic is reported as `crash`. Neither is cached, nor are
  timeouts, since they describe the machine rather than the candidate. This matters for the report:
  `failure_reasons` no longer mixes setup problems into wrong proofs.
- `run --jobs N` and `sanity --jobs N`: N theorems at once on threads (the work is waiting on Lean and on
  model APIs). A worker owns a whole theorem, so goal-state and verdict extraction for a theorem never
  run twice at once, and rows of one theorem stay in strategy order; rows are appended as they finish.
  All on-disk caches (LLM replies, goal states, verdicts, checks) are now written atomically
  (`lpe/cachefile.py`), and the LLM usage counters are locked.
- Design follows the two cheap parts of Kimina Lean Server (Dos Santos et al. 2025): a pool of
  workers and reuse of work already done. Its third part, a warm REPL per import header, is ROADMAP
  item 18 (new). `docs/literature.md` has a new section on checking infrastructure (Kimina Lean Server;
  AXLE and Shen & Shi 2026 on proof-state snapshotting, the latter two from their abstracts only).

**Verified**

- `python3 -m pytest -q`: 56 passed (Lean built). New tests use a stand-in for `lake` that counts its
  invocations: identical candidates run Lean once (also across checker instances); a changed manifest or
  toolchain invalidates the cache; `environment`, `crash` and `timeout` results are classified and never
  written; banned tokens never reach Lean; a corrupt cache entry is rechecked; concurrent atomic writes
  leave one complete file; `run --jobs 3` writes exactly the rows of `run --jobs 1` (6 theorems ×
  5 strategy/mask pairs) and resumes correctly; `sanity --jobs 4` prints in benchmark order. One test
  round-trips the cache with the real Lean checker.
- `python3 -m lpe sanity benchmarks/pilot.toml`: all 12 ok, after rebuilding the pilot closure from a
  fresh checkout with `scripts/setup_lean.sh`. Wall time 22.5 s serially, 13.0 s with `--jobs 2` on this
  2-core machine.
- A file importing a module that is not built (`Mathlib.Analysis.SpecialFunctions.Gamma.Basic`) is now
  reported as `environment` with Lean's "object file … does not exist" message, and nothing is cached.
- Smoke run with the real checker on the first four pilot theorems, `none,flat`, `--mask both
  --samples 4 --all-samples --feedback 0 --jobs 2`, with mock models (the rebuilder alternates between
  `simp` and the original proof): 48 candidates, 8 Lean runs, 40 answered from the cache; a rerun with
  `--overwrite` made no Lean runs. The mock rebuilder repeats itself far more than a real model would, so
  this shows the plumbing, not the saving to expect. No model API key was available; there are still no
  real explain/rebuild numbers.

**Next**

- Saturday (training groundwork, ROADMAP item 10): export rows as a training dataset with
  reward = success − λ·leakage; the check cache makes best-of-n explanation selection cheaper to score.
- ROADMAP item 18 (warm checker): build the leanprover-community `repl` for v4.36.0 from GitHub and
  measure seconds per check against the cold checker; the cache only helps repeated candidates.
- When an API key is available: run the pilot with all six strategies and `--jobs 2`, and record the
  cache hit rate of a real rebuilder.
