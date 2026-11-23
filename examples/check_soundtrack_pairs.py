"""Measure whether a reader changes its description when the sound on the same picture changes.

Input is a JSON list of pairs. Each pair has an id, two lists of human sound captions in
`sound`, two lists of visual captions in `picture`, and a `readers` mapping. Each reader's
value is a 2 x 2 table indexed by picture first, then soundtrack. Its cells contain
`caption`, `heard`, and `seen` strings. The diagonal cells use the original recordings.
A positive sound preference change means that switching the audio moved the text toward
the other recording's panel. Run with python examples/check_soundtrack_pairs.py reports.json.
The sentence encoder uses torch and transformers.
"""
import argparse
import json
from pathlib import Path

import numpy as np

if __package__:
    from .check_first_impressions import embeddings
else:
    from check_first_impressions import embeddings


COMPARISONS = (("caption", "sound"), ("heard", "sound"), ("seen", "picture"), ("caption", "picture"))


def compare_pairs(pairs, vectors):
    if not pairs or len({p["id"] for p in pairs}) != len(pairs):
        raise ValueError("use a distinct id for each pair")
    readers = set(pairs[0]["readers"])
    if not readers:
        raise ValueError("provide at least one reader")
    rows = []
    for pair in pairs:
        if set(pair["readers"]) != readers:
            raise ValueError("use the same readers throughout")
        panels = {}
        for sense in ("sound", "picture"):
            refs = pair[sense]
            if len(refs) != 2 or not all(refs):
                raise ValueError(f"provide two nonempty {sense} panels")
            panels[sense] = np.array([np.mean([vectors[s] for s in group], axis=0) for group in refs])
        for reader, table in pair["readers"].items():
            if len(table) != 2 or any(len(row) != 2 for row in table):
                raise ValueError("each reader needs both soundtracks on both pictures")
            for i, row in enumerate(table):
                for field, sense in COMPARISONS:
                    direction = panels[sense][i] - panels[sense][1 - i]
                    own = float(direction @ vectors[row[i][field]])
                    exchanged = float(direction @ vectors[row[1 - i][field]])
                    # Both values use the same pair of references and the same picture.
                    rows.append({"pair": pair["id"], "picture_index": i, "reader": reader, "field": field,
                                 "sense": sense, "original_preference": own, "exchanged_preference": exchanged,
                                 "change_toward_soundtrack": own - exchanged if sense == "sound" else None})
    def credit(x):
        return float(np.mean((np.sign(x) + 1) / 2))

    summary = {}
    for name in sorted(readers):
        scores = {}
        for field, sense in COMPARISONS:
            selected = [r for r in rows if (r["reader"], r["field"], r["sense"]) == (name, field, sense)]
            original = np.array([r["original_preference"] for r in selected])
            exchanged = np.array([r["exchanged_preference"] for r in selected])
            measures = {"original_retrieval": credit(original),
                        "exchanged_retrieval": credit(-exchanged if sense == "sound" else exchanged)}
            if sense == "sound":
                change = original - exchanged
                measures.update(mean_preference_change=float(change.mean()),
                                pictures_toward=int((change > 0).sum()), pictures_tied=int((change == 0).sum()))
            scores[f"{field}_vs_{sense}"] = measures
        summary[name] = scores
    return {"pairs": len(pairs), "pictures": 2 * len(pairs), "summary": summary, "by_picture": rows}


def score(pairs):
    texts = {s for p in pairs for sense in ("sound", "picture") for refs in p[sense] for s in refs}
    texts.update(cell[k] for p in pairs for table in p["readers"].values() for row in table for cell in row
                 for k in ("caption", "heard", "seen"))
    return compare_pairs(pairs, embeddings(sorted(texts)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pairs", type=Path)
    args = parser.parse_args()
    print(json.dumps(score(json.loads(args.pairs.read_text())), indent=2))
