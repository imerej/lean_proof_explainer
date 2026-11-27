"""Training data from result files (`lpe export`).

A result row says how well one explanation did: whether a rebuilder could turn it back into a
proof Lean accepts, and how much of the proof's Lean vocabulary it leaked. That is a reward:

    reward = success − λ · leak_rate − μ · [explanation contains Lean-only notation]

`success` is the fraction of successful samples when the row drew every sample
(`run --all-samples`, an estimate of the rebuild probability), and 0/1 otherwise. With λ = 0 a
list of lemma names would be the best "explanation"; the leakage term keeps the reward about
conveying the proof, not copying it. With a leak gate g, an explanation whose leak rate exceeds g
gets no credit for its rebuild at all (success term 0), as in the leakage gate of CARD (Luo et al.
2026); the linear penalty alone lets a strong rebuild outweigh heavy copying.

Rows become *candidates*: one per (theorem, explanation text). Rows with the same text (the
same explanation rebuilt in two runs, or by two rebuilders) are pooled and their rewards averaged.
Only explanations written about the theorem count: `none` has no text, and a `decoy` explanation
is about another theorem. Masked rows are left out by default, because their text was edited
after the fact (lemma names replaced by `[lemma]`) and is not something an explainer should learn
to write.

Three output formats:

  records  every candidate with its reward and the fields it was computed from
  sft      best-of-n: per theorem, the highest-reward candidate, if its reward reaches
           `min_reward`, as a chat example whose input is the one-pass `flat` prompt. Explanations
           built bottom-up over the proof tree (`recursive`, `grounded`, `contrastive`) thereby
           become targets for a one-pass explainer, the distillation step of the chess recipe in
           which explanations built from successor positions are distilled back into one model.
  pairs    preference pairs per theorem (chosen, rejected) whose rewards differ by at least
           `min_margin`, for DPO-style training, again with the `flat` prompt as input.

Theorems the rebuilder proves with no explanation at all (`none` succeeded) say little about any
explanation; `skip_easy` drops them from `sft` and `pairs`. Each candidate records that baseline.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable, Iterable

from .explain import flat_prompt

# Strategies whose explanation text is about the theorem in the row.
EXPLAINING = ("flat", "recursive", "grounded", "contrastive")


@dataclass(frozen=True)
class RewardConfig:
    lam: float = 0.5  # weight of the leak rate
    notation_penalty: float = 0.0  # subtracted when Lean-only notation appears
    leak_gate: float | None = None  # above this leak rate, the rebuild counts for nothing


def success_score(row: dict) -> float:
    """Estimated rebuild probability of a row: the fraction of successful samples when every
    sample was drawn, otherwise the row's 0/1 outcome (early stopping biases the fraction)."""
    samples = row.get("sample_success") or []
    if row.get("all_samples") and samples:
        return sum(bool(s) for s in samples) / len(samples)
    return 1.0 if row.get("success") else 0.0


def reward(score: float, leak_rate: float, notation: Iterable[str], cfg: RewardConfig) -> float:
    if cfg.leak_gate is not None and leak_rate > cfg.leak_gate:
        score = 0.0  # treated as a copy of the proof: its success is not credited (cf. CARD's leakage gate)
    return score - cfg.lam * leak_rate - (cfg.notation_penalty if list(notation) else 0.0)


@dataclass
class Candidate:
    theorem: str
    file: str
    statement: str
    proof: str
    text: str
    leak_rate: float
    lean_notation: list[str]
    strategies: list[str] = field(default_factory=list)  # every strategy that produced this text
    explainers: list[str] = field(default_factory=list)
    reconstructors: list[str] = field(default_factory=list)
    masked: bool = False
    scores: list[float] = field(default_factory=list)  # one per pooled row
    baseline: bool | None = None  # did `none` rebuild this theorem? None if no such row
    reward: float = 0.0

    @property
    def score(self) -> float:
        return sum(self.scores) / len(self.scores) if self.scores else 0.0

    @property
    def id(self) -> str:
        return hashlib.sha256(f"{self.theorem}\0{self.text}".encode()).hexdigest()[:16]

    def record(self) -> dict:
        return {
            "id": self.id,
            "theorem": self.theorem,
            "file": self.file,
            "statement": self.statement,
            "proof": self.proof,
            "explanation": self.text,
            "reward": round(self.reward, 4),
            "success": round(self.score, 4),
            "rows": len(self.scores),
            "leak_rate": self.leak_rate,
            "lean_notation": self.lean_notation,
            "strategies": self.strategies,
            "explainers": self.explainers,
            "reconstructors": self.reconstructors,
            "masked": self.masked,
            "baseline_success": self.baseline,
        }


