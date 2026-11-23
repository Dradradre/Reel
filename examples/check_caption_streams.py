"""Compare time-aligned open sound descriptions to independent human listeners.

Pass the published caption-styles report and the original AVCaps test_captions.json
or Clotho evaluation caption CSV. Each film contributes equally. Inside a film each
newly released sample contributes once, even when the last window used some earlier
audio for context. Identical channels can share a caption.
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np

if __package__:
    from .check_first_impressions import compare, embeddings, paired_change
else:
    from check_first_impressions import compare, embeddings, paired_change


def weighted_stream(film, style, vectors):
    total = np.zeros_like(next(iter(vectors.values())), dtype=np.float64)
    first = 0
    for window in film["windows"]:
        start, released, end = (window[k] for k in ("start_sample", "first_released_sample", "end_sample"))
        if not (0 <= start <= released == first < end <= film["frames"]):
            raise ValueError("each window must cover the next samples, with any context in the past")
        channels = set()
        for reading in window["channels"]:
            group = reading["channels"]
            if (not group or len(set(group)) != len(group) or channels.intersection(group)
                    or min(group) < 1 or max(group) > film["channels"]):
                raise ValueError("each recorded channel can contribute only once per window")
            channels.update(group)
            total += (end - released) * len(group) * vectors[reading[style]]
        first = end
    if first != film["frames"] or first <= 0 or film["channels"] <= 0:
        raise ValueError("expected the complete released recording")
    # Silent channels add no source evidence. They still count toward elapsed listening time.
    return total / (first * film["channels"])


def score(films, references, styles, vectors):
    if len(styles) != 2 or len(set(styles)) != 2:
        raise ValueError("choose two different caption conditions")
    features = dict(vectors)
    records = []
    for film in films:
        readers = {}
        for style in styles:
            key = (film["id"], style)
            features[key] = weighted_stream(film, style, vectors)
            readers[style] = key
        records.append({"id": film["id"], "human": references[film["id"]], "readers": readers})
    result = compare(records, features, human_reference=True)
    rows = result["by_film"]
    gaps = [[rows[r["id"]][style][0] - rows[r["id"]][style][1] for r in records] for style in styles]
    result["paired_change_in_separation"] = paired_change(records, *gaps)
    return result


def read_references(path):
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return {r["file_name"]: [r[f"caption_{i}"] for i in range(1, 6)] for r in csv.DictReader(handle)}
    return {k: v["audio_captions"] for k, v in json.loads(path.read_text()).items()}


def score_panel(films, references, styles):
    texts = {text for film in films for text in references[film["id"]]}
    texts.update(ch[style] for film in films for w in film["windows"] for ch in w["channels"] for style in styles)
    return score(films, references, styles, embeddings(sorted(texts)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("references", type=Path)
    parser.add_argument("--panel", choices=("avcaps", "field"), default="avcaps")
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    print(json.dumps(score_panel(report["panels"][args.panel]["films"], read_references(args.references),
                                 report["styles"]), indent=2))
