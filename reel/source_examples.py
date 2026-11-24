"""Recognize sounds by comparing released PCM with named reference recordings and source text."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import csv
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile

import numpy as np

if __package__:
    from .common import open_recording, resample_channels, sample_boundary
    from .compare_sounds import CLAP_NAME, Comparator, vocabulary
    from .config import MODELS
    from .identify_sound import HOP_SAMPLES, MODEL, PerceptionEncoder, RATE, REVISION
    from .local_reader import LocalReader
    from .localize_sound import PYTHON, prepare_runtime
    from .scene_audio import digest, fetch
    from .sound_focus import FocusHistory
    from .sound_events import active_channels
else:
    from common import open_recording, resample_channels, sample_boundary
    from compare_sounds import CLAP_NAME, Comparator, vocabulary
    from config import MODELS
    from identify_sound import HOP_SAMPLES, MODEL, PerceptionEncoder, RATE, REVISION
    from local_reader import LocalReader
    from localize_sound import PYTHON, prepare_runtime
    from scene_audio import digest, fetch
    from sound_focus import FocusHistory
    from sound_events import active_channels

MANIFEST = Path(__file__).with_name("data") / "source_examples.csv"
DATA_REVISION = "ccf1acaff12f3f4a4c10052dddafbb6d22152c9b"
ROOT = MODELS / ("sound-examples-" + REVISION[:8])
INDEX = ROOT / "index-v1.npz"
SECONDS = 4


def unit(x):
    x = np.asarray(x, dtype=np.float32)
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-10)


def waveform_vector(encoder, samples, rate):
    return encoder.audio(samples, rate)[0].cpu().numpy()


def ready():
    return INDEX.is_file() and Path(PYTHON).is_file()


def setup(audio_dir=None):
    """Cache each encoded reference separately so setup can resume after an interrupted download."""
    ROOT.mkdir(exist_ok=True, parents=True)
    with MANIFEST.open() as f:
        manifest = list(csv.DictReader(f))
    source_hash = digest(MANIFEST)
    if INDEX.is_file():
        with np.load(INDEX, allow_pickle=False) as current:
            if str(current["manifest_sha256"]) == source_hash:
                return
    encoder = PerceptionEncoder(download=True)
    names = sorted(set(vocabulary(download=True)) | {r["name"] for r in manifest})
    text = encoder.encode_text(names).cpu().numpy()
    encoder.audio_only()
    partial = ROOT / "reference-v1"
    partial.mkdir(exist_ok=True)
    vectors = []
    with tempfile.TemporaryDirectory(dir=ROOT) as temp:
        scratch = Path(temp)
        for row in manifest:
            identity = row["freesound_id"]
            stored = partial / (row["sha256"] + ".npy")
            if not stored.is_file():
                wav = Path(audio_dir) / (identity + ".wav") if audio_dir else scratch / (identity + ".wav")
                if audio_dir:
                    if digest(wav) != row["sha256"]:
                        raise ValueError(f"reference recording {identity} has a different checksum")
                else:
                    url = f"https://huggingface.co/datasets/Fhrozen/FSD50k/resolve/{DATA_REVISION}/clips/dev/{identity}.wav"
                    fetch(url, wav, row["sha256"])
                with open_recording(wav) as f:
                    samples = f.read(min(len(f), SECONDS * f.samplerate), dtype="float32", always_2d=True)[:, 0]
                    vec = waveform_vector(encoder, samples, f.samplerate)
                np.save(scratch / "reference.npy", vec)
                (scratch / "reference.npy").replace(stored)
                if not audio_dir:
                    wav.unlink()
            vectors.append(np.load(stored, allow_pickle=False))
        output = scratch / "index.npz"
        np.savez_compressed(output, names=names, text=unit(text), vectors=unit(np.stack(vectors)),
                            name_ids=[names.index(r["name"]) for r in manifest], manifest_sha256=source_hash)
        output.replace(INDEX)


class SourceIndex:
    def __init__(self, path=INDEX):
        with np.load(path, allow_pickle=False) as data:
            self.names = data["names"].tolist()
            self.text = data["text"]
            self.vectors = data["vectors"]
            self.name_ids = data["name_ids"]
        self.groups = [(i, np.flatnonzero(self.name_ids == i)) for i in np.unique(self.name_ids)]

    def scores(self, vector):
        query = unit(vector)
        similarity = self.vectors @ query
        references = np.full(len(self.names), -np.inf, dtype=np.float32)
        for i, rows in self.groups:
            nearest = np.partition(similarity[rows], -3)[-3:]
            references[i] = np.mean(nearest)
        return self.text @ query, references

    def identify(self, vector):
        return self.match(*self.scores(vector))

    def match(self, text, examples):
        # Shared thresholds across all sources, calibrated on separate uploaders.
        word, sound = int(np.argmax(text)), int(np.argmax(examples))
        if (word != sound or text[word] < 0.10 or examples[sound] < 0.25
                or np.count_nonzero(text == text[word]) != 1
                or np.count_nonzero(examples == examples[sound]) != 1):
            return None
        return {"name": self.names[word], "text_similarity": round(float(text[word]), 4),
                "reference_similarity": round(float(examples[word]), 4)}

    def corroborated(self, text, examples, acoustic_text):
        # All names compete in both text catalogues, including those without example recordings.
        # Give tied scores the least favourable rank in the tie.
        if (not np.isfinite(text).all() or not np.isfinite(acoustic_text).all()
                or not np.all(np.isfinite(examples) | np.isneginf(examples))):
            return None
        ranks = [len(row) - np.searchsorted(np.sort(row), row) for row in (text, examples, acoustic_text)]
        product = np.prod(ranks, axis=0)
        supported = np.flatnonzero(np.isfinite(examples))
        if len(supported) < 2:
            return None
        best, runner_up = supported[np.argsort(product[supported], kind="stable")[:2]]
        if (max(rank[best] for rank in ranks) > 3 or product[runner_up] <= 8 * product[best]
                or text[best] < .10 or examples[best] < .55 or acoustic_text[best] < .25):
            return None
        return {"name": self.names[best], "text_similarity": round(float(text[best]), 4),
                "reference_similarity": round(float(examples[best]), 4),
                "corroborating_model": CLAP_NAME, "corroborating_cosine": round(float(acoustic_text[best]), 4)}


class FocusMatcher:
    """Use cached CLAP weights for additional short-window names; the ordinary reader works alone."""
    def __init__(self):
        self.model = self.text = None
        self.unavailable = False

    def scores(self, signal, rate, names):
        if self.unavailable:
            return None
        try:
            if self.model is None:
                self.model = Comparator(device="cpu")
                self.text = self.model.text_features(["the sound of " + name.lower() for name in names])
            return self.model.similarities([resample_channels(signal, rate, RATE)], self.text)[0]
        except (ImportError, OSError, RuntimeError, ValueError):
            self.unavailable = True
            self.model = self.text = None
            return None


class SourceHistory:
    def __init__(self, identify, identify_focus=None):
        self.identify = identify
        self.identify_focus = identify_focus
        self.tail = None
        self.format = self.end = None

    def read(self, wav, first_sample, end_s):
        windows, focused = [], []
        with open_recording(wav) as recording:
            rate, channels = recording.samplerate, recording.channels
            available = min(len(recording), max(0, sample_boundary(end_s, rate) - first_sample))
            if (self.end, self.format) != (first_sample, (rate, channels)):
                self.tail = np.empty((0, channels), dtype=np.float32)
                self.focus = FocusHistory()
            self.format = rate, channels
            while recording.tell() < available:
                fresh = recording.read(min(SECONDS * rate, available - recording.tell()), dtype="float32", always_2d=True)
                self.tail = np.concatenate([self.tail, fresh])[-SECONDS * rate:].copy()
                stop = first_sample + recording.tell()
                start = stop - len(self.tail)
                groups = []
                if len(self.tail) * RATE >= HOP_SAMPLES * rate:
                    for ids, signal in active_channels(self.tail):
                        active = [i for i in ids if np.any(fresh[:, i - 1])]
                        if active and (result := self.identify(signal, rate)):
                            groups.append(dict(channels=active, **result))
                for channels_seen, waveform, left, right in self.focus.passages(self.tail, rate, start, stop - len(fresh)):
                    wide_name = next((g["name"] for g in groups if channels_seen[0] in g["channels"]), None)
                    if self.identify_focus is None:
                        result = self.identify(waveform[left:right], rate)
                        if result and result["name"] != wide_name:
                            result = None
                    else:
                        try:
                            result = self.identify_focus(waveform[left:right], rate, wide_name)
                        except (OSError, RuntimeError, ValueError):
                            continue  # Keep the valid whole-window reading if the extra pass fails.
                    if result:
                        focused.append({"start_s": (start + left) / rate, "end_s": min(end_s, (start + right) / rate),
                                        "channels": [dict(channels=channels_seen, **result)]})
                if groups:
                    windows.append(dict(start_s=start / rate, end_s=min(end_s, stop / rate), channels=groups))
            self.end = first_sample + available
        return {"status": "available", "windows": windows, **({"focused_windows": focused} if focused else {}), "model": MODEL,
                "basis": "Estimated source names from the released channel. Reference clips and source text "
                         "are compared with the recorded sound. A match can describe an earlier part of its "
                         "window. A focused window was also heard separately at its recorded level. Missing matches leave the source open. Speech labels supply no words."}


class SourceReader(LocalReader):
    def __init__(self):
        super().__init__([PYTHON, str(Path(__file__).resolve()), "--serve"], MODEL)

    def read(self, wav, first_sample, end_s, *, isolated=False):
        return self.request(dict(wav=str(Path(wav).resolve()), first_sample=first_sample, end_s=end_s, isolated=isolated))


def serve():
    encoder = index = None
    def identify(samples, rate):
        nonlocal encoder, index
        if index is None:
            index = SourceIndex()
        if encoder is None:
            encoder = PerceptionEncoder()
            encoder.audio_only()
        return index.identify(waveform_vector(encoder, samples, rate))

    verifier = FocusMatcher()

    def identify_focus(samples, rate, wide_name):
        # The whole interval was scored first, so the encoder and index are already resident.
        text, examples = index.scores(waveform_vector(encoder, samples, rate))
        narrow = index.match(text, examples)
        if narrow and narrow["name"] == wide_name:
            return narrow
        # If the first two rankings share no top-three name, a third vote cannot accept a match.
        if not np.any((text >= np.partition(text, -3)[-3]) & (examples >= np.partition(examples, -3)[-3])):
            return None
        other = verifier.scores(samples, rate, index.names)
        return index.corroborated(text, examples, other) if other is not None else None

    history = SourceHistory(identify, identify_focus)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            active = SourceHistory(identify, identify_focus) if request.pop("isolated", False) else history
            with redirect_stdout(sys.stderr):
                result = active.read(**request)
        except Exception as error:
            result = {"status": "unavailable", "reason": str(error), "startup_failure": encoder is None or index is None}
            history = SourceHistory(identify, identify_focus)
        print(json.dumps(result), flush=True)


def prepare_environment():
    prepare_runtime()
    subprocess.run([PYTHON, str(Path(__file__).resolve()), "--setup"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--setup", action="store_true")
    mode.add_argument("--serve", action="store_true")
    if parser.parse_args().setup:
        setup(os.environ.get("REEL_FSD50K_AUDIO"))
        # Existing installations can still use their original index while this download is pending.
        Comparator(download=True, device="cpu")
    else:
        serve()
