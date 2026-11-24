"""Retrieve human descriptions of other recordings with similar sound."""
from __future__ import annotations

import csv
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
import tempfile

import numpy as np

if __package__:
    from .compare_sounds import CLAP_NAME, WINDOW_SECONDS, Comparator, native_windows
    from .config import COMPARE_PY, MODELS
    from .local_reader import LocalReader
    from .sound_events import active_channels
else:
    from compare_sounds import CLAP_NAME, WINDOW_SECONDS, Comparator, native_windows
    from config import COMPARE_PY, MODELS
    from local_reader import LocalReader
    from sound_events import active_channels

REVISION = "8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a"
ROOT = MODELS / ("clotho-development-clap-" + REVISION[:8])
DATASET = "https://zenodo.org/records/4783391"
CSV = "clotho_captions_development.csv"
FILES = {
    "LICENSE": "4701019c81918fc205f6c675508efd4e4eff81339aa2660a632bd67fd0baffbf",
    CSV: "df2e5b92060b4bb23311f8b3a7f82241d900b9c4f62b0cc467ac2ce5e9c52886",
}
BASIS = ("These people described other recordings. Listen for acoustic details shared by several matches. "
         "Their sources, actions, words and event order can differ from this recording. "
         "Cosine is a relative text match, not confidence that an event occurred.")
# One analysis window of this pinned CLAP feature extractor at 48 kHz.
FFT_SAMPLES = 1024


def ready():
    return all((ROOT / f).is_file() for f in (*FILES, "vectors.npy"))


def caption_rows(path):
    captions, groups, seen = [], [], set()
    with path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            for i in range(1, 6):
                sentence = row[f"caption_{i}"].strip()
                key = sentence.casefold()
                if sentence and key not in seen:
                    captions.append(sentence)
                    groups.append(row["file_name"])
                    seen.add(key)
    return captions, groups


def setup():
    if __package__:
        from .scene_audio import fetch
    else:
        from scene_audio import fetch
    for name, digest in FILES.items():
        fetch(f"{DATASET}/files/{name}", ROOT / name, digest)
    captions, _ = caption_rows(ROOT / CSV)
    # The weights and corpus live in separate caches and either can have been removed.
    model = Comparator(download=True, revision=REVISION)
    if ready():
        try:
            index = CaptionIndex()
            if not np.isfinite(index.vectors).all():
                raise ValueError("nonfinite caption index")
        except (OSError, ValueError):
            pass  # Replace an incomplete index only after its replacement has been built.
        else:
            return
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        dest = Path(tmp) / "vectors.npy"
        vectors = None
        for start in range(0, len(captions), 128):
            chunk = model.text_features(captions[start:start + 128]).cpu().numpy()
            if vectors is None:
                vectors = np.lib.format.open_memmap(dest, mode="w+", dtype=np.float32,
                                                    shape=(len(captions), chunk.shape[1]))
            vectors[start:start + len(chunk)] = chunk
        del vectors
        dest.replace(ROOT / "vectors.npy")


class CaptionIndex:
    def __init__(self):
        self.captions, self.groups = caption_rows(ROOT / CSV)
        self.vectors = np.load(ROOT / "vectors.npy", mmap_mode="r", allow_pickle=False)
        if self.vectors.shape != (len(self.captions), 512) or self.vectors.dtype != np.float32:
            raise ValueError("caption index does not match the installed corpus and encoder")

    def nearest(self, vector):
        scores = self.vectors @ vector
        if not np.isfinite(scores).all():
            raise ValueError("invalid caption similarity")
        selected, groups = [], set()
        for i in np.argsort(-scores, kind="stable"):
            group = self.groups[i]
            if group in groups:
                continue
            selected.append({"caption": self.captions[i], "cosine": round(float(scores[i]), 4)})
            groups.add(group)
            if len(selected) == 4:
                break
        return selected


def match_captions(wav, start, end, first, get_resources):
    windows = []
    for stamp, pcm in native_windows(wav, start, end, first, WINDOW_SECONDS):
        groups = []
        for indices, signal in active_channels(pcm):
            if len(signal) < FFT_SAMPLES:
                groups.append({"channels": indices, "status": "insufficient_encoder_input", "matches": []})
                continue
            model, index = get_resources()
            vector = model.audio_features([signal], "pad")[0].cpu().numpy()
            groups.append({"channels": indices, "matches": index.nearest(vector)})
        windows.append({**stamp, "active_channels": groups})
    return {"status": "available", "model": CLAP_NAME, "revision": REVISION, "windows": windows,
            "source": "Clotho v2.1 development captions, Audio Research Group at Tampere University",
            "dataset": DATASET, "basis": BASIS}


def inspect_matches(wav, start, end, first, *, verify=False):
    if not ready():
        return {"status": "not_prepared", "setup": "reel setup --caption-matches"}
    command = [COMPARE_PY, str(Path(__file__).resolve()), "--serve"]
    with LocalReader(command, CLAP_NAME) as reader:
        report = reader.request(dict(wav=str(Path(wav).resolve()), start=start, end=end, first=first))
    if verify:
        if __package__:
            from .check_captions import checked_report
        else:
            from check_captions import checked_report
        return checked_report(report, wav, start, end, first)
    return report


def serve():
    resources = None

    def get_resources():
        nonlocal resources
        if resources is None:
            resources = Comparator(revision=REVISION), CaptionIndex()
        return resources

    for line in sys.stdin:
        try:
            with redirect_stdout(sys.stderr):
                result = match_captions(**json.loads(line), get_resources=get_resources)
        except Exception as error:
            result = {"status": "unavailable", "model": CLAP_NAME, "reason": str(error)}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["--setup"]:
        setup()
    elif sys.argv[1:] == ["--serve"]:
        serve()
