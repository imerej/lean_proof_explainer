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

## Contrastive explanation and checking claims against a tool

**Press, Golubev. *Explaining Chess and Go in Natural Language.* arXiv:2610.23166, 2026.**
<https://arxiv.org/abs/2610.23166>
Treats a chess explanation as *contrastive*: given the engine's best move and a weaker alternative, it
should name a property (a "motif", written as a boolean function on positions) that holds after the best
line and not after the alternative. Positives and negatives are sampled by letting Stockfish play out
both lines, plus "nearby negatives" that share a prefix with a positive and diverge late, which catch
motifs that only look right. A motif's score is its worst accuracy over the three groups. Their
training-free method (BinProp: an LLM proposes motifs over a large position corpus, then motifs are
ranked per move) raises the average score across eight models from about 0.08 to about 0.24, and
transfers to Go. (Read from the abstract page and the first part of the paper only.)
*Relevance to this repo:* the contrast is decided by the engine, not by the explainer, which is the
design of the new `contrastive` strategy: Lean decides which routine alternatives fail at a goal, and
the explainer only translates the verdict. Their "nearby negatives" suggest that the corrupted-step
control (ROADMAP item 3) should include near-miss corruptions (a bound off by one, a weaker hypothesis),
not only gross ones, and their worst-group score suggests reporting the worst of the control gaps.

**Hebbar, Sheng, Oh, Viswanath. *Hallucinations on the Board: Tool-Augmented Evaluation of LLM Chess Commentary.* arXiv:2608.04240, 2026.**
<https://arxiv.org/abs/2608.04240>
ACT-Eval splits generated chess commentary into atomic claims, splits those into verifiable
sub-claims, and has a judge check each one with about fifteen Stockfish-backed tools (board state,
attacks, move simulation, evaluation); a claim counts only if all its parts check out. With tools, the
judge's sub-claim error rate drops from about 22% to about 9%, and bad-move detection becomes reliable,
while a fluency-style judge still rated many commentaries highly that the tool check found mostly
wrong. Recall of expert-annotated claims stays at most about 61%. Long variations remain hard to verify.
*Relevance to this repo:* a concrete template for the claim-level score of ROADMAP item 7: decompose
an explanation into atomic claims, and verify each with the formal tool. It also gives a cheap first
check for contrastive explanations: every "this cannot be done by routine arithmetic" or "this is
routine" sentence can be compared with the verdict table that `lpe contrast` produced.

**Rastogi. *Does a model's stated reason for rejecting a candidate do any work?* arXiv:2609.30151, 2026.**
<https://arxiv.org/abs/2609.30151>
A causal test of "why not the other option" reasons in multi-hop question answering: the model picks an
answer and names a rejected rival plus the attribute the rival lacks; the experimenter then inserts a
real sentence giving the rival that attribute and asks again, with length-matched irrelevant insertions
and insertions at an unnamed option as controls. The relevant edit at the named rival moves the choice
more than the irrelevant one in the main experiment (odds ratio about 3.6), but the effect does not
replicate on other checkpoints, and the author presents the result as a bound rather than a finding.
*Relevance to this repo:* the same intervention logic tests whether the "why not" sentences of a
`contrastive` explanation are used by the rebuilder: remove (or invert) only those sentences and compare
rebuild success, with a length-matched deletion as control. Its fragile effect sizes are another
argument for paired statistics before claiming any strategy difference (ROADMAP item 8, done 2026-11-26).

## Evaluation statistics on small benchmarks

**Miller. *Adding Error Bars to Evals: A Statistical Approach to Language Model Evaluations.* arXiv:2411.00640, 2024.**
<https://arxiv.org/abs/2411.00640>
Treats an eval as a sample from a larger population of questions and derives standard errors for its
score, with clustered standard errors when questions come in groups (in its examples these are up to
about three times the naive ones). For comparing two models it recommends working with per-question
paired differences, which removes the shared difficulty of each question, and reporting the difference,
its standard error and the correlation between the models. It also suggests drawing several answers per
question to reduce within-question noise (without lowering the temperature, which changes the quantity
being measured) and gives a power formula for the number of questions needed to detect a given effect.
*Relevance to this repo:* every strategy comparison here is paired by theorem, which is what `lpe report`
now uses. Benchmark theorems also cluster by Mathlib file (and the decoy pairs files deliberately), so
file-clustered intervals are the natural next refinement; and the power formula says how large the
benchmark must grow before a realistic gain can be seen at all (ROADMAP item 17).

**Hariri, Samandar, Hinczewski, Chaudhary. *Don't Pass@k: A Bayesian Framework for Large Language Model Evaluation.* arXiv:2510.04265, 2025.**
<https://arxiv.org/abs/2510.04265>
Argues that pass@k gives unstable rankings when only a few trials per question are affordable, as on
small competition sets. It models each question's outcome with a Dirichlet prior over rubric categories,
which gives closed-form posterior means and credible intervals; with a uniform prior the posterior mean
orders models as average accuracy over trials does. The proposed rule is to report the posterior mean
with an interval, call a difference only when the intervals separate, and add trials until the intervals
are narrow enough.
*Relevance to this repo:* a warning against reading too much into pass@k columns from a handful of
samples. The report therefore keeps pass@k as a description and bases its tests on paired per-theorem
outcomes; drawing all samples (`--all-samples`) gives the per-theorem success rates that an average or
posterior summary needs.

