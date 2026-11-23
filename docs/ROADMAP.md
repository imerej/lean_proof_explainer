# Roadmap

Prioritised work items. Each item says what to build and how to tell whether it worked.
Citations refer to [literature.md](literature.md). Done items move to the bottom with the date.

## Next

1. **Run the pilot with real models.** `none,decoy,flat,recursive,grounded` × masked/unmasked on
   `benchmarks/pilot.toml`. Nothing has been measured yet; every claim about which strategy is better
   waits on this. *Measure:* the report's gain over `none` **and** over `decoy`.

2. **Measure grounding.** The `grounded` strategy (recursive pass + Lean goal states, see Done) exists
   but has not been run with real models. *Measure:* rebuild gain of `grounded` over `recursive` at equal
   leakage, plus the leakage of Lean notation from the goal states into the prose (a new leakage form:
   check for `⊢`, `↑`, hypothesis names like `h₁`). Hattori et al. 2025 report better step translations
   with states; whether that survives a rebuild test is open.

3. **Corrupted-step control.** Take the real explanation, change one intermediate claim (swap an
   inequality, drop a hypothesis, delete a step), and rebuild. If success does not drop, the rebuilder
   is not following the explanation. Motivated by the local perturbations of Gui et al. 2026.
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

8. **Statistics in `lpe report`.** pass@k for `--samples > 1`, McNemar/sign test for paired strategy
   comparisons, bootstrap confidence intervals. Pilot-sized samples (~12) need these to say anything.

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

## Done

- 2026-11-23 — `scripts/setup_lean.sh`: toolchain from GitHub releases + build of the benchmark's
  import closure, for machines without access to the Lean release and Mathlib cache servers.
- 2026-11-23 — `decoy` control strategy (another theorem's flat explanation) and a paired
  "gain over decoy" section in `lpe report`.
- 2026-11-24 — Goal states per tactic from Lean's info trees (`lean/GoalStates.lean`, `lpe/goals.py`,
  `lpe goals`) and the `grounded` strategy that gives them to every node of the recursive pass.
