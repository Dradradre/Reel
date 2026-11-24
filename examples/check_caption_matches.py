"""Audit the audio-only listening trial; optionally rescore it from the five original human captions."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import unicodedata

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


def answer_text(s):
    return " ".join(re.findall(r"\w+(?:'\w+)?", unicodedata.normalize("NFKC", s).casefold()))


def listening_answers(cases):
    result = {}
    for form in ("yes/no", "open"):
        rows = [q for case in cases for q in case["items"] if q["form"] == form]
        votes = [[answer_text(v) for v in q["votes"]] for q in rows]
        pairs = sum(a == b for group in votes for i, a in enumerate(group) for b in group[i + 1:])
        counts = {"questions": len(rows), "human_pair_agreements": pairs, "human_pairs": 3 * len(rows),
                  "questions_with_a_majority": sum(len(set(group)) < 3 for group in votes)}
        for arm in rows[0]["answers"]:
            answers = [answer_text(q["answers"][arm]) for q in rows]
            hits = [group.count(a) for group, a in zip(votes, answers, strict=True)]
            counts[arm] = {"individual_agreements": sum(hits), "majority_agreements": sum(h >= 2 for h in hits),
                           "unknown": answers.count("unknown")}
            if form == "yes/no":
                table = {vote: {reply: 0 for reply in ("yes", "no", "unknown")} for vote in ("yes", "no")}
                for group, answer in zip(votes, answers, strict=True):
                    if len(group) != 3 or not set(group) <= {"yes", "no"}:
                        raise ValueError("each binary question needs three human yes/no votes")
                    table["yes" if group.count("yes") >= 2 else "no"][answer] += 1
                counts[arm].update(wrong=table["yes"]["no"] + table["no"]["yes"], by_human_majority=table)
        result[form] = counts
    return result


def binary_change(cases, arm, comparison):
    """Resample whole recordings to keep the four questions and their three votes together."""
    differences = []
    for case in cases:
        votes = [q for q in case["items"] if q["form"] == "yes/no"]
        correct = {key: [q["votes"].count(answer_text(q["answers"][key])) >= 2 for q in votes]
                   for key in (arm, comparison)}
        differences.append(np.mean(correct[arm]) - np.mean(correct[comparison]))
    values = np.array(differences)
    rng = np.random.default_rng(20261125)
    draws = rng.choice(values, size=(10000, len(values))).mean(axis=1)
    return {"arm": arm, "comparison": comparison, "fraction_change": float(values.mean()),
            "recording_bootstrap_95": np.percentile(draws, [2.5, 97.5]).tolist()}


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
    print("Unseen uploaders listening questions", json.dumps(listening_answers(data["listening_questions"]), indent=2))
    print("Clotho-AQA official test recordings", json.dumps(listening_answers(data["official_qa_test"]), indent=2))
    for arm, comparison in (("verified", "before"), ("waveform_question", "verified"),
                            ("question_control", "short_question"), ("experimental_direct", "experimental_silent")):
        print("Paired binary change", json.dumps(binary_change(data["official_qa_test"], arm, comparison)))
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
