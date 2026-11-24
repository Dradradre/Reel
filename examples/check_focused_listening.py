"""Rescore Reel's blind audio-only readings. No model is needed for the saved measurements.

With --captions, recompute the caption similarities from the official Clotho evaluation CSV.
With --fsd-audio, recreate the confirmation mixtures from their original FSD50K evaluation WAVs
and compare both the detected times and the source reader's names with the published observations.
"""
import argparse
import math
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np

if __package__:
    from .check_first_impressions import compare, embeddings, paired_change, relative_separation
else:
    from check_first_impressions import compare, embeddings, paired_change, relative_separation


BENCH = Path(__file__).resolve().parents[1] / "docs/benchmarks/focused-listening.json"


def change(before, after):
    films = [{"id": i} for i in range(len(before))]
    result = paired_change(films, np.asarray(before), np.asarray(after))
    return {k: v for k, v in result.items() if k != "by_film"}


def iou(a, b):
    if a is None:
        return 0.
    overlap = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    return overlap / (a[1] - a[0] + b[1] - b[0] - overlap)


def score(data):
    clips = data["clotho"]
    caption = {r["id"]: r["caption_scores"] for r in clips}
    descriptions = {arm: relative_separation(caption, arm) for arm in ("before", "after")}
    gaps = {arm: [r[arm][0] - r[arm][1] for r in caption.values()] for arm in descriptions}
    descriptions["paired_gap_change"] = change(gaps["before"], gaps["after"])

    questions = [q for r in clips for q in r["questions"]]
    qa, rates = {}, {}
    for arm in ("before", "after", "other_recording"):
        rates[arm] = [np.mean([q["votes"].count(q[arm]) / 3 for q in r["questions"]]) for r in clips]
        majority = sum(q["votes"].count(q[arm]) >= 2 for q in questions)
        unknown = sum(q[arm] == "unknown" for q in questions)
        qa[arm] = {"individual_votes_matched": sum(q["votes"].count(q[arm]) for q in questions),
                   "majority_matched": majority, "majority_wrong": len(questions) - majority - unknown,
                   "unknown": unknown}
    qa["constant_answer"] = {answer: {"majority_matched": sum(q["votes"].count(answer) >= 2 for q in questions),
                                      "individual_votes_matched": sum(q["votes"].count(answer) for q in questions)}
                             for answer in ("yes", "no")}
    split_votes = sum(len(set(q["votes"])) > 1 for q in questions)
    qa.update(questions=len(questions), votes=3 * len(questions), split_votes=split_votes,
              human_pairs_agree=3 * len(questions) - 2 * split_votes,
              paired_vote_change=change(rates["before"], rates["after"]),
              correct_vs_swapped_change=change(rates["other_recording"], rates["after"]))

    sources = {}
    for arm in ("before", "after"):
        sources[arm] = {material: dict(Counter(r[arm]["grade"] for r in data["gpt_sources"]
                                               if r["material"] == material)) for material in ("mix", "bed")}
    mixed = [r for r in data["gpt_sources"] if r["material"] == "mix"]
    sources["paired_exact_change"] = change(*[[r[arm]["grade"] == "exact" for r in mixed]
                                             for arm in ("before", "after")])
    trials = data["confirmation"]
    recognition = {}
    for material in ("mix", "bed"):
        recognition[material] = {}
        for arm, fields in (("before", ["wide"]), ("after", ["wide", "focus"])):
            labels = [set(label for field in fields for label in row[material][field]) for row in trials]
            targets = [{r["target"]} if material == "mix" else set() for r in trials]
            recognition[material][arm] = {
                "hit_clips": sum(bool(out & ref) for out, ref in zip(labels, targets, strict=True)),
                "unmatched_names": sum(len(out - ref) for out, ref in zip(labels, targets, strict=True)),
                "unnamed_clips": sum(not out for out in labels)}
    whole, focused, random_location = [], [], []
    for r in trials:
        event = [r["start"], r["end"]]
        whole.append(iou([0, 96000], event))
        selected = r["mix"]["bounds"]
        focused.append(iou(selected, event))
        if selected:
            width = selected[1] - selected[0]
            shifts = range(0, 96000 - width + 1, 480)
            random_location.append(np.mean([iou([start, start + width], event) for start in shifts]))
        else:
            random_location.append(0.)
    recognition["localization"] = {"mean_4s_iou": float(np.mean(whole)), "mean_focus_iou": float(np.mean(focused)),
                                   "paired_change": change(whole, focused),
                                   "mean_duration_matched_random_iou": float(np.mean(random_location)),
                                   "found_excerpt": sum(r["mix"]["bounds"] is not None for r in trials),
                                   "bed_excerpt": sum(r["bed"]["bounds"] is not None for r in trials)}
    field = {arm: {"hit_windows": sum(r[arm]["hit"] for r in data["field"]),
                   "unmatched_names": sum(r[arm]["unmatched"] for r in data["field"])}
             for arm in ("before", "after")}
    return dict(descriptions=descriptions, questions=qa, gpt_sources=sources, confirmation=recognition,
                annotated_field=field)


