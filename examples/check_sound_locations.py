"""Rescore replay locations and reader answers against a TUT evaluation metadata ZIP.

python examples/check_sound_locations.py docs/benchmarks/sound-location.json TUT-...meta.zip
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import mean
from zipfile import ZipFile


def score(panel, metadata):
    events = defaultdict(list)
    with ZipFile(metadata) as archive:
        name, = (n for n in archive.namelist() if n.endswith("/meta.txt"))
        for line in archive.read(name).decode().splitlines():
            path, _, left, right, label, *_ = line.split("\t")
            events[path].append((float(left), float(right), label.strip()))

    locations = {"queries": 0, "clap": 0, "pe_a_frame": 0, "uniform_expected": 0.}
    readers = {arm: {"found": 0, "extra": 0, "missed": 0, "per_clip_overlap": []} for arm in ("base", "query")}
    for clip in panel:
        start, stop = clip["start_s"], clip["end_s"]
        if clip["file"] not in events:
            raise ValueError(f"missing recording in metadata: {clip['file']}")
        truth = set()
        for option in clip["locations"]:
            label = option["description"]
            active = [any(c == label and a < t + 1 and b > t for a, b, c in events[clip["file"]])
                      for t in range(start, stop)]
            if any(active):
                truth.add(label)
            if not any(active) or all(active):
                continue
            locations["queries"] += 1
            locations["uniform_expected"] += mean(active)
            for model in ("clap", "pe_a_frame"):
                second = option[model]
                if second is not None:
                    locations[model] += active[second]
        for arm, report in readers.items():
            selected = set(clip["reader"][arm])
            report["found"] += len(selected & truth)
            report["extra"] += len(selected - truth)
            report["missed"] += len(truth - selected)
            union = truth | selected
            report["per_clip_overlap"].append(len(truth & selected) / len(union) if union else 1.)
    return {"locations": locations, "reader": {
        arm: {k: v for k, v in data.items() if k != "per_clip_overlap"} |
             {"mean_clip_overlap": mean(data["per_clip_overlap"])} for arm, data in readers.items()}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("metadata", type=Path)
    args = parser.parse_args()
    print(json.dumps(score(json.loads(args.report.read_text())["panel"], args.metadata), indent=2))
