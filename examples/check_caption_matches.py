"""Audit the audio-only listening trial; optionally rescore it from the five original human captions."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from check_first_impressions import compare, embeddings, relative_separation

BENCHMARK = Path(__file__).resolve().parents[1] / "docs/benchmarks/caption-matches.json"


def panel_result(trials, reader="reference"):
    by_film = {r["source_id"]: r["caption_scores"] for r in trials}
    names = trials[0]["descriptions"]
    scores = {name: relative_separation(by_film, name) for name in names}
    # Paired uncertainty on the change in the ratio, using the same held-out people and recordings.
    baseline = np.array([r["human"][0] - r["human"][1] for r in by_film.values()])
    delta = np.array([(r[reader][0] - r[reader][1]) - (r["before"][0] - r["before"][1])
                      for r in by_film.values()])
    rng = np.random.default_rng(20261125)
    samples = rng.integers(0, len(delta), size=(10000, len(delta)))
    denominator = baseline[samples].mean(axis=1)
    interval = (np.percentile(delta[samples].mean(axis=1) / denominator, [2.5, 97.5]).tolist()
                if np.all(denominator > 0) else None)
    return {"recordings": len(trials), "readers": scores, "compared_to_before": reader,
            "paired_fraction_change": float(delta.mean() / baseline.mean()) if baseline.mean() > 0 else None,
            "paired_bootstrap_95": interval, "improved_recordings": int((delta > 0).sum()),
            "worse_recordings": int((delta < 0).sum())}


def rescore(rows, captions):
    films = []
    for row in rows:
        refs = captions[row["file"]]
        encoded = json.dumps(refs, ensure_ascii=False).encode()
        if hashlib.sha256(encoded).hexdigest() != row["captions_sha256"]:
            raise ValueError("human captions do not match the trial")
        films.append({"id": row["source_id"], "human": refs, "readers": row["descriptions"]})
    texts = sorted({s for f in films for s in (*f["human"], *f["readers"].values())})
    scores = compare(films, embeddings(texts), human_reference=True)["by_film"]
    for row in rows:
        for name, actual in scores[row["source_id"]].items():
            np.testing.assert_allclose(actual, row["caption_scores"][name], atol=2e-6)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captions", type=Path, help="Clotho v2.1 evaluation caption CSV")
    args = parser.parse_args()
    data = json.loads(BENCHMARK.read_text())
    captions = None
    if args.captions:
        with args.captions.open(encoding="utf-8", newline="") as f:
            captions = {r["file_name"]: [r[f"caption_{n}"] for n in range(1, 6)] for r in csv.DictReader(f)}
    for name, rows in data["panels"].items():
        if captions is not None:
            rescore(rows, captions)
        print(name, json.dumps(panel_result(rows), indent=2))
        if "verified" in rows[0]["descriptions"]:
            print(name, json.dumps(panel_result(rows, "verified"), indent=2))
    questions = [q for r in data["panels"]["replication"] for q in r["questions"]]
    for arm in ("before", "reference", "swapped"):
        hits = sum(q["votes"].count(q[arm]) >= 2 for q in questions)
        print(f"Listening questions {arm}: agrees with majority on {hits}/{len(questions)}")
    for name, rows in data["verifier_control"].items():
        for arm in ("matched_counts", "swapped_counts"):
            counts = [k for row in rows for k in row[arm]]
            print(f"PE-AV {name} {arm}: retained {sum(k >= 125 for k in counts)}/{len(counts)}")
    for name in ("before", "reference"):
        cases = data["field"]
        hits = sum(any("E" in g for g in r[name]["grades"]) for r in cases)
        print(f"TUT {name}: at least one annotated event named in {hits}/{len(cases)} excerpts")


if __name__ == "__main__":
    main()
