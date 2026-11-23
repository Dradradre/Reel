"""Compare sound descriptions with recorded audio using the local CLAP model."""
import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

import numpy as np

if __package__:
    from .common import audio_blocks, open_recording, probe, read_audio, resample_channels, sample_boundary
    from .config import COMPARE_PY, MODELS
    from .local_reader import LocalReader
    from .sounds import AST_NAME
else:
    from common import audio_blocks, open_recording, probe, read_audio, resample_channels, sample_boundary
    from config import COMPARE_PY, MODELS
    from local_reader import LocalReader
    from sounds import AST_NAME

CLAP_NAME = "laion/clap-htsat-unfused"
RATE = 48000
WINDOW_SECONDS = 10


def cache_options(repo, download=False):
    from huggingface_hub import try_to_load_from_cache
    for cache in (str(MODELS / "hub"), None):
        if isinstance(try_to_load_from_cache(repo, "config.json", cache_dir=cache), str):
            return {"cache_dir": cache, "local_files_only": not download}
    return {"cache_dir": str(MODELS / "hub"), "local_files_only": not download}


def vocabulary(download=False):
    from transformers import AutoConfig
    config = AutoConfig.from_pretrained(AST_NAME, **cache_options(AST_NAME, download))
    return [config.id2label[i] for i in range(config.num_labels)]


def native_windows(wav, start, end, first_sample, seconds):
    with open_recording(wav) as recording:
        rate = recording.samplerate
        origin = sample_boundary(start, rate) if first_sample is None else first_sample
        offset = 0
        for pcm in recording.blocks(blocksize=seconds * rate, dtype="float32", always_2d=True):
            stop = offset + len(pcm)
            stamp = {"start_s": min(end, (origin + offset) / rate),
                     "end_s": min(end, (origin + stop) / rate), "wav_samples": [offset, stop]}
            yield stamp, resample_channels(pcm, rate, RATE)
            offset = stop


def match_windows(wav, start, end, per_second, first_sample):
    if per_second:
        yield from native_windows(wav, start, end, first_sample, 1)
        return
    rate = probe(wav)["audio_rate"]
    origin = sample_boundary(start, rate) / rate
    for index, block in enumerate(audio_blocks(wav, RATE, 10 * RATE)):
        left = origin + index * 10
        yield {"start_s": min(end, left), "end_s": min(end, left + len(block) / RATE)}, block


class Comparator:
    def __init__(self, download=False):
        import torch
        from transformers import ClapModel, ClapProcessor
        options = cache_options(CLAP_NAME, download)
        self.references = None
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.proc = ClapProcessor.from_pretrained(CLAP_NAME, **options)
        self.model = ClapModel.from_pretrained(CLAP_NAME, **options).eval().to(self.device)

    @staticmethod
    def unit(output):
        import torch
        vector = output if torch.is_tensor(output) else output.pooler_output
        return torch.nn.functional.normalize(vector, dim=-1)

    def text_features(self, phrases):
        import torch
        parts = []
        with torch.inference_mode():
            for start in range(0, len(phrases), 32):
                tokens = self.proc.tokenizer(phrases[start:start + 32], padding=True, truncation=True,
                                             return_tensors="pt").to(self.device)
                parts.append(self.unit(self.model.get_text_features(**tokens)))
        return torch.cat(parts)

    def audio_features(self, channels, padding):
        import torch
        with torch.inference_mode():
            batch = self.proc.feature_extractor(channels, sampling_rate=RATE, padding=padding,
                                                return_tensors="pt").to(self.device)
            return self.unit(self.model.get_audio_features(**batch))

    def similarities(self, channels, text, *, padding="repeatpad"):
        return (self.audio_features(channels, padding) @ text.T).cpu().numpy()

    def compare_recordings(self, reference, target):
        """Compare original channel waveforms independently of any words or pictures."""
        vectors = []
        for wav in (reference, target):
            with open_recording(wav) as recording:
                signal = resample_channels(recording.read(dtype="float32", always_2d=True),
                                           recording.samplerate, RATE)
            channels = []
            for channel in signal.T:
                vector = None
                if np.any(channel):
                    vector = self.audio_features([channel], "pad")[0].cpu().numpy()
                channels.append(vector)
            vectors.append(channels)
        reference_channels, target_channels = vectors
        return {"status": "available", "model": CLAP_NAME,
                "cosines": [[round(float(np.clip(left @ right, -1, 1)), 4)
                             if left is not None and right is not None else None
                             for right in target_channels] for left in reference_channels],
                "basis": "Rows follow the reference channels and columns the target channels in recording order. "
                         "Each encoder input contains one channel of one excerpt at its recorded level, played "
                         "once with zero padding. Null means no signal at the encoder rate. A higher cosine "
                         "indicates more similar whole-clip audio features. Background sound can contribute. "
                         "The score does not identify an object, speaker or word."}

    def choose_layer(self, wavs, focus):
        signals = [read_audio(wav, RATE, channels=1)[:, 0] for wav in wavs]
        active = [i for i, signal in enumerate(signals) if np.any(signal)]
        if not active:
            return {"status": "no_signal_at_encoder_rate"}
        scores = self.similarities([signals[i] for i in active], self.text_features([focus]))[:, 0]
        cosines = [None] * len(wavs)
        for i, score in zip(active, scores, strict=True):
            cosines[i] = round(float(score), 4)
        return {"status": "available", "model": CLAP_NAME, "focus": focus,
                "selected": active[int(np.argmax(scores))], "cosines_mix_then_layers": cosines,
                "basis": "Closest audio to the supplied description among the mix (0) and its estimates. "
                         "The requested sound can be absent from every recording."}

    def match(self, wav, start, end, descriptions, per_second=False, first_sample=None):
        import torch
        if self.references is None:
            labels = vocabulary()
            self.references = labels, self.text_features(["the sound of " + label.lower() for label in labels])
        labels, references = self.references
        text = torch.cat([self.text_features(descriptions), references]) if descriptions else references
        def compare(stamp, block, padding):
            ids = [i for i, channel in enumerate(block.T) if np.any(channel)]
            rows = [{"channel": i + 1, "cosines": [], "reference_neighbours": []} for i in range(block.shape[1])]
            if ids:
                values = self.similarities([block[:, i] for i in ids], text, padding=padding)
                for i, scores in zip(ids, values, strict=True):
                    rows[i]["cosines"] = [round(float(v), 4) for v in scores[:len(descriptions)]]
                    background = scores[len(descriptions):]
                    rows[i]["reference_neighbours"] = [
                        {"sound": labels[j], "cosine": round(float(background[j]), 4)}
                        for j in np.argsort(-background)[:3]]
            return {**stamp, "channels": rows}

        windows = [compare(stamp, block, "repeatpad")
                   for stamp, block in match_windows(wav, start, end, per_second, first_sample)]
        basis = ("Audio-only cosine similarities, in the supplied text order. Empty scores mean no signal "
                 "at the encoder rate. Reference neighbours are the closest of 527 independent AudioSet labels "
                 "on the same scale. All suggestions, including the neighbours, can be poor. Use a relative "
                 "match to choose what to inspect. Identifying a source, visible object or word needs "
                 "independent evidence. For windows, the encoder repeats short inputs as many whole times "
                 "as fit in ten seconds and zero-pads the rest. Repetition inside the encoder does not "
                 "establish repetition in the recording.")
        result = {"status": "available", "model": CLAP_NAME, "descriptions": descriptions, "windows": windows}
        if per_second:
            # Take the entire acoustic phrase from native PCM. Joining independently resampled seconds
            # would insert filter edges and can change the number of samples at fractional rate ratios.
            result["continuous_windows"] = [compare(stamp, block, "pad") for stamp, block in
                                            native_windows(wav, start, end, first_sample, WINDOW_SECONDS)]
            basis += (" The one-second windows can locate changes in similarity. In continuous_windows the "
                      "encoder hears up to ten seconds in their original order once, then pads unused input "
                      "with zeros. Only the recorded part has film times. Neither score locates an onset.")
        return {**result, "basis": basis}


