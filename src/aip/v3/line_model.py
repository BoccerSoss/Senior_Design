"""Small supervised stroke-continuity model, trained only on synthetic sequences.

Both training and inference use the ordered ink/no-ink samples along a route.
The prototypes are class means learned from a fixed seeded generator. No
drawing, annotated benchmark, answer file, or externally fitted weight is read.
"""
from __future__ import annotations

from functools import lru_cache
from itertools import groupby
from random import Random


MODEL_ID = "synthetic-stroke-centroids-v1"
TRAINING_SEED = 340197
EXAMPLES_PER_CLASS = 160
CLASSES = ("solid", "scan_dropout", "interrupted", "dashed", "clutter", "blank")
CONTINUOUS = {"solid", "scan_dropout"}


def features(samples):
    n = len(samples)
    gaps = [len(list(group)) for ink, group in groupby(samples) if not ink]
    end = max(1, n // 10)
    return (sum(samples) / n, max(gaps, default=0) / n,
            min(sum(samples[:end]), sum(samples[-end:])) / end,
            sum(min(1, gap / 3) for gap in gaps) / n)


def _example(rng, category):
    n = rng.randint(30, 180)
    if category == "solid":
        return [True] * n
    if category == "scan_dropout":
        return [rng.random() >= 0.018 for _ in range(n)]
    if category == "interrupted":
        samples = [True] * n
        gap = rng.randint(max(3, n // 20), max(4, n // 3))
        start = rng.randrange(n - gap + 1)
        samples[start:start + gap] = [False] * gap
        return samples
    if category == "dashed":
        solid, blank = rng.randint(3, 12), rng.randint(2, 6)
        offset = rng.randrange(solid + blank)
        return [(i + offset) % (solid + blank) < solid for i in range(n)]
    if category == "clutter":
        density = rng.uniform(0.15, 0.65)
        return [rng.random() < density for _ in range(n)]
    return [False] * n


@lru_cache(maxsize=1)
def fit_prototypes():
    rng = Random(TRAINING_SEED)
    result = {}
    for label in CLASSES:
        rows = [features(_example(rng, label)) for _ in range(EXAMPLES_PER_CLASS)]
        result[label] = tuple(sum(column) / len(rows) for column in zip(*rows))
    return result


def classify_stroke(samples):
    observed = features(samples)
    distances = {label: sum((left - right) ** 2 for left, right in zip(observed, prototype))
                 for label, prototype in fit_prototypes().items()}
    label = min(distances, key=distances.get)
    return label, distances[label], observed[0]
