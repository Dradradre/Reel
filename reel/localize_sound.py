"""Locate possible sounds described in words in a released recording."""
from pathlib import Path
import argparse
from contextlib import redirect_stdout
import json
import shutil
import subprocess
import os
import sys
from statistics import median

import numpy as np

if __package__:
    from .common import open_recording, resample_channels, sample_boundary
    from .config import HOME, MODELS
    from .scene_audio import fetch
    from .local_reader import LocalReader
    from .sound_events import active_channels
else:
    from common import open_recording, resample_channels, sample_boundary
    from config import HOME, MODELS
    from scene_audio import fetch
    from local_reader import LocalReader
    from sound_events import active_channels

MODEL = "facebook/pe-a-frame-small"
REVISION = "e5fc71c1f0be50279f52f292390b589780079e13"
ROOT = MODELS / ("pe-a-frame-" + REVISION[:8])
PYTHON = os.environ.get("REEL_LOCALIZE_PYTHON", str(HOME / ".venv-localize" / "bin" / "python"))
RATE, FRAMES_PER_SECOND, CONTEXT_SECONDS = 48000, 25, 10
FILES = {
    "config.json": "b71e59652d8d3bc2b4a6d5e882418c24b4ce08267df88145e5ac4ec508f4d14c",
    "tokenizer.json": "43e9ea931bb0f359d16d0e435c26abbebd9a42a7298270b8ab7e5c9fa1be1dcb",
    "tokenizer_config.json": "c16e13355ed389742590152634aa4923a6bc6cd180958d73ed43283fa1f0e8a2",
    "special_tokens_map.json": "ea97ecdbcc73713039d8d64dbb05e3689495c96657fbd9a18f5bed381be81049",
    "preprocessor_config.json": "0ee55f8b2de0849b9fdebdc96486fcbb12eafe8e63d688f3a307a24048d80efa",
    "processor_config.json": "9bf1e54b150eca3ff5301a6b9896395043906766f5fdeb04c6fc7ceb1c4f5150",
    "model.safetensors": "00ead719f02ef703ee1b55d6aee801074195d7d507a69622cda67ee4c484ed81",
}
BASIS = ("Each description is ranked separately across consecutive seconds of this excerpt. "
         "Within each second the score is the largest recorded-channel mean of 40 ms frame scores. "
         "Each frame can use up to ten seconds of the supplied audio. A best interval can exist for "
         "an absent sound. Use it to replay that part. The score supplies neither a probability nor words.")


def ready():
    return (ROOT / "ready").is_file() and Path(PYTHON).is_file() and all((ROOT / f).is_file() for f in FILES)


class Encoder:
    def __init__(self):
        import torch
        from transformers import PeAudioFrameLevelModel, PeAudioProcessor
        torch.set_num_threads(2)
        self.processor = PeAudioProcessor.from_pretrained(str(ROOT), local_files_only=True)
        self.model = PeAudioFrameLevelModel.from_pretrained(str(ROOT), local_files_only=True).eval()
        self.model.text_model.config.output_hidden_states = True

    def text(self, descriptions):
        import torch
        batches = []
        with torch.inference_mode():
            for i in range(0, len(descriptions), 16):
                tokens = self.processor.tokenizer(descriptions[i:i + 16], padding=True, truncation=True,
                                                  return_tensors="pt")
                batches.append(self.model.get_text_audio_embeds(tokens.input_ids, tokens.attention_mask))
        return torch.cat(batches)

    def score(self, channel, rate, text):
        import torch
        waveform = resample_channels(channel, rate, RATE)
        inputs = self.processor.feature_extractor([waveform], sampling_rate=RATE, padding=True, return_tensors="pt")
        with torch.inference_mode():
            audio = self.model.get_audio_embeds(**inputs)[0]
            logits = (audio @ text.T) * self.model.text_audio_logit_scale + self.model.text_audio_logit_bias
            return logits.sigmoid().cpu().numpy()


