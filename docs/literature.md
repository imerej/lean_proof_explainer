# Literature

A curated bibliography for this project. Each entry: citation and link, a short summary, and what it
suggests for this repository. Dates for arXiv preprints are submission dates where the abstract page
gives them, otherwise the month implied by the arXiv identifier. Numbers quoted are the papers' own.

## Explaining expert systems

**Bhaskar, Cheng, Chen. *Language Models that Play Chess and Explain Their Moves.* arXiv:2610.03695, 2026.**
<https://arxiv.org/abs/2610.03695>
Introduces QUEEN, a ~4B chess model: a frozen-style board encoder (Leela/Lc0 BT5) connected to a small
instruction-tuned decoder through gated cross-attention. After a question-answering curriculum for
domain adaptation, it is trained by *iterative search distillation*: positions are explained from the
explanations of their successor positions found by search, the consolidated explanations are distilled
back into the model, and the loop repeats (seven iterations, Elo reported to rise from 1782 to 2697).
Explanations are judged fluent but somewhat less conceptually coherent than a frontier general model's.
*Relevance to this repo:* this is the recipe the `recursive` strategy imitates on proof trees; the
missing half is the distillation loop (ROADMAP: training groundwork). It also shows that the quality of
the *successor* explanations bounds the consolidated one, which argues for grounding node
explanations in real goal states rather than code alone.

## Round-trip and faithfulness

**Amrollahi, Lopez, Barrett. *Faithful Autoformalization via Roundtrip Verification and Repair.* arXiv:2604.25031, 2026.**
<https://arxiv.org/abs/2604.25031>
Formalizes a statement, translates the formalization back to natural language, re-formalizes it, and
asks a formal tool whether the two formalizations are logically equivalent; no ground-truth annotation
is needed. When the check fails, a diagnosis function attributes the failure to one translation stage
and a scoped repair operator fixes only that stage. Evaluated on two bodies of Texas statutes; scoped,
diagnosis-guided repair beats whole-pipeline retries, and its benefit depends on diagnosis being right.
*Relevance to this repo:* our pipeline is the proof-level version of this round trip (proof →
explanation → proof). Their *diagnose-then-repair-one-stage* idea maps to the recursive tree: when a
rebuild fails, locate the proof node whose explanation was insufficient (e.g. by step-level rebuilds)
and regenerate only that node's explanation.

**Zhang, Gallardo Candela, Murthy, Xie, Wang, Raissi. *Beyond Compilation: Evaluating Faithful Natural-Language-to-Lean Statement Formalization.* arXiv:2606.31002, 2026.**
<https://arxiv.org/abs/2606.31002>
Argues that "it compiles" overstates the quality of statement autoformalization and accepts an output
only if it compiles *and* two judges from different model families agree it is faithful; this criterion
matches a human majority on 91.5% of audited cases. Across eight systems the gap between "compiles" and
"accepted" is 1.3–29.5 points, and about half of the gain from Lean-error feedback does not survive the
semantic check.
*Relevance to this repo:* our success criterion is safer than theirs because the theorem statement is
fixed and only the proof is rebuilt, so "Lean accepts" really is the target. The warning transfers to
our feedback loop, though: Lean feedback can make the rebuilder succeed *despite* the explanation. This
is part of the motivation for the `decoy` control and for reporting first-attempt success separately.

**Gui, Yang, Shi. *Evaluating the Robustness of Proof Autoformalization in Lean 4.* arXiv:2606.14867, 2026.**
<https://arxiv.org/abs/2606.14867>
Builds a benchmark from miniF2F and MATH-500 with two kinds of perturbation of an informal
theorem–proof pair: global paraphrases, and local edits to a value, symbol or proof step. Seven recent
proof autoformalizers are evaluated; correctness shifts under paraphrase, and models mostly fail to stay
faithful to locally perturbed proofs, i.e. they prove the original instead of following the text.
*Relevance to this repo:* direct evidence that a rebuilder may ignore the explanation it is given.
Two cheap controls follow: give the rebuilder an unrelated explanation (`decoy`, implemented), and
corrupt one step of the real explanation and measure whether success drops (ROADMAP).

