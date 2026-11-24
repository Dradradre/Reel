"""Compare freely supplied source descriptions with the same released sound in two audio encoders."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

if __package__:
    from .common import open_recording, resample_channels, sample_boundary
    from .compare_sounds import CLAP_NAME, Comparator, vocabulary
    from .config import MODELS
    from .local_reader import LocalReader
    from .localize_sound import PYTHON, prepare_runtime
    from .sound_events import active_channels
else:
    from common import open_recording, resample_channels, sample_boundary
    from compare_sounds import CLAP_NAME, Comparator, vocabulary
    from config import MODELS
    from local_reader import LocalReader
    from localize_sound import PYTHON, prepare_runtime
    from sound_events import active_channels

MODEL = "facebook/pe-av-large"
REVISION = "0d24878d4107d64bef49e53602fc34ce6f94f6d8"
READY = MODELS / ("sound-identity-" + REVISION[:8])
RATE = 48000
BASIS = ("Two audio encoders independently rank the same descriptions on the same recorded channel and interval. "
         "PE-AV uses its audio-text dot product and CLAP uses cosine. Compare scores only within each model. "
         "A shared choice is relative to the supplied alternatives. All alternatives can be absent. "
         "Different choices leave the source unresolved. No speech text was decoded.")


class Encoders:
    def __init__(self, download=False):
        import torch
        from huggingface_hub import snapshot_download
        from transformers import PeAudioModel, PeAudioProcessor, logging

        # The official audiovisual checkpoint contains extra visual weights. Load its audio and text branches.
        logging.set_verbosity_error()
        torch.set_num_threads(min(2, torch.get_num_threads()))
        path = snapshot_download(MODEL, revision=REVISION, cache_dir=MODELS / "hub", local_files_only=not download,
                                 allow_patterns=["*.json", "model.safetensors"])
        self.processor = PeAudioProcessor.from_pretrained(path, local_files_only=True)
        self.model, info = PeAudioModel.from_pretrained(path, local_files_only=True, output_loading_info=True)
        if info.get("missing_keys") or info.get("mismatched_keys"):
            raise ValueError("incomplete weights for the audio or text encoder")
        self.model.eval()
        self.model.text_model.config.output_hidden_states = True
        self.clap = Comparator(download=download, device="cpu")
        self.catalogue = vocabulary(download=download)
        self.last = None
        self.text = None

    def prepare(self, descriptions):
        import torch

        labels = descriptions or self.catalogue
        if labels != self.last:
            phrases = ["the sound of " + label.lower() for label in labels]
            with torch.inference_mode():
                parts = []
                for start in range(0, len(phrases), 8):
                    batch = self.processor.tokenizer(phrases[start:start + 8], padding=True, truncation=True,
                                                     return_tensors="pt")
                    parts.append(self.model.get_text_audio_embeds(batch.input_ids, batch.attention_mask))
                self.text = torch.cat(parts), self.clap.text_features(phrases)
            self.last = list(labels)
        return labels

    def scores(self, channel, rate):
        import torch

        wave = resample_channels(channel, rate, RATE)
        batch = self.processor.feature_extractor([wave], sampling_rate=RATE, padding=True, return_tensors="pt")
        with torch.inference_mode():
            pe = (self.model.get_audio_embeds(**batch) @ self.text[0].T)[0].cpu().numpy()
        return pe, self.clap.similarities([wave], self.text[1])[0]


def prepared_for(descriptions):
    return READY.is_file() and len({s.strip().casefold() for s in descriptions if s.strip()}) >= 2


def ranking(labels, scores):
    scores = np.asarray(scores)
    if scores.shape != (len(labels),) or not np.isfinite(scores).all():
        raise ValueError("invalid acoustic ranking")
    order = np.argsort(-scores, kind="stable")
    first = labels[order[0]] if len(order) >= 2 and scores[order[0]] > scores[order[1]] else None
    nearest = [{"description": labels[i], "score": round(float(scores[i]), 5)} for i in order[:3]]
    return first, nearest


def read_identity(wav, start, end, first_sample, descriptions, get_encoders):
    cleaned = list({d.strip().casefold(): d.strip() for d in descriptions}.values())
    if "" in cleaned or (descriptions and len(cleaned) < 2):
        raise ValueError("give two or more distinct sound descriptions, or omit them for AudioSet")
    model = labels = None
    windows = []
    with open_recording(wav) as recording:
        rate = recording.samplerate
        first = sample_boundary(start, rate) if first_sample is None else first_sample
        count = min(len(recording), max(0, sample_boundary(end, rate) - first))
        while recording.tell() < count:
            left = recording.tell()
            pcm = recording.read(min(10 * rate, count - left), dtype="float32", always_2d=True)
            groups = []
            for channels, signal in active_channels(pcm):
                if model is None:
                    model = get_encoders()
                    labels = model.prepare(cleaned)
                pe, clap = [ranking(labels, vector) for vector in model.scores(signal, rate)]
                agree = pe[0] is not None and pe[0] == clap[0]
                groups.append({"channels": channels, "status": "same_choice" if agree else "unresolved",
                               "shared_choice": pe[0] if agree else None, MODEL: pe[1], CLAP_NAME: clap[1]})
            windows.append({"start_s": max(start, (first + left) / rate),
                            "end_s": min(end, (first + recording.tell()) / rate),
                            "status": "signal" if groups else "digital_silence", "channels": groups})
    return {"status": "available", "model": MODEL, "second_model": CLAP_NAME, "basis": BASIS,
            "alternatives": cleaned or "AudioSet", "windows": windows}


class IdentityReader(LocalReader):
    def __init__(self):
        super().__init__([PYTHON, str(Path(__file__).resolve()), "--serve"], MODEL)

    def read(self, wav, start, end, first_sample, descriptions=()):
        if not READY.is_file():
            return {"status": "not_prepared", "model": MODEL, "setup": "reel setup --identify-sounds"}
        return self.request({"wav": str(Path(wav).resolve()), "start": start, "end": end, "first_sample": first_sample,
                             "descriptions": list(descriptions)})


def serve():
    model = None

    def get_encoders():
        nonlocal model
        if model is None:
            model = Encoders()
        return model

    for line in sys.stdin:
        try:
            with redirect_stdout(sys.stderr):
                result = read_identity(**json.loads(line), get_encoders=get_encoders)
        except Exception as error:
            result = {"status": "unavailable", "model": MODEL, "reason": str(error)}
        print(json.dumps(result), flush=True)


def prepare_environment():
    prepare_runtime()
    subprocess.run([PYTHON, str(Path(__file__).resolve()), "--setup"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ("--serve", "--setup"):
        modes.add_argument(mode, action="store_true")
    if parser.parse_args().serve:
        serve()
    else:
        Encoders(download=True).prepare(["a steady hum", "a short click"])
        READY.touch()
