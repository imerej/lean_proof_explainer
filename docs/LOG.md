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
