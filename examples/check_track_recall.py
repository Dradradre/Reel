"""Does free recall follow the sound when only the soundtrack changes?

Pass a crossover report and a JSON dictionary of original AVCaps annotations keyed
by video ID. Every pair needs all four assignments of its two pictures and two
recordings. Each response contains separate "seen" and "heard" sentences.
"""
import argparse
import json
from pathlib import Path
from statistics import mean

import numpy as np

if __package__:
    from .check_first_impressions import embeddings, paired_change
else:
    from check_first_impressions import embeddings, paired_change


TRACKS = (("heard", "recording", "audio_captions"), ("seen", "picture", "visual_captions"))


def score(pairs, captions, vectors):
    by_pair = []
    if len(pairs) < 2:
        raise ValueError("supply at least two independent pairs")
    arms = set(pairs[0]["assignments"][0]["responses"])
    if not arms or "human" in arms:
        raise ValueError("give each reader condition a name distinct from human")
    used = set()
    for pair in pairs:
        ids = pair["ids"]
        if len(ids) != 2 or ids[0] == ids[1] or used.intersection(ids):
            raise ValueError("each source must belong to exactly one pair")
        used.update(ids)
        rows = pair["assignments"]
        expected = {(a, b) for a in ids for b in ids}
        if len(rows) != 4 or {(r["picture"], r["recording"]) for r in rows} != expected:
            raise ValueError("each pair needs all four picture and recording assignments")
        if any(set(r["responses"]) != arms for r in rows):
            raise ValueError("use the same readers in every condition")
        measures, assignments = {}, {}
        for field, source, human_column in TRACKS:
            references = [np.stack([vectors[text] for text in captions[i][human_column]]) for i in ids]
            if any(len(r) < 2 for r in references):
                raise ValueError("each source needs at least two independent human descriptions per track")
            centroids = [r.mean(axis=0) for r in references]
            # Holding one person out gives the same reference pool to that person and each reader.
            # Across folds each remaining caption contributes equally to the reader's mean.
            people = []
            for i, refs in enumerate(references):
                gaps = [float(v @ (np.delete(refs, j, axis=0).mean(axis=0) - centroids[1 - i]))
                        for j, v in enumerate(refs)]
                people.append(mean(gaps))
            gaps = {"human": mean(people)}
            assignments[field] = {}
            other_track = "picture" if source == "recording" else "recording"
            for arm in sorted(arms):
                by_fixed_source = {video: [] for video in ids}
                for row in rows:
                    i = ids.index(row[source])
                    prediction = vectors[row["responses"][arm][field]]
                    by_fixed_source[row[other_track]].append(float(prediction @ (centroids[i] - centroids[1 - i])))
                margins = [mean(v) for v in by_fixed_source.values()]
                gaps[arm] = mean(margins)
                assignments[field][arm] = {
                    "correct": sum(m > 0 for m in margins), "tied": sum(m == 0 for m in margins),
                    "reversed": sum(m < 0 for m in margins)}
            measures[field] = gaps
        by_pair.append({"id": "/".join(ids), **measures, "assignment_counts": assignments})
    summary = {}
    for field, _, _ in TRACKS:
        summary[field] = {}
        human = mean(r[field]["human"] for r in by_pair)
        for arm in sorted(arms | {"human"}):
            values = [r[field][arm] for r in by_pair]
            boot = paired_change(by_pair, np.zeros(len(by_pair)), values)
            summary[field][arm] = {"same_minus_paired_source": mean(values),
                                   "pair_bootstrap_95": boot["bootstrap_95_percent"],
                                   "percent_of_held_out_human_gap": 100 * mean(values) / human if human > 0 else None}
            if arm != "human":
                summary[field][arm]["assignment_counts"] = {
                    k: sum(r["assignment_counts"][field][arm][k] for r in by_pair)
                    for k in ("correct", "tied", "reversed")}
    return {"summary": summary, "by_pair": by_pair}


def sentences(pairs, captions):
    texts = set()
    for pair in pairs:
        for _, _, column in TRACKS:
            for video in pair["ids"]:
                texts.update(captions[video][column])
        for row in pair["assignments"]:
            for response in row["responses"].values():
                texts.update(response[field] for field, _, _ in TRACKS)
    return sorted(texts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("captions", type=Path)
    args = parser.parse_args()
    pairs = json.loads(args.report.read_text())["pairs"]
    captions = json.loads(args.captions.read_text())
    print(json.dumps(score(pairs, captions, embeddings(sentences(pairs, captions))), indent=2))
