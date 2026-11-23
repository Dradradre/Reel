"""Describe recent recorded sound at playback pauses, without a picture or listener prompt."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
from urllib.request import urlopen

import numpy as np

if __package__:
    from .common import open_recording, sample_boundary
    from .config import CAPTION_PY, HOME, MODELS
    from .local_reader import LocalReader
else:
    from common import open_recording, sample_boundary
    from config import CAPTION_PY, HOME, MODELS
    from local_reader import LocalReader

MODEL = "Labbeti/conette"
REVISION = "f7c6d0338832ee22df1e7c9d579cf5911a1a115d"
ROOT = MODELS / ("conette-" + REVISION[:8])
READY = ROOT / "ready-v1"
MAX_SECONDS = 10
MIN_SECONDS = 2
BASIS = ("A local captioner received each distinct channel's waveform, with no picture, filename, question "
         "or transcript. Each impression describes the stated interval and can misidentify actions and sources. "
         "A description of speech supplies no words to quote.")


def fetch(url, target, checksum):
    if target.is_file() and digest(target) == checksum:
        return
    target.parent.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as directory:
        part = Path(directory) / "download"
        with urlopen(url, timeout=90) as response, part.open("wb") as dest:
            shutil.copyfileobj(response, dest)
        if digest(part) != checksum:
            raise ValueError(f"unexpected checksum for {target.name}")
        part.replace(target)


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def prerequisites():
    # CoNeTTE can invoke spaCy's downloader internally when this resource is absent.
    # Keep ordinary playback strictly offline, even with an incomplete installation.
    if importlib.util.find_spec("en_core_web_sm") is None:
        raise OSError("the caption environment needs en_core_web_sm 3.7.1")
    os.environ["NLTK_DATA"] = str(MODELS / "nltk_data")
    from nltk.corpus import stopwords
    stopwords.words("english")


def load_model():
    prerequisites()
    import torch
    from conette import CoNeTTEConfig, CoNeTTEModel
    torch.set_num_threads(min(4, torch.get_num_threads()))
    cfg = CoNeTTEConfig.from_pretrained(str(ROOT), local_files_only=True)
    return CoNeTTEModel.from_pretrained(str(ROOT), config=cfg, device="cpu", offline=True, local_files_only=True).eval()


def prepare_environment():
    command = [CAPTION_PY, str(Path(__file__).resolve())]
    if READY.is_file() and Path(CAPTION_PY).is_file():
        if subprocess.run([*command, "--check"], capture_output=True).returncode == 0:
            return
    if "REEL_CAPTION_PYTHON" not in os.environ:
        env = HOME / ".venv-captions"
        uv = shutil.which("uv")
        if not Path(CAPTION_PY).exists():
            if uv:
                subprocess.run([uv, "venv", "--python", "3.10", str(env)], check=True)
            else:
                python = shutil.which("python3.10")
                if not python:
                    raise ValueError("install uv or Python 3.10 to prepare the separate caption environment")
                subprocess.run([python, "-m", "venv", str(env)], check=True)
        install = [uv, "pip", "install", "--python", CAPTION_PY] if uv else [CAPTION_PY, "-m", "pip", "install"]
        subprocess.run([*install, "conette==0.4.0", "numpy<2", "torchmetrics<1", "huggingface-hub<1",
                        "pillow", "soundfile>=0.12", "https://github.com/explosion/spacy-models/releases/download/"
                        "en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl"], check=True)
    subprocess.run([*command, "--setup"], check=True)


def setup():
    import nltk
    from conette.transforms.audioset_mapping import get_audioset_mapping_cache_path
    prefix = f"https://huggingface.co/{MODEL}/resolve/{REVISION}"
    for name, checksum in [("config.json", "631015186a12621c3398cb6f1dc3219db5a9f38611421f5e8f116f333dab407f"),
                           ("pytorch_model.bin", "271b9f4409ebbceaa0d5fe5319069a3a56e269ab0b868616d7e23f5145cc0ce2")]:
        fetch(f"{prefix}/{name}", ROOT / name, checksum)
    fetch("https://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/class_labels_indices.csv",
          get_audioset_mapping_cache_path() / "class_labels_indices.csv",
          "cdd1049833c4b86127c2773ac0d14a2754b6a6d0d1798002ed5c66e699708429")
    nltk_dir = MODELS / "nltk_data"
    nltk_dir.mkdir(exist_ok=True, parents=True)
    nltk.data.path.insert(0, str(nltk_dir))
    nltk.download("stopwords", download_dir=str(nltk_dir), quiet=True, raise_on_error=True)
    load_model()
    READY.touch()


class SceneHistory:
    def __init__(self, describe):
        self.describe = describe
        self.tail = None
        self.next_sample = None
        self.rate = None
        self.last_recent_end = None

    def _window(self, samples, active, rate, end_sample, end_s):
        signals, groups = [], []
        for i in np.flatnonzero(active):
            channel = samples[:, i]
            same = next((j for j, other in enumerate(signals) if np.array_equal(channel, other)), None)
            if same is None:
                signals.append(channel)
                groups.append([int(i) + 1])
            else:
                groups[same].append(int(i) + 1)
        notes = self.describe(signals, rate)
        return {"start_s": (end_sample - len(samples)) / rate, "end_s": min(end_s, end_sample / rate),
                "channels": [{"channels": group, "impression": note}
                             for group, note in zip(groups, notes, strict=True)]}

    def read(self, wav, first_sample, end_s):
        windows, recent = [], []
        with open_recording(wav) as recording:
            rate = recording.samplerate
            if (self.next_sample != first_sample or self.rate != rate or self.tail is None
                    or recording.channels != self.tail.shape[1]):
                self.tail = np.empty((0, recording.channels), dtype=np.float32)
                self.last_recent_end = first_sample
            self.rate = rate
            cursor = first_sample
            available = min(len(recording), max(0, sample_boundary(end_s, rate) - first_sample))
            while recording.tell() < available:
                count = min(MAX_SECONDS * rate, available - recording.tell())
                block = recording.read(count, dtype="float32", always_2d=True)
                keep = MAX_SECONDS * rate - len(block)
                if keep and len(self.tail):
                    context = np.concatenate((self.tail[-keep:], block))
                else:
                    context = block
                cursor += len(block)
                self.tail = context
                if len(context) < MIN_SECONDS * rate:
                    continue
                active = np.any(block, axis=0)
                if not np.any(active):
                    continue
                windows.append(self._window(context, active, rate, cursor, end_s))
                # Compare the new passage with the wider history. Several rapid pauses
                # share a two-second listening interval, with its own actual bounds.
                width = max(len(block), MIN_SECONDS * rate)
                if len(context) == width:
                    self.last_recent_end = cursor
                elif cursor - self.last_recent_end >= MIN_SECONDS * rate:
                    try:
                        reading = self._window(context[-width:], active, rate, cursor, end_s)
                    except (OSError, RuntimeError, ValueError):
                        reading = {"start_s": (cursor - width) / rate, "end_s": min(end_s, cursor / rate),
                                   "status": "unavailable", "channels": []}
                    recent.append(reading)
                    self.last_recent_end = cursor
            self.next_sample = cursor
        report = {"status": "available", "model": MODEL, "basis": BASIS, "windows": windows}
        if recent:
            report["recent_windows"] = recent
        return report


class SceneReader(LocalReader):
    def __init__(self):
        super().__init__([CAPTION_PY, str(Path(__file__).resolve()), "--serve"], MODEL)

    def read(self, wav, first_sample, end_s):
        return self.request({"wav": str(Path(wav).resolve()), "first_sample": first_sample, "end_s": end_s})


def inspect_scene(wav, first_sample, end_s):
    if not READY.is_file():
        return {"status": "not_prepared", "model": MODEL, "setup": "reel setup --captions"}
    with SceneReader() as reader:
        return reader.read(wav, first_sample, end_s)


class Captioner:
    def __init__(self):
        self.model = None

    def __call__(self, signals, rate):
        import torch
        if self.model is None:
            self.model = load_model()
        batch = torch.from_numpy(np.stack(signals)).unsqueeze(1)
        with torch.inference_mode():
            return self.model(batch, sr=rate)["cands"]


def serve():
    captioner = Captioner()
    reader = SceneHistory(captioner)
    for line in sys.stdin:
        try:
            with redirect_stdout(sys.stderr):
                result = reader.read(**json.loads(line))
        except Exception as error:
            result = {"status": "unavailable", "model": MODEL, "reason": str(error), "startup_failure": captioner.model is None}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    for flag in ("--serve", "--check", "--setup"):
        mode.add_argument(flag, action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve()
    elif args.setup:
        setup()
    else:
        load_model()
