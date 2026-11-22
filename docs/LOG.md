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