def check_captions(rows, path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        refs = {r["file_name"]: [r[f"caption_{i}"] for i in range(1, 6)] for r in csv.DictReader(f)}
    films = [{"id": r["id"], "human": refs[r["file"]], "readers": r["descriptions"]} for r in rows]
    for r, film in zip(rows, films, strict=True):
        digest = hashlib.sha256(json.dumps(film["human"], ensure_ascii=False).encode()).hexdigest()
        if digest != r["reference_sha256"]:
            raise ValueError(f"human reference mismatch for {r['id']}")
    sentences = sorted({s for f in films for s in [*f["human"], *f["readers"].values()]})
    computed = compare(films, embeddings(sentences))["by_film"]
    for row in rows:
        for arm, values in row["caption_scores"].items():
            np.testing.assert_allclose(computed[row["id"]][arm], values, atol=1e-5)


def mixture(source, source_rate, identity):
    from scipy import signal
    from reel.common import resample_channels
    rate = 24000
    recording = resample_channels(source, source_rate, rate)
    seed = hashlib.sha256(("scene/" + identity).encode()).digest()[:8]
    rng = np.random.default_rng(int.from_bytes(seed, "big"))
    width = min(len(recording), int(rate * rng.uniform(.5, 1.6)))
    starts = range(0, max(1, len(recording) - width + 1), max(1, width // 3))
    offset = max(starts, key=lambda i: np.sum(recording[i:i + width] ** 2))
    event = recording[offset:offset + width].copy()
    if width > 1:
        event *= signal.windows.tukey(width, .04)
    start = int(rng.integers(int(rate * .25), 4 * rate - width - int(rate * .2)))
    time = np.arange(4 * rate) / rate
    noise = rng.normal(size=len(time))
    bed = signal.lfilter([1], [1, -rng.uniform(.65, .98)], noise)
    bed /= np.sqrt(np.mean(bed ** 2))
    phase = 2 * np.pi * rng.uniform(45, 180) * time
    bed = .022 * (bed + np.sin(phase) + .4 * np.sin(2 * phase))
    snr = float(rng.choice([-3., 3., 9., 15.]))
    bed_level = np.sqrt(np.mean(bed[start:start + width] ** 2))
    event *= bed_level * 10 ** (snr / 20) / max(np.sqrt(np.mean(event ** 2)), 1e-10)
    mix = bed.copy()
    mix[start:start + width] += event
    scale = max(1., np.max(np.abs(mix)) / .95)
    return mix / scale, bed / scale, [offset, start, start + width, snr]


def check_recordings(rows, directory):
    import soundfile as sf
    from reel.sound_focus import contrast_interval
    from reel.source_examples import SourceReader
    with tempfile.TemporaryDirectory() as tmp, SourceReader() as reader:
        wav = Path(tmp) / "clip.wav"
        for r in rows:
            path = directory / (r["id"] + ".wav")
            if hashlib.sha256(path.read_bytes()).hexdigest() != r["sha256"]:
                raise ValueError(f"original recording mismatch for {r['id']}")
            x, rate = sf.read(path, dtype="float32", always_2d=True)
            mix, bed, params = mixture(x[:, 0], rate, r["id"])
            assert params == [r[k] for k in ("offset", "start", "end", "snr_db")]
            for material, samples in (("mix", mix), ("bed", bed)):
                samples = samples.astype(np.float32)
                bounds = contrast_interval(samples, 24000)
                sf.write(wav, samples, 24000, subtype="FLOAT")
                response = reader.read(wav, 0, 4, isolated=True)
                if response["status"] != "available":
                    raise RuntimeError(response)
                actual = {"bounds": list(bounds) if bounds else None}
                for key, field in (("wide", "windows"), ("focus", "focused_windows")):
                    actual[key] = [c["name"] for w in response.get(field, []) for c in w["channels"]]
                if actual != r[material]:
                    raise ValueError(f"{r['id']} {material}: {actual} differs from the published reading")


def same_totals(actual, saved):
    if isinstance(saved, dict):
        return actual.keys() == saved.keys() and all(same_totals(actual[k], v) for k, v in saved.items())
    if isinstance(saved, list):
        return len(actual) == len(saved) and all(same_totals(a, b) for a, b in zip(actual, saved, strict=True))
    if isinstance(saved, float):
        return math.isclose(actual, saved, abs_tol=1e-9)
    return actual == saved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=BENCH)
    parser.add_argument("--captions", type=Path, help="official clotho_captions_evaluation.csv")
    parser.add_argument("--fsd-audio", type=Path, help="original evaluation WAV directory, requires source weights")
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    if args.captions:
        check_captions(report["clotho"], args.captions)
    if args.fsd_audio:
        check_recordings(report["confirmation"], args.fsd_audio)
    summary = score(report)
    if not same_totals(summary, report["summary"]):
        raise ValueError("published totals differ from the individual readings")
    print(json.dumps(summary, indent=2))
