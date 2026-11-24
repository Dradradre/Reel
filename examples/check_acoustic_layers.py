"""Rescore the released audio-only trials. Optional inputs rerun the acoustic or caption measurements.

    python examples/check_acoustic_layers.py
    python examples/check_acoustic_layers.py --captions-csv clotho_captions_evaluation.csv
    python examples/check_acoustic_layers.py --fsd-audio FSD50K.eval_audio

The last command uses the installed Reel MixIT worker. The FSD50K directory contains <id>.wav.
"""
import argparse
import csv
import hashlib
import itertools
import json
from pathlib import Path
import sys
import tempfile

import numpy as np

from check_first_impressions import compare, embeddings, paired_change, relative_separation

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/benchmarks/acoustic-layers.json"


def maximum_hits(matrix):
    """An utterance and a reference sound can each receive credit only once."""
    labels = len(matrix)
    claims = len(matrix[0]) if labels else 0
    count = min(labels, claims)
    return max(sum(matrix[i][j] == "E" for i, j in zip(indices, order))
               for indices in itertools.combinations(range(labels), count)
               for order in itertools.permutations(range(claims), count))


def naming(trials):
    result = {}
    for arm in trials[0]["readings"]:
        counts = dict(clips=len(trials), reference_events=0, claims=0, matched_events=0,
                      clips_with_match=0, all_events_named=0, two_claims=0)
        for trial in trials:
            targets = trial["labels"]
            reading = trial["readings"][arm]
            claims = reading["names"]
            matrix = reading["grades_by_reference"]
            assert len(matrix) == len(targets) and all(len(row) == len(claims) for row in matrix)
            hits = maximum_hits(matrix)
            counts["reference_events"] += len(targets)
            counts["claims"] += len(claims)
            counts["matched_events"] += hits
            counts["all_events_named"] += hits == len(targets)
            counts["clips_with_match"] += hits > 0
            counts["two_claims"] += len(claims) == 2
        counts["unmatched_claims"] = counts["claims"] - counts["matched_events"]
        result[arm] = counts
    return result


def panel_scores(rows):
    by_film = {r["source_id"]: r["caption_scores"] for r in rows}
    result = {}
    for arm in rows[0]["descriptions"]:
        qa = [(q["votes"], q["answers"][arm]) for r in rows for q in r["questions"]]
        result[arm] = dict(caption_separation=relative_separation(by_film, arm),
                           questions=len(qa), majority=sum(v.count(a) >= 2 for v, a in qa),
                           individual_agreements=sum(v.count(a) for v, a in qa),
                           individual_votes=sum(len(v) for v, _ in qa),
                           unknown=sum(a == "unknown" for _, a in qa))
    return result


def acoustic_scores(rows):
    gain = []
    for row in rows:
        scores = np.asarray(row["part_sisdr"])
        # Assignment by total SI-SDR, independently of the reader's names.
        selected = scores if scores.trace() >= scores[:, ::-1].trace() else scores[:, ::-1]
        gain.append(selected.diagonal() - row["base"])
    gain = np.asarray(gain)
    quieter = [row["relative_db"][1] < row["relative_db"][0] for row in rows]
    return dict(mixtures=len(rows), sources=gain.size, improved_sources=int((gain > 0).sum()),
                improved_both=int((gain.min(axis=1) > 0).sum()), median_gain_db=float(np.median(gain)),
                quieter_sources=sum(quieter), quieter_median_gain_db=float(np.median(gain[quieter, 1])))


