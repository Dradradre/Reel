"""Compare a reader's event times with each viewer of the same film.

Read a JSON file with a "films" list. Each film has an id, a scoring interval
[start, end), "human" (one list of seconds per observer), and "readers" (a map
from reader names to lists of seconds). Marks outside the interval are ignored.
"""
import argparse
from itertools import combinations
import json
import math
from pathlib import Path
from statistics import mean

import numpy as np


def matches(left, right, tolerance):
    """Sorted one-to-one matching. The earliest feasible pair leaves the most room for later pairs."""
    i = j = matched = 0
    while i < len(left) and j < len(right):
        if right[j] - left[i] > tolerance:
            i += 1
        elif left[i] - right[j] > tolerance:
            j += 1
        else:
            matched += 1
            i += 1
            j += 1
    return matched


def agreement(left, right, tolerance):
    count = len(left) + len(right)
    return 2 * matches(left, right, tolerance) / count if count else 1.


def score(films, tolerance=.5):
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("use a finite nonnegative tolerance")
    if not films or len({f["id"] for f in films}) != len(films):
        raise ValueError("supply films with distinct IDs")
    names = list(films[0]["readers"])
    if not names:
        raise ValueError("supply at least one reader")
    results = []
    for f in films:
        start, end = f["interval"]
        if not (math.isfinite(start) and math.isfinite(end) and start < end):
            raise ValueError("choose a finite scoring interval")
        if len(f["human"]) < 2 or set(names) != set(f["readers"]):
            raise ValueError("each film needs multiple viewers and the same reader names")

        def within(marks):
            if not all(math.isfinite(t) for t in marks):
                raise ValueError("supply finite timestamps")
            return sorted(t for t in marks if start <= t < end)

        people = [within(group) for group in f["human"]]
        human = mean(agreement(a, b, tolerance) for a, b in combinations(people, 2))
        readers = {}
        for name in names:
            marks = within(f["readers"][name])
            actual = mean(agreement(marks, person, tolerance) for person in people)
            # Preserve each reader's mark count and spacing. Average over an entire
            # rotation of this film clock, sampling the midpoint of 128 equal steps.
            rotations = []
            for i in range(128):
                offset = (i + .5) * (end - start) / 128
                moved = sorted(start + ((t - start + offset) % (end - start)) for t in marks)
                rotations.append(mean(agreement(moved, person, tolerance) for person in people))
            paired = [matches(marks, person, tolerance) for person in people]
            readers[name] = {"f1": actual, "rotated_f1": mean(rotations), "marks": len(marks),
                             "matched_per_viewer": mean(paired),
                             "marks_near_majority": sum(
                                 sum(any(abs(t - h) <= tolerance for h in person) for person in people) > len(people) / 2
                                 for t in marks)}
        results.append({"id": f["id"], "human_f1": human, "human_marks": mean(map(len, people)), "readers": readers})

    def interval(values):
        samples = np.asarray(values)
        rng = np.random.default_rng(11471)
        means = np.empty(10000)
        batch = max(1, 262144 // len(samples))
        for i in range(0, len(means), batch):
            block = means[i:i + batch]
            block[:] = rng.choice(samples, size=(len(block), len(samples))).mean(axis=1)
        return {"mean": float(samples.mean()), "film_bootstrap_95": np.percentile(means, [2.5, 97.5]).tolist()}

    summary = {"human_f1": interval([r["human_f1"] for r in results]), "readers": {}, "paired": []}
    for name in names:
        rows = [r["readers"][name] for r in results]
        summary["readers"][name] = {"f1": interval([r["f1"] for r in rows]),
                                    "marks": sum(r["marks"] for r in rows),
                                    "marks_near_majority": sum(r["marks_near_majority"] for r in rows),
                                    "matched_per_film_per_viewer": mean(r["matched_per_viewer"] for r in rows),
                                    "rotated_f1": interval([r["rotated_f1"] for r in rows]),
                                    "above_rotation": interval([r["f1"] - r["rotated_f1"] for r in rows]),
                                    "minus_human": interval([s["f1"] - r["human_f1"] for s, r in zip(rows, results)])}
    for a, b in combinations(names, 2):
        summary["paired"].append({"before": a, "after": b,
                                  "change": interval([r["readers"][b]["f1"] - r["readers"][a]["f1"] for r in results])})
    return {"tolerance": tolerance, "summary": summary, "by_film": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path)
    parser.add_argument("--tolerance", type=float, default=.5)
    args = parser.parse_args()
    print(json.dumps(score(json.loads(args.reports.read_text())["films"], args.tolerance), indent=2))
