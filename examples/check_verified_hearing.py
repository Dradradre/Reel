"""Rescore the published sound descriptions with the authors' AVCaps test_captions.json.

Run python examples/check_verified_hearing.py docs/benchmarks/verified-hearing.json test_captions.json.
Each film has equal weight. For the local caption streams, average each distinct
channel reading over the released windows. For GPT, use its final free recall.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

if __package__:
    from .check_first_impressions import compare, embeddings, paired_change
else:
    from check_first_impressions import compare, embeddings, paired_change


def score(rows, references, field, arms):
    films, vectors = [], {}
    texts = {t for row in rows for t in references[row["id"]][field]}
    for row in rows:
        for arm in arms:
            texts.update(row["readings"][arm])
    vectors.update(embeddings(sorted(texts)))
    for row in rows:
        readers = {}
        for arm in arms:
            key = (row["id"], arm)
            vectors[key] = np.mean([vectors[t] for t in row["readings"][arm]], axis=0)
            readers[arm] = key
        films.append({"id": row["id"], "human": references[row["id"]][field], "readers": readers})
    metric = compare(films, vectors, human_reference=True)
    gaps = {arm: [v[arm][0] - v[arm][1] for v in metric["by_film"].values()] for arm in arms}
    return {"relative": {k: v["fraction_of_human"] for k, v in metric["human_relative_description_separation"].items()},
            "paired": {arm: paired_change(films, gaps["control"], gaps[arm]) for arm in arms if arm != "control"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("references", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    raw = args.references.read_bytes()
    if hashlib.sha256(raw).hexdigest() != report["captions_sha256"]:
        parser.error("the supplied human references differ from the published comparison")
    references = json.loads(raw)
    rows = []
    for film in report["upstream"]["films"]:
        readings = {arm: [ch[arm] for w in film["windows"] for ch in w["channels"]] for arm in ("control", "checked")}
        rows.append({"id": film["id"], "readings": readings})
    results = {"upstream": score(rows, references, "audio_captions", ["control", "checked"])}
    replies = report["reader"]["films"]
    for label, field in [("heard", "audio_captions"), ("seen", "visual_captions"), ("caption", "audio_visual_captions")]:
        rows = [{"id": f["id"], "readings": {k: [v[label]] for k, v in f["replies"].items()}} for f in replies]
        results[label] = score(rows, references, field, list(rows[0]["readings"]))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
