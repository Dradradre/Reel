"""Score Reel's timing against Modal's hand-marked onsets (requires h5py for this check only)."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

from reel.onsets import OnsetTrace
from reel.sound_glimpses import SoundChanges


def split(name):
    return "development" if hashlib.sha256(name.encode()).digest()[0] % 4 == 0 else "holdout"


def score(reference, measured, tolerance=.05):
    """Maximum number of ordered one-to-one matches within the tolerance."""
    i = j = hits = 0
    while i < len(reference) and j < len(measured):
        delta = measured[j] - reference[i]
        if delta < -tolerance:
            j += 1
        elif delta > tolerance:
            i += 1
        else:
            hits += 1
            i += 1
            j += 1
    return {"matched": hits, "missed": len(reference) - hits, "extra": len(measured) - hits}


def summarize(rows):
    out = {}
    for key in ("band_changes", "band_rises", "attacks"):
        counts = Counter()
        for row in rows:
            counts.update(score(row["reference_s"], row[key]))
        tp, fp, fn = (counts[k] for k in ("matched", "extra", "missed"))
        out[key] = dict(counts, f1=round(2 * tp / (2 * tp + fp + fn), 4))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hdf5", type=Path)
    parser.add_argument("--split", choices=("development", "holdout"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    import h5py

    rows = []
    with h5py.File(args.hdf5) as db:
        for name, ds in db.items():
            if split(name) != args.split:
                continue
            audio = ds[:].reshape(-1, 1)
            rate = int(ds.attrs["sampling_rate"])
            old, new = SoundChanges(), OnsetTrace()
            bands, attacks = [], []
            for first in range(0, len(audio), 4096):
                block = audio[first:first + 4096]
                bands.extend(old.scan_samples(block, rate, first))
                attacks.extend(new.scan(block, rate, first))
            rows.append(dict(recording=name, group=ds.attrs["type"].decode(),
                             reference_s=sorted((np.array(ds.attrs["onsets"]) / rate).tolist()),
                             band_changes=[e["time_s"] for e in bands],
                             band_rises=[e["time_s"] for e in bands if "rise" in e],
                             attacks=[e["time_s"] for e in attacks]))
    with args.hdf5.open("rb") as data:
        digest = hashlib.file_digest(data, "sha256").hexdigest()
    report = dict(dataset="John Glover et al., Modal hand-annotated onset database",
                  dataset_sha256=digest,
                  split=args.split, tolerance_s=.05, recordings=len(rows), totals=summarize(rows), rows=rows)
    for row in rows:
        row["scores"] = summarize([row])
        row["reference_count"] = len(row.pop("reference_s"))
    args.out.write_text(json.dumps(report, indent=1) + "\n")
    print(report["recordings"], report["totals"])


if __name__ == "__main__":
    main()
