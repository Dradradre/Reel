"""Re-score the source trial, optionally recomputing it from original FSD50K eval WAVs."""
import argparse
import json
from pathlib import Path

import numpy as np

from reel.common import open_recording
from reel.identify_sound import PerceptionEncoder
from reel.scene_audio import digest
from reel.source_examples import SECONDS, SourceIndex, waveform_vector

RESULTS = Path(__file__).resolve().parents[1] / "docs/benchmarks/reference-sources.json"


def recognize(index, vector, label):
    text, refs = index.scores(vector)
    predicted = index.match(text, refs)
    if label in index.names:
        refs[index.names.index(label)] = -np.inf
    withheld = index.match(text, refs)
    return {"reference_match": predicted["name"] if predicted else None,
            "reference_withheld": withheld["name"] if withheld else None,
            "text_alone": index.names[text.argmax()]}


def summarize(rows):
    matched = [r for r in rows if r["reference_match"]]
    outside = [r for r in rows if not r["in_library"]]
    counts = dict(clips=len(rows), correct=sum(r["reference_match"] == r["label"] for r in matched),
                  named=len(matched), unknown=len(rows) - len(matched),
                  text_top1_correct=sum(r["text_alone"] == r["label"] for r in rows),
                  outside=len(outside), outside_named=sum(bool(r["reference_match"]) for r in outside),
                  true_audio_references_withheld_named=sum(bool(r["reference_withheld"]) for r in rows))
    counts["wrong"] = counts["named"] - counts["correct"]
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, help="directory of original eval WAVs, named by Freesound ID")
    parser.add_argument("--tag-audio-dir", type=Path, help="repeat the original AST/YAMNet tag comparison")
    args = parser.parse_args()
    report = json.loads(RESULTS.read_text())
    if args.audio_dir:
        index = SourceIndex()
        encoder = PerceptionEncoder()
        encoder.audio_only()
        for row in report["trials"]:
            wav = args.audio_dir / (row["id"] + ".wav")
            if digest(wav) != row["sha256"]:
                raise ValueError(f"recording {row['id']} has a different checksum")
            with open_recording(wav) as f:
                samples = f.read(SECONDS * f.samplerate, dtype="float32", always_2d=True)
                vector = waveform_vector(encoder, samples[:, 0], f.samplerate)
            for field, value in recognize(index, vector, row["label"]).items():
                if row[field] != value:
                    raise ValueError(f"recording {row['id']}: expected {row[field]}, got {value} for {field}")
    holdout = [r for r in report["trials"] if r["split"] == "holdout"]
    if args.tag_audio_dir:
        from reel.sound_events import Classifier, EventHistory
        from reel.sounds import AudioTagger, PlaybackSounds

        tagger, events = AudioTagger(local_only=True), Classifier()
        for row in holdout:
            wav = args.tag_audio_dir / (row["id"] + ".wav")
            if digest(wav) != row["sha256"]:
                raise ValueError(f"recording {row['id']} has a different checksum")
            with open_recording(wav) as f:
                end = min(SECONDS, len(f) / f.samplerate)
            brief = EventHistory(events).read(wav, 0, end)["candidates"]
            whole = PlaybackSounds(tagger).read(wav, 0, end)["windows"]
            names = {c["sound"] for c in brief}
            names.update(c["sound"] for w in whole for channel in w["channels"] for c in channel["candidates"])
            if sorted(names) != row["existing_tags"]:
                raise ValueError(f"recording {row['id']} produced different tags")
    existing = sum(r["label"] in r["existing_tags"] for r in holdout)
    new = [r for r in holdout if r["reference_match"] and r["reference_match"] not in r["existing_tags"]]
    correct = sum(r["reference_match"] == r["label"] for r in new)
    additions = dict(clips=len(holdout), existing_correct_available=existing,
                     combined_correct_available=existing + correct,
                     new_correct_names=correct, new_incorrect_names=len(new) - correct)
    if any(report["holdout_candidate_comparison"][k] != v for k, v in additions.items()):
        raise ValueError("candidate comparison totals differ")
    print("playback candidates", json.dumps(additions))
    for part in ("calibration", "holdout"):
        result = summarize([r for r in report["trials"] if r["split"] == part])
        if result != report["scores"][part]:
            raise ValueError(f"{part} totals differ")
        print(part, json.dumps(result))