**Mohammad, Sheikh. *The Faithfulness Gap: Certifying Semantic Equivalence Between Natural-Language and Formal Mathematical Statements.* arXiv:2606.16541, ICML 2026 workshop (MuslML).**
<https://arxiv.org/abs/2606.16541>
Proposes Bidirectional Provability Fingerprinting: probe statements derived from the natural-language
text are tested against a candidate formalization by asking Lean/Mathlib for entailments, giving a
graded equivalence score without a reference formalization, plus counterfactual probes. Releases
DRIFTBENCH (2,183 pairs); reports 89.6% detection of drifted formalizations at 3% false positives, and
uses the score as a decoding reward.
*Relevance to this repo:* the "probe with Lean" idea suggests a leakage-free faithfulness check for
explanations: extract the intermediate claims an explanation states, formalize each, and check that each
is provable from the hypotheses (a claim-level score, finer than whole-proof rebuild).

## Informalization of Mathlib

**Gao, Wang, Jiang, Gao, Qin, Xu, Dong. *Herald: A Natural Language Annotated Lean 4 Dataset.* ICLR 2025. arXiv:2410.10878.**
<https://arxiv.org/abs/2410.10878>
Informalizes Mathlib at scale. A static analyzer builds the dependency DAG of declarations, and
declarations are translated in dependency order so that each prompt contains the informal statements
of what it uses (without this, the model invented meanings for undefined dependencies). Proofs are
translated tactic by tactic *using proof states* and then recombined; candidates are filtered by
compilation and by back-translation plus an entailment-style comparison. A statement formalizer trained
on the data reports 96.7% pass@128 on miniF2F-test (the abstract gives 93.2%; the paper is inconsistent).
*Relevance to this repo:* two concrete ideas. (1) Feed the explainer informal statements of the
*premises* the proof uses (dependency-ordered context), not just the proof's own sub-steps. (2) Their
tactic-by-tactic translation from proof states is the same grounding our ROADMAP lists as the main
weakness of v0.

## Proof feedback for people

**Guo, Kothari, Bhattacharyya, Patel, Lu, Donovan, Ranade, Mehta, Voss, Hartmann. *LeanSide: A Formally Verified Co-Reasoning System for Natural-language Proofs.* arXiv:2610.00760, 2026.**
<https://arxiv.org/abs/2610.00760>
Students write natural-language proofs; each step is autoformalized against a curated library of small
lemmas and checked in Lean, and the compiler's verdict is turned back into step-level feedback.
Deployed in an undergraduate optimization/linear-algebra course with 23 students and 137 proofs; it
flagged a fraction of correct proofs as wrong (11 of 56), showing how often step-level formalization,
not the mathematics, is the failure point.
*Relevance to this repo:* step-level checking of an explanation is feasible in practice and gives
localized feedback. Its false alarms are a warning for any step-level rebuild metric we add: a failed
step rebuild can mean a weak rebuilder rather than a weak explanation, so step metrics need the same
`none`/`decoy` controls as whole-proof rebuilds.

## Proof states and step-wise informalization

