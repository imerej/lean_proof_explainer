"""Small-sample statistics for `lpe report`.

The pilot has about a dozen theorems, so everything here is exact or resampling-based rather than
relying on normal approximations: Wilson intervals for success rates, the exact McNemar (sign) test
for paired strategy comparisons, a paired percentile bootstrap over theorems for the difference in
success rate, Holm's step-down adjustment across the comparisons of one report section, and the
unbiased pass@k estimator for runs with several samples per theorem.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def wilson_interval(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (95% by default)."""
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(gained: int, lost: int) -> float:
    """Two-sided exact McNemar p-value from the discordant pair counts.

    Under the null hypothesis each discordant theorem is equally likely to go either way, so this is
    a two-sided binomial test of `gained` out of `gained + lost` at p = 1/2.
    """
    n = gained + lost
    if n == 0:
        return 1.0
    k = min(gained, lost)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired_bootstrap_ci(
    pairs: Sequence[tuple[bool, bool]],
    resamples: int = 10000,
    level: float = 0.95,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap interval for mean(a) - mean(b), resampling theorems (pairs) with replacement.

    Deterministic for a given seed, so a report is reproducible.
    """
    n = len(pairs)
    if n == 0:
        return (0.0, 0.0)
    diffs = [int(a) - int(b) for a, b in pairs]
    rng = random.Random(seed)
    stats = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(resamples))
    lo = stats[int(math.floor((1 - level) / 2 * resamples))]
    hi = stats[min(resamples - 1, int(math.ceil((1 + level) / 2 * resamples)) - 1)]
    return (lo, hi)


def holm(pvalues: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values (family-wise error control), in the input order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate of pass@k from n independent samples of which c succeeded (k <= n)."""
    if k > n:
        raise ValueError(f"pass@{k} needs at least {k} samples, got {n}")
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def row_pass_at_k(row: dict, k: int) -> float | None:
    """pass@k estimate for one theorem, or None if the row has too few samples.

    With all samples drawn this is the unbiased estimator. When sampling stopped at the first success,
    "some success among the first k samples" is still an unbiased (higher-variance) estimate, because
    the first k samples are k independent draws: it is 1 if the first success came within k samples,
    0 if k samples were drawn and all failed.
    """
    outcomes = row.get("sample_success")
    if outcomes is None:
        return float(bool(row.get("success"))) if k == 1 else None
    if row.get("all_samples"):
        return pass_at_k(len(outcomes), sum(map(bool, outcomes)), k) if k <= len(outcomes) else None
    if any(outcomes[:k]):
        return 1.0
    return 0.0 if len(outcomes) >= k else None