def locate_seconds(wav, start, end, first_sample, descriptions, get_encoder):
    """Rank seconds within the supplied PCM, starting at the first sample of the excerpt."""
    windows, values = [], []
    vectors = encoder = None
    with open_recording(wav) as recording:
        rate = recording.samplerate
        first = sample_boundary(start, rate) if first_sample is None else first_sample
        available = min(len(recording), sample_boundary(end, rate) - first)
        while recording.tell() < available:
            offset = recording.tell()
            pcm = recording.read(min(available - offset, CONTEXT_SECONDS * rate), dtype="float32", always_2d=True)
            count = (len(pcm) + rate - 1) // rate
            best = np.zeros((count, len(descriptions)), dtype="float32")
            for channels, signal in active_channels(pcm):
                if encoder is None:
                    encoder = get_encoder()
                    vectors = encoder.text(descriptions)
                frames = encoder.score(signal, rate, vectors)
                for i in range(count):
                    block = signal[i * rate:(i + 1) * rate]
                    n = (len(block) * FRAMES_PER_SECOND + rate - 1) // rate
                    if np.any(block):
                        scores = frames[i * FRAMES_PER_SECOND:i * FRAMES_PER_SECOND + n]
                        if len(scores) != n:
                            raise ValueError("audio encoder returned an incomplete time series")
                        best[i] = np.maximum(best[i], scores.mean(axis=0))
            for i in range(count):
                a, b = offset + i * rate, offset + min((i + 1) * rate, len(pcm))
                windows.append([max(start, (first + a) / rate), min(end, (first + b) / rate)])
            values.extend(best)
    locations = []
    for index, name in enumerate(descriptions):
        scores = [float(row[index]) for row in values]
        peak = max(scores, default=0.)
        winners = [i for i,v in enumerate(scores) if v == peak]
        # An everywhere-equal score supplies no time preference; a tied maximum has no unique location.
        interval = windows[winners[0]] if len(scores) > 1 and len(winners) == 1 else None
        locations.append({"description": name, "best_interval_s": interval,
                          "largest_1s_mean": round(peak, 4) if encoder is not None else None,
                          "median_1s_mean": round(median(scores), 4) if encoder is not None else None})
    return {"status": "available" if encoder is not None else "no_signal", "model": MODEL,
            "start_s": start, "end_s": end, "locations": locations, "basis": BASIS}


def locate(wav, start, end, descriptions, first_sample=None):
    if (not isinstance(descriptions, (list, tuple)) or not descriptions
            or any(not isinstance(s, str) or not s.strip() for s in descriptions)):
        raise ValueError("give at least one nonempty sound description to locate")
    if not ready():
        return {"status": "unavailable", "model": MODEL, "reason": "run reel setup --locate-sounds"}
    with LocalReader([PYTHON, str(Path(__file__).resolve()), "--serve"], MODEL) as reader:
        return reader.request({"wav": str(Path(wav).resolve()), "start": start, "end": end,
                               "descriptions": descriptions, "first_sample": first_sample})


def prepare_environment():
    command = [PYTHON, str(Path(__file__).resolve())]
    if ready() and subprocess.run([*command, "--check"], capture_output=True).returncode == 0:
        return
    if "REEL_LOCALIZE_PYTHON" not in os.environ:
        env = HOME / ".venv-localize"
        uv = shutil.which("uv")
        if not Path(PYTHON).exists():
            if uv:
                subprocess.run([uv, "venv", "--python", "3.12", str(env)], check=True)
            else:
                python = shutil.which("python3.12")
                if not python:
                    raise ValueError("install uv or Python 3.12 to prepare the sound location environment")
                subprocess.run([python, "-m", "venv", str(env)], check=True)
        install = [uv, "pip", "install", "--python", PYTHON] if uv else [PYTHON, "-m", "pip", "install"]
        subprocess.run([*install, "torch==2.15.0", "transformers==5.24.0", "scipy", "soundfile", "pillow"], check=True)
    for name, checksum in FILES.items():
        fetch(f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}", ROOT / name, checksum)
    subprocess.run([*command, "--check"], check=True)
    (ROOT / "ready").touch()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.check:
        encoder = Encoder()
        encoder.score(np.zeros(RATE, dtype="float32"), RATE, encoder.text(["a sound"]))
    else:
        for line in sys.stdin:
            try:
                with redirect_stdout(sys.stderr):
                    result = locate_seconds(**json.loads(line), get_encoder=Encoder)
            except Exception as error:
                result = {"status": "unavailable", "model": MODEL, "reason": str(error)}
            print(json.dumps(result), flush=True)
