"""Small-sample statistics for the report: intervals, paired bootstrap, ranking metrics."""

from __future__ import annotations

import math
import random


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denominator = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    # The bounds are exact at the extremes; avoid 0.9999999999 from float rounding.
    lo = 0.0 if successes == 0 else max(0.0, (center - margin) / denominator)
    hi = 1.0 if successes == n else min(1.0, (center + margin) / denominator)
    return (lo, hi)


def paired_bootstrap(
    a: list[float], b: list[float], iterations: int = 10000, seed: int = 20261124
) -> tuple[float, float, float]:
    """Mean of b − a over paired items and its 95% percentile interval."""
    if len(a) != len(b) or not a:
        raise ValueError("配对样本长度必须相同且非空")
    diffs = [y - x for x, y in zip(a, b, strict=True)]
    mean = sum(diffs) / len(diffs)
    if all(d == diffs[0] for d in diffs):
        return (float(mean), float(mean), float(mean))
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(iterations))
    return (mean, means[int(0.025 * iterations)], means[int(0.975 * iterations) - 1])


def pass_hat_k(outcomes: list[list[bool]]) -> float:
    """Share of items that passed in every one of their k runs."""
    return sum(all(runs) for runs in outcomes) / len(outcomes) if outcomes else 0.0


def recall_at_k(ranks: list[int | None], k: int) -> float:
    return sum(rank is not None and rank <= k for rank in ranks) / len(ranks) if ranks else 0.0


def mrr(ranks: list[int | None]) -> float:
    return sum(1 / rank for rank in ranks if rank) / len(ranks) if ranks else 0.0


def ndcg_at_k(relevance: list[list[int]], k: int = 10) -> float:
    """Binary relevance per returned rank; the ideal ranking reorders the same list."""
    scores = []
    for row in relevance:
        top = row[:k]
        dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(top))
        ideal = sorted(top, reverse=True)
        idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal))
        scores.append(dcg / idcg if idcg else 0.0)
    return sum(scores) / len(scores) if scores else 0.0


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