def question_conditions(rows):
    names = rows[0]["questions"][0]["conditioned"]
    totals = {}
    by_clip = {}
    for name in names:
        scores = []
        votes_agree, unknown = 0, 0
        for row in rows:
            correct = 0
            for q in row["questions"]:
                answer = q["conditioned"][name]
                votes_agree += q["votes"].count(answer)
                unknown += answer == "unknown"
                correct += q["votes"].count(answer) >= 2
            scores.append(correct)
        by_clip[name] = np.array(scores)
        n = sum(len(r["questions"]) for r in rows)
        totals[name] = dict(questions=n, majority=sum(scores), wrong=n-sum(scores)-unknown,
                            unknown=unknown, individual_agreements=votes_agree)
    films = [{"id": r["source_id"]} for r in rows]
    comparisons = {}
    for name in ("prior", "open", "different_recording"):
        comparison = paired_change(films, by_clip[name] / 4, by_clip["asked"] / 4)
        comparisons[name + "_to_asked"] = {k: comparison[k] for k in ("mean_change", "bootstrap_95_percent")}
    return dict(totals=totals, paired_accuracy_change=comparisons)


def summarize(data):
    panels = data["human_panels"]
    return dict(acoustic=acoustic_scores(data["mixtures"]),
                naming={key: naming(rows) for key, rows in data["naming"].items()},
                human={key: panel_scores(rows) for key, rows in panels.items()},
                specific_questions=question_conditions(panels["replication"]))


def check_captions(panels, source):
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reference = {r["file_name"]: [r[f"caption_{i}"] for i in range(1, 6)] for r in csv.DictReader(stream)}
    for rows in panels.values():
        films = []
        for r in rows:
            captions = reference[r["file"]]
            digest = hashlib.sha256(json.dumps(captions, ensure_ascii=False).encode()).hexdigest()
            assert digest == r["reference_sha256"], r["file"]
            films.append(dict(id=r["source_id"], human=captions, readers=r["descriptions"]))
        vectors = embeddings(sorted({s for f in films for s in (*f["human"], *f["readers"].values())}))
        actual = compare(films, vectors)["by_film"]
        for r in rows:
            for arm, scores in r["caption_scores"].items():
                np.testing.assert_allclose(actual[r["source_id"]][arm], scores, atol=2e-5)


def si_sdr(estimate, target):
    estimate = estimate.astype(np.float64) - estimate.mean()
    target = target.astype(np.float64) - target.mean()
    projection = target * (estimate @ target) / max(target @ target, 1e-12)
    error = estimate - projection
    return float(10 * np.log10(max(projection @ projection, 1e-15) / max(error @ error, 1e-15)))


def check_waveforms(rows, folder):
    import soundfile as sf
    sys.path.insert(0, str(ROOT))
    from reel.common import resample_channels
    from reel.unmix import LayerReader

    with LayerReader() as separator:
        for r in rows:
            targets = []
            for uid, start, db in zip(r["ids"], r["start_samples"], r["relative_db"], strict=True):
                pcm, rate = sf.read(folder / (uid + ".wav"), dtype="float32", always_2d=True)
                part = resample_channels(pcm[:, 0], rate, 16000)[start:start + 64000]
                normalized = part / np.sqrt(np.mean(part ** 2)) * (.06 * 10 ** (db / 20))
                targets.append(normalized)
            mix = (targets[0] + targets[1]) / r["scale"]
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "input.wav"
                sf.write(wav, mix, 16000, subtype="FLOAT")
                row = separator.request(dict(wav=str(wav), channel=1, layer=1))
                if row["status"] != "available":
                    raise ValueError(row.get("reason", row["status"]))
                layer, _ = sf.read(row["layer_wav"], dtype="float32")
            np.testing.assert_allclose([si_sdr(mix, t) for t in targets], r["base"], atol=.05)
            measured = [[si_sdr(s, t) for s in (layer, mix - layer)] for t in targets]
            np.testing.assert_allclose(measured, r["part_sisdr"], atol=.15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captions-csv", type=Path)
    parser.add_argument("--fsd-audio", type=Path)
    args = parser.parse_args()
    data = json.loads(DATA.read_text())
    result = summarize(data)
    assert result == data["summary"]
    if args.captions_csv:
        check_captions(data["human_panels"], args.captions_csv)
    if args.fsd_audio:
        check_waveforms(data["mixtures"], args.fsd_audio)
    print(json.dumps(result, indent=2))