**Li, Li. *Repair, Retry, and the Cost of Hindsight: Verifier Feedback in Formal Theorem Proving.* arXiv:2610.22698, 2026.**
<https://arxiv.org/abs/2610.22698>
Asks whether showing a prover its failed Lean proof and the compiler error helps it repair the proof,
compared with simply sampling a fresh attempt. Controlled single-step experiments vary what is shown
(the failed proof, a correct or a shuffled error message, another theorem's failed proof), and
sequential experiments run long repair histories. Seeing its own failed proof makes the model copy it
and lowers success; correct diagnostics help one model on outdated syntax but not the other; and at equal
attempts or equal tokens no feedback condition beats fresh resampling. Analysis uses problem-level
paired bootstrap intervals, exact randomization tests and Holm adjustment because the problem banks are
small.
*Relevance to this repo:* `lpe run` spends rebuild attempts on repair rounds (`--feedback 1` by
default). Whether that budget would be better spent on independent samples is an empirical question for
our rebuilder, and it interacts with the explanation: a repair prompt shows the explanation again next to
a failed proof. It also supports the statistical choices made in `lpe report` (paired by theorem, exact
tests, Holm). See ROADMAP item 16.

## Checking infrastructure

**Dos Santos, de Saxcé, Wang, Wang, Bakšys, Ünsal, Liu, Liu, Li. *Kimina Lean Server: A High-Performance Lean Server for Large-Scale Verification.* arXiv:2504.21230, 2025 (v3 December 2025; MATH-AI workshop).**
<https://arxiv.org/abs/2504.21230>
A REST server around the official Lean REPL for checking many proofs at once. It keeps a pool of REPL
processes, one per core, and splits each submitted file into its imports and its body; the imports are
the key of an LRU cache of "warm" REPLs that have already loaded them, so only the body is elaborated.
Loading Mathlib is the dominant start-up cost, and on about 9.4k competition proofs the import cache
roughly halves the total time (reported 1.94× on 64 cores) while near-linear scaling with cores gives
the rest; the authors report 1.5–2× over two other Python Lean clients.
*Relevance to this repo:* our checker pays the import cost on every candidate (`lake env lean` on a fresh
file). The two cheap parts of this design are now in `lpe run`: a verdict cache for identical candidates
and per-theorem parallelism (`--jobs`). The warm-REPL part is the next step for checker speed
(ROADMAP item 18); since every candidate for a theorem shares the same truncated file prefix, a REPL
environment per theorem would let the checker elaborate only the declaration.

**Xin, Schneidman, Cummins, Ram, Ganesh, Limperg. *AXLE: A Cloud Infrastructure for Lean 4 Theorem Proving Utilities.* arXiv:2606.26442, 2026.**
<https://arxiv.org/abs/2606.26442> (abstract read; the full text was not)
A hosted service offering fourteen Lean metaprogramming tools, among them strict proof verification,
declaration metadata extraction, source manipulation and deterministic proof repair and simplification,
with per-request isolation and several Lean/Mathlib versions side by side, reachable from a Python SDK,
a CLI and an HTTP API. The abstract motivates it by noting that existing tooling parallelizes compilation
but not proof verification at scale.
*Relevance to this repo:* a reminder that "strict verification" (banned tokens, axiom checks, no
environment tampering) is a tool of its own, which our `lean_check.py` reimplements by hand; and its
proof-simplification utilities could normalize rebuilt proofs before comparing them with the original.
Not usable here directly, since the workspace cannot reach outside services.

**Shen, Shi. *Keep the Proof State Live: Snapshotting for Efficient Tactic Search in Lean 4.* arXiv:2605.25556, 2026.**
<https://arxiv.org/abs/2605.25556> (abstract read; the full text was not)
Measures where time goes when many proof branches are explored from one partially written proof:
loading imports (about a minute per branch in their setting) and re-elaborating the theorem up to the
target goal together take over 99% of the wall time. Capturing the elaborated proof state once, through a
small extension of the Lean language server, and reusing it across branches gives 5.6–50× speedups
(median 9.7×) on miniF2F problems, and the authors stress that this is complementary to import caching.
*Relevance to this repo:* step-level reconstruction (ROADMAP item 4) and richer contrasts (item 15)
re-elaborate the same proof prefix for every candidate sub-proof. `lean/TryTactics.lean` already does the
snapshot idea inside one Lean run (alternatives are tried on saved proof states); step-level rebuild
should be built the same way, with all candidates for a node checked in one elaboration of the prefix,
rather than one file per candidate.

## Not yet read / to follow up

- arXiv:2603.24372 (formal–informal translation; the page could not be fetched on 2026-11-23).
- *Do Chess Explanations Reflect Model Decisions? Behavioral and Token-Level Tests of LLM Reasoning Faithfulness*, NeurIPS 2026 (<https://neurips.cc/virtual/2026/169063>): counterfactual tests of explanation faithfulness in chess.
- The leanprover-community `repl` (tactic mode with proof-state ids), as an alternative to Pantograph for step-level rebuilds.
- Quan, Valentino, Dennis, Freitas. *Faithful and Robust LLM-Driven Theorem Proving for NLI Explanations*, arXiv:2505.24264 (prover-feedback refinement of explanations; relevant to iterative refinement).