**Hattori, Matsuzaki, Fujiwara. *Natural Language Translation of Formal Proofs through Informalization of Proof Steps and Recursive Summarization along Proof Structure.* arXiv:2509.09726, 2025.**
<https://arxiv.org/abs/2509.09726> (first author's surname is spelled "Hattrori" on the arXiv page)
The closest prior work to this repository. For Lean 4/Mathlib proofs, an extended LeanDojo extractor
supplies each tactic with its goal state before and after; every step is informalized from a
hand-written per-tactic template filled with those states plus LLM-written explanations of the cited
Mathlib lemmas; `have` blocks define a tree, and the tree is summarized recursively from the innermost
sub-proofs outward. Evaluated by hand (one annotator) on 38 proofs from a calculus textbook: step
correctness rises from about 53% with neither templates nor the premise library to about 89% with
both, while recursive summarization helps the final text only slightly. There is no rebuild/round-trip
test; formal notation still leaks into the output.
*Relevance to this repo:* independent support for both halves of the `grounded` strategy (goal states
per step, recursive consolidation over the `have` tree), and for ROADMAP item 6 (premise context): in
their ablation the premise library mattered more than the templates. What this repository adds is the
rebuild test with `none`/`decoy` controls, which would also tell whether their 89% "correct steps"
translate into explanations someone can act on. Their per-tactic templates are a cheap thing to try at
the leaves of our tree (`rw`, `simp`, `obtain` have stereotyped meanings).

**Wang, Yang, Li, Zhang, Zhou, Dong, Wen. *Translating Informal Proofs into Formal Proofs Using a Chain of States.* arXiv:2512.10317, 2025.**
<https://arxiv.org/abs/2512.10317>
The reverse direction: to formalize an informal proof, a small model first writes a *chain of formal
proof states* that follows the informal argument, and tactics are then searched between consecutive
states (off-the-shelf automation first, a reasoning model second, with regeneration of failing tactics
or states). Training data are proof states read from elaboration trees over Mathlib and Lean Workbook.
Reported to reach about 69% on miniF2F-test at a small sampling budget; the ablation attributes most of
the gain to the state-repair loop rather than to the tactic generator.
*Relevance to this repo:* suggests that the most useful content of an explanation, for a rebuilder, is
the sequence of intermediate goals; the tactics between them are often found by automation. Two
concrete uses: (a) measure what fraction of each explanation's intermediate claims match the real goal
chain (a cheap faithfulness proxy, complementary to ROADMAP item 7); (b) a rebuilder variant that first
formalizes the explanation's claims as a chain of `have`s and then fills the gaps, which separates
"the explanation states the right waypoints" from "the rebuilder can find the tactics".

**Aniva, Sun, Miranda, Barrett, Koyejo. *Pantograph: A Machine-to-Machine Interaction Interface for Advanced Theorem Proving, High Level Reasoning, and Data Extraction in Lean 4.* arXiv:2410.16429, 2024.**
<https://arxiv.org/abs/2410.16429>
A Lean 4 library and REPL (with Python bindings) for driving proofs from another program: inspect goals,
run tactics incrementally (including partial `conv` and `calc`), and turn each `sorry` in a draft into
an open goal. Its frontend command processes a whole file and returns (goal before, tactic, goal after)
triples, which is the same data LeanDojo extracts, without Docker and with explicit tracking of goals
that share metavariables. Demonstrated with a draft-sketch-prove loop on miniF2F.
*Relevance to this repo:* the reference design for what `lean/GoalStates.lean` now does in about 60
lines (walk the info trees, print states per tactic). If we need interactive steps later (trying
alternative tactics at a node for contrastive "why" explanations, or step-level rebuilds without
recompiling the file), Pantograph's goal-state API is the tool to adopt rather than growing our script.

## Not yet read / to follow up

- arXiv:2603.24372 (formal–informal translation; the page could not be fetched on 2026-11-23).
- *Do Chess Explanations Reflect Model Decisions? Behavioral and Token-Level Tests of LLM Reasoning Faithfulness*, NeurIPS 2026 (<https://neurips.cc/virtual/2026/169063>): counterfactual tests of explanation faithfulness in chess.
- The leanprover-community `repl` (tactic mode with proof-state ids), as an alternative to Pantograph for step-level rebuilds.
- Quan, Valentino, Dennis, Freitas. *Faithful and Robust LLM-Driven Theorem Proving for NLI Explanations*, arXiv:2505.24264 (prover-feedback refinement of explanations; relevant to iterative refinement).