class SoundMatcher(LocalReader):
    def __init__(self):
        super().__init__([COMPARE_PY, str(Path(__file__).resolve()), "--serve"], CLAP_NAME)

    def read(self, wav, start, end, descriptions, *, per_second=False, first_sample=None):
        if any(not s.strip() for s in descriptions):
            raise ValueError("each supplied sound description must contain text")
        job = {"wav": str(Path(wav).resolve()), "start": start, "end": end, "descriptions": list(descriptions)}
        if per_second:
            job.update(per_second=True, first_sample=first_sample)
        return self.request(job)

    def compare_recordings(self, reference, target):
        return self.request({"operation": "compare_recordings", "reference": str(Path(reference).resolve()),
                             "target": str(Path(target).resolve())})

    def choose_layer(self, wavs, focus):
        return self.request({"operation": "choose_layer", "wavs": wavs, "focus": focus})

    def inspect(self, out, start, end, descriptions, *, per_second=False):
        if __package__:
            from .listen import excerpt
            from .turn_audio import released_samples
        else:
            from listen import excerpt
            from turn_audio import released_samples
        first = None
        if (Path(out) / "view.json").is_file():
            wav, start, end, first, _ = released_samples(out, start, end, end - start)
        else:
            wav = excerpt(out, start, end)
        return self.read(wav, start, end, descriptions, per_second=per_second, first_sample=first)


def neighbours(wav, windows):
    labels = vocabulary()
    model = Comparator()
    text = model.text_features(["the sound of " + label.lower() for label in labels])
    samples = read_audio(wav, RATE, channels=None)
    result = []
    for start, stop in windows:
        block = samples[round(start * RATE):round(stop * RATE)]
        active = [channel for channel in block.T if np.any(channel)]
        if not active:
            result.append([])
            continue
        scores = model.similarities(active, text).mean(axis=0)
        result.append([{"sound": labels[j], "cosine": round(float(scores[j]), 4)}
                       for j in np.argsort(-scores)[:3]])
    return result


def serve():
    model = None
    for line in sys.stdin:
        try:
            with redirect_stdout(sys.stderr):
                if model is None:
                    model = Comparator()
                job = json.loads(line)
                operation = job.pop("operation", None)
                if operation == "compare_recordings":
                    result = model.compare_recordings(**job)
                elif operation == "choose_layer":
                    result = model.choose_layer(**job)
                else:
                    result = model.match(**job)
        except Exception as error:
            result = {"status": "unavailable", "model": CLAP_NAME, "reason": str(error), "startup_failure": model is None}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    for option in ("--setup", "--check", "--serve"):
        mode.add_argument(option, action="store_true")
    parser.add_argument("wav", type=Path, nargs="?")
    args = parser.parse_args()
    if args.serve:
        serve()
    elif args.setup or args.check:
        vocabulary(args.setup)
        if args.check:
            from huggingface_hub import try_to_load_from_cache
            from transformers import ClapProcessor
            options = cache_options(CLAP_NAME)
            ClapProcessor.from_pretrained(CLAP_NAME, **options)
            if not any(isinstance(try_to_load_from_cache(CLAP_NAME, f, cache_dir=options["cache_dir"]), str)
                       for f in ("model.safetensors", "pytorch_model.bin")):
                sys.exit("local CLAP weights missing")
        else:
            Comparator(download=True)
    else:
        print(json.dumps(neighbours(args.wav, json.load(sys.stdin))))
