"""Compare the sounds a reader and individual listeners picked on the same recordings.

Run with a JSON report containing categories and recordings. Each recording has
an id, a list of reader_tags and a list of listeners' tag lists (listener_tags).
For the published MACS panel, supply the authors' MACS.yaml with --macs.
Each recording counts equally, however many listeners it has.
"""
import argparse
from itertools import combinations
import json
from pathlib import Path
from statistics import mean


def overlap(a, b):
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def with_macs_listeners(recordings, path):
    import yaml

    with path.open() as source:
        files = yaml.safe_load(source)["files"]
    by_name = {r["filename"]: r["annotations"] for r in files}
    return [dict(r, listener_tags=[[tag for tag in a["tags"] if tag.strip()]
                                   for a in by_name[r["id"]]]) for r in recordings]


def score(categories, recordings):
    if len(recordings) < 2 or len({r["id"] for r in recordings}) != len(recordings):
        raise ValueError("supply at least two distinct recordings")
    allowed = set(categories)

    def checked(tags):
        if not isinstance(tags, list) or len(set(tags)) != len(tags) or not set(tags) <= allowed:
            raise ValueError("use distinct tags from the supplied categories")
        return set(tags)

    readers = [checked(r["reader_tags"]) for r in recordings]
    by_recording = []
    for i, row in enumerate(recordings):
        listeners = [checked(tags) for tags in row["listener_tags"]]
        if len(listeners) < 2:
            raise ValueError(f"{row['id']} needs at least two listeners")
        by_recording.append({
            "id": row["id"],
            "reader_listener": mean(overlap(readers[i], h) for h in listeners),
            "listener_listener": mean(overlap(a, b) for a, b in combinations(listeners, 2)),
            "different_recording": mean(overlap(reader, h) for j, reader in enumerate(readers)
                                        if j != i for h in listeners),
        })
    measures = ("reader_listener", "listener_listener", "different_recording")
    return {**{k: mean(r[k] for r in by_recording) for k in measures}, "by_recording": by_recording}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--macs", type=Path, help="original MACS.yaml with individual listener tags (requires PyYAML)")
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    recordings = report["recordings"]
    if args.macs:
        recordings = with_macs_listeners(recordings, args.macs)
    print(json.dumps(score(report["categories"], recordings), indent=2))
