# Roadmap

Prioritised work items. Each item says what to build and how to tell whether it worked.
Citations refer to [literature.md](literature.md). Done items move to the bottom with the date;
item numbers are kept, so the log can refer to them.

## Next

1. **Run the pilot with real models.** `none,decoy,flat,recursive,grounded,contrastive` × masked/unmasked on
   `benchmarks/pilot.toml`. Nothing has been measured yet; every claim about which strategy is better
   waits on this. *Measure:* the report's gain over `none` **and** over `decoy`.

2. **Measure grounding.** The `grounded` strategy (recursive pass + Lean goal states, see Done) exists
   but has not been run with real models. *Measure:* rebuild gain of `grounded` over `recursive` at equal
   leakage, plus the leakage of Lean notation from the goal states into the prose (now recorded per row
   as `lean_notation` and reported as the "Lean notation" column). Hattori et al. 2025 report better step
   translations with states; whether that survives a rebuild test is open.

3. **Corrupted-step control.** Take the real explanation, change one intermediate claim (swap an
   inequality, drop a hypothesis, delete a step), and rebuild. If success does not drop, the rebuilder
   is not following the explanation. Motivated by the local perturbations of Gui et al. 2026. Include
   near-miss corruptions (off-by-one bounds, slightly weaker hypotheses), the analogue of the "nearby
   negatives" of Press & Golubev 2026.
   *Measure:* success(real) − success(corrupted), per theorem.

4. **Step-level reconstruction with diagnosis.** Rebuild one sub-proof (`have` block, case) at a time
   from its node explanation, with the rest of the proof fixed. This localizes *which* node's
   explanation is insufficient, in the spirit of diagnose-then-repair round trips (Amrollahi et al.
   2026). *Measure:* fraction of nodes rebuilt; agreement between node failures and whole-proof failures.

5. **Targeted refinement.** When step-level rebuild fails at node k, regenerate only node k's
   explanation with the rebuilder's failed attempt and Lean's error as feedback, then re-consolidate
   upward. *Measure:* whole-proof rebuild success after one refinement round vs. regenerating the whole
   explanation, at equal explainer calls.

6. **Premise context.** Give the explainer informal statements of the library lemmas the proof uses
   (docstrings or a one-line informalization, generated in dependency order as in Herald), so it can
   describe facts instead of naming lemmas. *Measure:* leakage rate down, masked success up.

7. **Claim-level faithfulness score.** Extract the intermediate claims stated in an explanation,
   formalize each as a `have` with `sorry`-free proof search, and report the fraction Lean confirms
   under the theorem's hypotheses (cf. Lean-probing in Mohammad & Sheikh 2026). A finer and
   leakage-independent signal than whole-proof rebuild.

9. **Held-out theorems.** Theorems added to Mathlib after the models' training cut-off, found by
   `git log` dates in the Mathlib checkout, to separate explanation from memorisation.

10. **Training groundwork.** Export (proof, explanation, rebuild success, leakage) as JSONL with
    reward = success − λ·leakage; best-of-n explanation selection; distillation loop as in the chess
    recipe.

11. **Waypoint agreement.** Formalize the intermediate claims of an explanation and compare them with the
    real goal chain now available from `lpe goals` (how many explanation claims match a goal the proof
    actually passes through, up to `simp`-normalization or a short proof of equivalence). A rebuilder
    variant that first writes the claims as a chain of `have … := by sorry` and then fills the gaps
    separates "the explanation names the right waypoints" from "the rebuilder can find the tactics"
    (Wang et al. 2025, chain of states). *Measure:* waypoint recall vs. rebuild success, per theorem.

12. **Leaf templates.** Describe leaves with stereotyped meaning (`rw`, `simp only`, `obtain`, `intro`,
    `constructor`) from their before/after states with a fixed template instead of a model call, as in
    Hattori et al. 2025. *Measure:* explainer calls saved at equal rebuild success.

13. **Measure contrast.** Run `contrastive` against `grounded` (same tree, same states; the only
    difference is Lean's verdict on routine alternatives). *Measure:* rebuild gain at equal leakage;
    and, per Rastogi 2026, an ablation that deletes only the "why not" sentences from contrastive
    explanations (length-matched deletion as control) to see whether the rebuilder uses them.

14. **Verdict faithfulness.** Check each explained part's "routine / non-routine" wording against the
    verdict table from `lpe contrast` (trace rows carry a `contrast` summary). A cheap instance of
    tool-checked claims (Hebbar et al. 2026). *Measure:* fraction of parts whose explanation contradicts
    Lean's verdict.

15. **Richer contrasts.** Beyond fixed closers: the original tactic with one premise removed, the
    proof's next step applied one step early, or the case split skipped. Each tells the explainer why a
    specific ingredient is needed, closer to the best-move-vs-alternative contrasts of chess.
    *Measure:* as item 13, contrastive-rich vs. contrastive.

16. **Repair rounds vs. fresh samples.** Run the rebuilder at equal attempt budgets with
    `--samples 4 --feedback 0 --all-samples` against `--samples 2 --feedback 1 --all-samples` (and count
    tokens, since a repair prompt is much longer). Li & Li 2026 find that Lean feedback never beats
    fresh resampling for their provers, and that showing the failed proof makes the model copy it.
    *Measure:* pass@k at equal attempts and at equal tokens, per strategy; if feedback does not help,
    make `--feedback 0` the default and spend the budget on samples.

17. **Benchmark size from a power calculation.** With the paired exact test on 12 theorems, a strategy
    must rebuild at least 6 more theorems than the baseline with none lost to reach p < 0.05, which only
    a very large effect can do. Use the first real pilot's discordance rate and Miller 2024's power
    formula to set the size of the next benchmark (item 9), and add file-clustered intervals since
    theorems from one Mathlib file are not independent. *Measure:* the number of theorems needed to
    detect a 15-point gain with 80% power.

## Done

- 2026-11-23 — `scripts/setup_lean.sh`: toolchain from GitHub releases + build of the benchmark's
  import closure, for machines without access to the Lean release and Mathlib cache servers.
- 2026-11-23 — `decoy` control strategy (another theorem's flat explanation) and a paired
  "gain over decoy" section in `lpe report`.
- 2026-11-24 — Goal states per tactic from Lean's info trees (`lean/GoalStates.lean`, `lpe/goals.py`,
  `lpe goals`) and the `grounded` strategy that gives them to every node of the recursive pass.
- 2026-11-25 — `contrastive` strategy: Lean's verdict on routine alternatives (`simp`, `omega`,
  `linarith`, …) at the goal each explained part starts from (`lean/TryTactics.lean`, `lpe/contrast.py`,
  `lpe contrast`); `lean_notation` leakage per result row and in `lpe report`.
- 2026-11-26 — Statistics in `lpe report` (`lpe/stats.py`): Wilson intervals per strategy, exact
  McNemar p-values with Holm adjustment and paired bootstrap intervals for the gains over `none` and
  `decoy`, pass@k from per-sample outcomes (`sample_success` in result rows; `run --all-samples` draws
  every sample for the unbiased estimator).
