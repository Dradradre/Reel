"""Compare the optional picture depth reader with DA-2K point annotations.

Supply the original DA-2K.zip. Eight images per scenario are selected by hash.
"""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def order(key):
    return hashlib.sha256(("reel-picture-depth/" + key).encode()).digest()


def measure(archive):
    from reel.depth import Depth

    model = Depth()
    rows = []
    with zipfile.ZipFile(archive) as z, tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        mapping = json.loads(z.read("DA-2K/annotations.json"))
        groups = defaultdict(list)
        for path in mapping:
            groups[path.split("/")[1]].append(path)
        for group, paths in sorted(groups.items()):
            for path in sorted(paths, key=order)[:8]:
                image = folder / "original.jpg"
                image.write_bytes(z.read("DA-2K/" + path))
                with Image.open(image) as im:
                    width, height = im.size
                pairs = []
                for i, annotation in enumerate(mapping[path]):
                    names = ["point1", "point2"]
                    if order(path + str(i))[0] % 2:
                        names.reverse()
                    coords = [[annotation[k][1] / (width - 1), annotation[k][0] / (height - 1)] for k in names]
                    pairs.append((coords, names.index(annotation["closer_point"])))
                points = [xy for coords, _ in pairs for xy in coords]
                result = model.measure(image, folder / "depth.png", points)
                values = result["relative_nearness"]
                for i, (coords, truth) in enumerate(pairs):
                    a, b = values[2 * i : 2 * i + 2]
                    found = None if abs(a - b) < 0.02 else int(b > a)
                    rows.append(
                        dict(
                            scenario=group, image=path, points=coords, closer=truth, nearness=[a, b], choice=found
                        )
                    )
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("archive", type=Path, nargs="?")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    rows = (
        measure(args.archive)
        if args.archive
        else json.loads((ROOT / "docs/benchmarks/event-paths.json").read_text())["depth"]
    )
    if args.output:
        args.output.write_text(json.dumps(rows) + "\n")
    print(
        dict(
            images=len({r["image"] for r in rows}),
            pairs=len(rows),
            correct=sum(r["choice"] == r["closer"] for r in rows),
            unresolved=sum(r["choice"] is None for r in rows),
        )
    )
