"""Check source choices on the published ESC-50 panel, optionally repeating both waveform reads.

python examples/check_source_identity.py docs/benchmarks/source-identity-agreement.json
python examples/check_source_identity.py docs/benchmarks/source-identity-agreement.json --audio ESC-50/audio
Prepare reel setup --identify-sounds for the second command. The isolated readers
receive waveforms and source descriptions. Filenames and labels stay in the scorer.
"""
import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

if __package__:
    from .check_first_impressions import paired_change
else:
    from check_first_impressions import paired_change

from reel.identify_sound import CLAP_NAME, MODEL, IdentityReader


def score(rows):
    target = [r["target"] for r in rows]
    pe, clap = [np.array([int(r[model][0]["description"] == r["target"]) for r in rows]) for model in (MODEL, CLAP_NAME)]
    agreed = [(r.get("shared_choice") or r.get("nearest_shared_alternative"), r["target"]) for r in rows]
    agreed = [(choice, gold) for choice, gold in agreed if choice is not None]
    omitted = 0
    for row in rows:
        # Removing an option does not change the pairwise scores of the other options.
        best = [next(v["description"] for v in row[model] if v["description"] != row["target"])
                for model in (MODEL, CLAP_NAME)]
        omitted += best[0] == best[1]
    categories = sorted(set(target))
    difference = np.array([sum((pe - clap)[np.array(target) == label]) for label in categories])
    size = np.array([target.count(label) for label in categories])
    draws = np.random.default_rng(1802).integers(len(categories), size=(20000, len(categories)))
    interval = np.percentile(difference[draws].sum(axis=1) / size[draws].sum(axis=1), [2.5, 97.5])
    return {"clips": len(rows), "clap_correct": int(clap.sum()), "pe_correct": int(pe.sum()),
            "agreed": len(agreed), "agreed_correct": sum(choice == gold for choice, gold in agreed),
            "agreed_with_target_removed": omitted, "paired_change": paired_change(rows, clap, pe),
            "category_bootstrap_95": interval.tolist()}


def score_outside(rows):
    result = {}
    for condition in ("included", "omitted"):
        before, after, before_correct, after_correct = [], [], [], []
        for row in rows:
            ch = row[condition]
            first = ch[MODEL][0]["description"]
            agreed = first == ch[CLAP_NAME][0]["description"]
            before.append(int(agreed))
            after.append(int(ch["shared_choice"] is not None))
            before_correct.append(int(agreed and first == row["target"]))
            after_correct.append(int(ch["shared_choice"] == row["target"]))
        result[condition] = {"clips": len(rows), "shared_before": sum(before), "retained_after": sum(after),
                             "correct_before": sum(before_correct), "correct_after": sum(after_correct),
                             "paired_change_in_claim_rate": paired_change(rows, np.array(before), after)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--audio", type=Path, help="directory containing the original ESC-50 WAV files")
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    rows = report["rows"]
    outside = "omitted" in rows[0]
    if args.audio:
        # Equal treatment for every class; labels enter only as the same supplied vocabulary on every file.
        labels = report["descriptions"]
        with TemporaryDirectory() as folder, IdentityReader() as ear:
            anonymous = Path(folder) / "sample.wav"
            for row in rows:
                source = args.audio / row["id"]
                data = source.read_bytes()
                if hashlib.sha256(data).hexdigest() != row["sha256"]:
                    raise ValueError(f"source differs from published recording: {source.name}")
                anonymous.write_bytes(data)
                conditions = (("included", labels), ("omitted", [s for s in labels if s != row["target"]])) if outside else [(None, labels)]
                for key, choices in conditions:
                    result = ear.read(anonymous, 0., 5., 0, choices)
                    if result["status"] != "available":
                        raise RuntimeError(result)
                    window, = result["windows"]
                    channel, = window["channels"]
                    if key is None:
                        row.update(channel)
                    else:
                        row[key] = channel
    print(json.dumps(score_outside(rows) if outside else score(rows), indent=2))


if __name__ == "__main__":
    main()