Lookup = Callable[[str, str], tuple[str, str]]  # (theorem, file) -> (statement in context, proof)


def candidates(
    rows: Iterable[dict],
    cfg: RewardConfig = RewardConfig(),
    include_masked: bool = False,
    lookup: Lookup | None = None,
) -> list[Candidate]:
    """Pool result rows into scored candidates, in first-seen order.

    Rows written before `run` recorded `statement` and `proof` need `lookup` to recover them;
    without it such rows raise a `ValueError` naming the theorem."""
    rows = list(rows)
    baseline: dict[str, bool] = {}
    for r in rows:
        if r.get("strategy") == "none":
            baseline[r["theorem"]] = baseline.get(r["theorem"], False) or bool(r.get("success"))
    pooled: dict[tuple[str, str], Candidate] = {}
    for r in rows:
        if r.get("strategy") not in EXPLAINING or (r.get("masked") and not include_masked):
            continue
        text = r.get("explanation") or ""
        if not text.strip():
            continue
        key = (r["theorem"], text)
        cand = pooled.get(key)
        if cand is None:
            statement, proof = r.get("statement"), r.get("proof")
            if statement is None or proof is None:
                if lookup is None:
                    raise ValueError(
                        f"row for {r['theorem']} has no statement/proof (written by an older `lpe run`); "
                        "pass the benchmark with --bench"
                    )
                statement, proof = lookup(r["theorem"], r.get("file", ""))
            cand = pooled[key] = Candidate(
                theorem=r["theorem"],
                file=r.get("file", ""),
                statement=statement,
                proof=proof,
                text=text,
                leak_rate=float(r.get("leak_rate", 0.0)),
                lean_notation=list(r.get("lean_notation") or []),
                masked=bool(r.get("masked")),
                baseline=baseline.get(r["theorem"]),
            )
        cand.scores.append(success_score(r))
        for attr, value in (("strategies", r["strategy"]), ("explainers", r.get("explainer")),
                            ("reconstructors", r.get("reconstructor"))):  # fmt: skip
            values = getattr(cand, attr)
            if value and value not in values:
                values.append(value)
    out = list(pooled.values())
    for c in out:
        c.reward = reward(c.score, c.leak_rate, c.lean_notation, cfg)
    return out


def _by_theorem(cands: Iterable[Candidate], skip_easy: bool) -> dict[str, list[Candidate]]:
    groups: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        if skip_easy and c.baseline:
            continue
        groups[c.theorem].append(c)
    return groups


def _rank_key(c: Candidate):
    # Higher reward first; among equals, the shorter explanation, then a stable textual order.
    return (-c.reward, len(c.text), c.text)


def best_of_n(cands: Iterable[Candidate], min_reward: float = 0.5, skip_easy: bool = False) -> list[Candidate]:
    """Per theorem, the highest-reward candidate, if it reaches `min_reward`."""
    best = []
    for _, group in _by_theorem(cands, skip_easy).items():
        top = min(group, key=_rank_key)
        if top.reward >= min_reward:
            best.append(top)
    return best


def preference_pairs(
    cands: Iterable[Candidate], min_margin: float = 0.5, skip_easy: bool = False
) -> list[tuple[Candidate, Candidate]]:
    """(chosen, rejected) pairs of explanations of the same theorem whose rewards differ by at
    least `min_margin` (and by more than zero), chosen first in rank order."""
    pairs = []
    for _, group in _by_theorem(cands, skip_easy).items():
        ranked = sorted(group, key=_rank_key)
        for i, a in enumerate(ranked):
            for b in ranked[i + 1 :]:
                # Equal rewards are never a preference, whatever the margin.
                if a.reward > b.reward and a.reward - b.reward >= min_margin:
                    pairs.append((a, b))
    return pairs


def _prompt(c: Candidate) -> list[dict]:
    system, user = flat_prompt(SimpleNamespace(statement_in_context=c.statement, proof=c.proof))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def sft_example(c: Candidate) -> dict:
    return {
        "messages": [*_prompt(c), {"role": "assistant", "content": c.text}],
        "reward": round(c.reward, 4),
        "meta": {k: v for k, v in c.record().items() if k not in ("statement", "proof", "explanation")},
    }


def pair_example(chosen: Candidate, rejected: Candidate) -> dict:
    return {
        "prompt": _prompt(chosen),
        "chosen": chosen.text,
        "rejected": rejected.text,
        "reward_chosen": round(chosen.reward, 4),
        "reward_rejected": round(rejected.reward, 4),
        "meta": {"theorem": chosen.theorem, "chosen_id": chosen.id, "rejected_id": rejected.id,
                 "chosen_strategies": chosen.strategies, "rejected_strategies": rejected.strategies},
    }  # fmt: skip
