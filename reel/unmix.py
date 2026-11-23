"""Rehear one channel of a released recording with the YFCC100M MixIT separator."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

import numpy as np

if __package__:
    from .compare_sounds import SoundMatcher
    from .config import HOME, MODELS
    from .local_reader import LocalReader
else:
    from compare_sounds import SoundMatcher
    from config import HOME, MODELS
    from local_reader import LocalReader

RATE = 16000
MAX_SECONDS = 10
MODEL = "Google Research MixIT (YFCC100M)"
ROOT = MODELS / "yfcc100m-mixit"
ENV = HOME / ".venv-unmix"
PYTHON = os.environ.get("REEL_UNMIX_PYTHON", str(ENV / "bin/python"))
BUCKET = "https://storage.googleapis.com/gresearch/sound_separation/yfcc100m_mixit_model_checkpoint/"
CHECKPOINT = "model.ckpt-3547330"
FILES = {
    "inference.meta": "02a84d54275b9d8d9533a7ebe51dfcd4dfb0707e0404835187002c3bb31e704d",
    CHECKPOINT + ".data-00000-of-00001": "a22a5b50ebe17839dcdb769159b349a83638edec9818f406ad143a6e98d5898d",
    CHECKPOINT + ".index": "6e03079c149e68f80ac247551f4f10414a0b2443579d2ca3dd4557147a53bf15",
    "LICENSE": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}
BASIS = ("Eight estimates ranked by RMS within this channel and interval. One event can occupy several estimates, "
         "and several events can share one. Ranks do not identify a source across intervals. "
         "The mix is the original channel at its native rate. Estimates use 16 kHz on the same film clock. "
         "Use the original mix and timed speech evidence to check any interpretation.")


def ready():
    return all((ROOT / name).is_file() for name in FILES)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_weights():
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        for name, checksum in FILES.items():
            target = ROOT / name
            if target.is_file() and digest(target) == checksum:
                continue
            downloaded = Path(tmp) / name
            urllib.request.urlretrieve(BUCKET + name, downloaded)
            if digest(downloaded) != checksum:
                raise ValueError(f"download checksum mismatch for {name}")
            downloaded.replace(target)


def prepare_environment():
    if "REEL_UNMIX_PYTHON" not in os.environ:
        uv = shutil.which("uv")
        if not Path(PYTHON).exists():
            cmd = [uv, "venv", "--python", "3.12"] if uv else [shutil.which("python3.12") or sys.executable, "-m", "venv"]
            subprocess.run([*cmd, str(ENV)], check=True)
        cmd = [uv, "pip", "install", "--python", PYTHON] if uv else [PYTHON, "-m", "pip", "install"]
        subprocess.run([*cmd, "tensorflow==2.20.0", "soundfile", "scipy", "pillow"], check=True)
    download_weights()
    subprocess.run([PYTHON, str(Path(__file__).resolve()), "--check"], check=True)


class Separator:
    def __init__(self):
        if not ready():
            raise ValueError("prepare the separator with reel setup --layers")
        import tensorflow.compat.v1 as tf
        graph = tf.Graph()
        self.session = tf.Session(graph=graph)
        with graph.as_default():
            saver = tf.train.import_meta_graph(str(ROOT / "inference.meta"), clear_devices=True)
            saver.restore(self.session, str(ROOT / CHECKPOINT))
        self.input = graph.get_tensor_by_name("input_audio/receiver_audio:0")
        self.output = graph.get_tensor_by_name("denoised_waveforms:0")
        self.previous = None
        self.estimates = None

    def separate(self, mix):
        key = hashlib.sha256(mix.tobytes()).digest()
        if self.previous != key:
            estimates = self.session.run(self.output, {self.input: mix[None, None, :]})[0]
            if estimates.shape != (8, len(mix)) or not np.all(np.isfinite(estimates)):
                raise ValueError("separator returned incomplete audio")
            power = np.mean(np.square(estimates, dtype=np.float64), axis=1)
            self.estimates = estimates[np.argsort(-power, kind="stable")]
            self.previous = key
        return self.estimates


def level(samples):
    power = np.mean(np.square(samples, dtype=np.float64))
    return round(float(10 * np.log10(power)), 2) if power else None


def save_wav(directory, samples):
    from scipy.io import wavfile
    # Keep float PCM without a wall-clock timestamp in the header, including on replay.
    name = "layer_pcm_" + hashlib.sha256(samples.tobytes()).hexdigest() + ".wav"
    path = directory / name
    if not path.is_file():
        with tempfile.TemporaryDirectory(dir=directory) as tmp:
            wav = Path(tmp) / name
            wavfile.write(wav, RATE, samples)
            wav.replace(path)
    return str(path)


def listen_layer(get_model, wav, channel, layer):
    from scipy.signal import resample_poly
    if __package__:
        from .common import open_recording
    else:
        from common import open_recording
    path = Path(wav)
    with open_recording(path) as recording:
        if not 1 <= channel <= recording.channels or (layer is not None and not 1 <= layer <= 8):
            raise ValueError("choose an existing recording channel and a layer from 1 to 8")
        rate = recording.samplerate
        if not 0 < len(recording) <= math.ceil(MAX_SECONDS * rate):
            raise ValueError(f"choose a nonempty interval of at most {MAX_SECONDS} seconds")
        samples = np.empty(len(recording), np.float32)
        offset = 0
        energy = 0.0
        for block in recording.blocks(blocksize=32768, dtype="float64", always_2d=True):
            mono = block[:, channel - 1]
            energy += np.dot(mono, mono)
            samples[offset:offset + len(block)] = mono
            offset += len(block)
    divisor = math.gcd(rate, RATE)
    mix = resample_poly(samples, RATE // divisor, rate // divisor) if rate != RATE else samples.copy()
    result = {"model": MODEL, "channel": channel, "selected_layer": layer, "basis": BASIS,
              "original_rate_hz": rate, "original_samples": len(samples), "rate_hz": RATE, "samples": len(mix),
              "mix_rms_dbfs": round(float(10 * np.log10(energy / len(samples))), 2) if energy else None,
              "encoder_mix_rms_dbfs": level(mix),
              "mix_wav": save_wav(path.parent, mix)}
    if not np.any(mix):
        return {**result, "status": "no_signal_at_encoder_rate"}
    layers = get_model().separate(mix)
    result.update(status="available", layer_rms_dbfs=[level(s) for s in layers])
    if layer is None:
        result["candidate_wavs"] = [save_wav(path.parent, s) for s in layers]
    else:
        result["layer_wav"] = save_wav(path.parent, layers[layer - 1])
    return result


def original_channel(wav, channel, info):
    if info["audio_channels"] == 1:
        return str(wav.resolve())
    if __package__:
        from .common import pcm_codec
    else:
        from common import pcm_codec
    path = wav.with_name(f"{wav.stem}_channel{channel}.wav")
    with tempfile.TemporaryDirectory(dir=wav.parent) as tmp:
        output = Path(tmp) / "channel.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(wav), "-map", "0:a:0",
                        "-af", f"pan=mono|c0=c{channel - 1}", "-map_metadata", "-1", "-c:a", pcm_codec(info),
                        "-rf64", "auto", str(output)], capture_output=True, check=True)
        output.replace(path)
    return str(path.resolve())


class LayerReader(LocalReader):
    def __init__(self):
        super().__init__([PYTHON, str(Path(__file__).resolve()), "--serve"], MODEL)
        self.matcher = SoundMatcher()

    def __exit__(self, *args):
        self.matcher.__exit__(*args)
        super().__exit__(*args)

    def inspect(self, directory, start, end, channel, layer=None, focus=None):
        if (layer is None) == (focus is None) or (focus is not None and not focus.strip()):
            raise ValueError("choose one layer rank or describe a sound to focus on")
        if not 0 <= start < end or end - start > MAX_SECONDS or channel < 1 or (layer is not None and not 1 <= layer <= 8):
            raise ValueError(f"choose a channel, layer 1 to 8 and a film interval of at most {MAX_SECONDS} seconds")
        if __package__:
            from .common import probe, sample_boundary
            from .listen import excerpt
            from .turn_audio import released_samples
        else:
            from common import probe, sample_boundary
            from listen import excerpt
            from turn_audio import released_samples
        if (Path(directory) / "view.json").is_file():
            wav, start, end, first, _ = released_samples(directory, start, end, MAX_SECONDS)
        else:
            wav = excerpt(directory, start, end)
            first = None
        info = probe(wav)
        if channel > info["audio_channels"]:
            raise ValueError(f"choose one of the {info['audio_channels']} recorded channels")
        if first is None:
            first = sample_boundary(start, info["audio_rate"])
        response = self.request({"wav": str(wav.resolve()), "channel": channel, "layer": layer})
        if candidates := response.pop("candidate_wavs", None):
            selection = self.matcher.choose_layer([response["mix_wav"], *candidates], focus)
            response["selection"] = selection
            if selection["status"] == "available":
                rank = selection["selected"]
                response["selected_layer"] = rank
                if rank:
                    response["layer_wav"] = candidates[rank - 1]
        # The matcher needs equal bandwidth for a fair comparison. The client gets the native
        # channel even if separation or matching is unavailable.
        response["mix_wav"] = original_channel(wav, channel, info)
        if response["status"] == "unavailable":
            response["setup"] = "reel setup --layers"
        return {"start_s": start, "end_s": end, "first_sample_on_film": first,
                "recorded_channels": info["audio_channels"], "original_rate_hz": info["audio_rate"], **response}


def describe_layer(sound, result):
    """Read the original channel independently of the separator's guess."""
    if result["status"] == "available":
        for name, key in (("mix", "mix_wav"), ("estimate", "layer_wav")):
            if key not in result:
                continue
            reading = sound.read(result[key], result["start_s"], result["end_s"])
            if reading.get("status") == "available":
                # Both clips cover the same interval and contain a single channel.
                # Return the words first so a client can read them within a small text budget.
                observations = [{k: channel[k] for k in ("impression", "digital_silence", "measured_profile") if k in channel}
                                for window in reading["windows"] for channel in window["channels"]]
                result[name + "_reading"] = {"status": "available", "observations": observations}
                result["waveform_reader"] = reading["model"]
            else:
                result[name + "_reading"] = reading
    return result


def serve():
    model = None

    def get_model():
        nonlocal model
        if model is None:
            model = Separator()
        return model

    for line in sys.stdin:
        try:
            result = listen_layer(get_model, **json.loads(line))
        except Exception as error:
            result = {"status": "unavailable", "model": MODEL, "reason": str(error), "startup_failure": not ready()}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["--serve"]:
        serve()
    elif sys.argv[1:] == ["--check"]:
        Separator().session.close()
