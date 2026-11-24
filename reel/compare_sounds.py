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


def nearest_labels(labels, scores):
    return [{"sound": labels[i], "cosine": round(float(scores[i]), 4)} for i in np.argsort(-scores)[:3]]


class Comparator:
    def __init__(self, download=False, *, device=None):
        import torch
        from transformers import ClapModel, ClapProcessor
        options = cache_options(CLAP_NAME, download)
        self.references = None
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
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

    def reference_labels(self):
        if self.references is None:
            labels = vocabulary()
            self.references = labels, self.text_features(["the sound of " + label.lower() for label in labels])
        return self.references

    def compare_recordings(self, reference, target, alternatives=None):
        """Encode the unaltered channels once, then compare recordings and any supplied descriptions."""
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
        report = {
            "status": "available", "model": CLAP_NAME,
            "cosines": [[round(float(np.clip(left @ right, -1, 1)), 4)
                         if left is not None and right is not None else None
                         for right in target_channels] for left in reference_channels],
            "basis": ("Rows follow the reference channels and columns the target channels in recording order. "
                      "Each encoder input contains one channel of one excerpt at its recorded level, played "
                      "once with zero padding. Null means no signal at the encoder rate. A higher cosine "
                      "indicates more similar whole-clip audio features. Background sound can contribute. "
                      "The score does not identify an object, speaker or word."),
        }
        if alternatives is not None:
            report["description_comparison"] = self.compare_descriptions(vectors, alternatives)
        return report

    def compare_descriptions(self, vectors, alternatives):
        reference, target = vectors
        active_recordings = [any(ch is not None for ch in channels) for channels in vectors]
        if not all(active_recordings):
            basis = "A change in sound identity cannot be measured without two active recordings. "
            basis += ("Replay the active interval separately to identify what you heard." if any(active_recordings)
                      else "Neither interval has a signal at the encoder rate.")
            return {"descriptions": alternatives, "status": "no_signal_at_encoder_rate_in_one_or_both",
                    "target_minus_reference": None, "basis": basis}
        if len(reference) == len(target) and all(np.array_equal(a, b) for a, b in zip(reference, target, strict=True)):
            return {"descriptions": alternatives, "status": "identical_encoder_features",
                    "target_minus_reference": [0.0] * len(alternatives),
                    "basis": "All corresponding channels have identical encoder features. This comparison "
                             "has no change to attribute to a sound. Each excerpt can be replayed for identification."}
        text = self.text_features(alternatives).cpu().numpy()
        comparison = {"descriptions": alternatives}
        means = []
        for name, channels in zip(("reference", "target"), vectors, strict=True):
            scores = [None if ch is None else np.clip(ch @ text.T, -1, 1) for ch in channels]
            comparison[name] = [None if row is None else [round(float(v), 4) for v in row] for row in scores]
            active = [row for row in scores if row is not None]
            means.append(np.mean(active, axis=0) if active else None)
        comparison["target_minus_reference"] = (
            [round(float(v), 4) for v in means[1] - means[0]] if all(v is not None for v in means) else None)
        comparison["basis"] = ("Each row is one recorded channel. Columns follow the supplied descriptions. "
                               "The change subtracts the reference from the target after averaging the active "
                               "channels of each recording. A positive change means greater similarity to that "
                               "description in the target. Either sound may occur twice or in neither recording.")
        try:
            labels, references = self.reference_labels()
        except OSError:
            comparison["catalogue"] = {"status": "unavailable", "reason": "AudioSet label vocabulary unavailable"}
        else:
            catalogue = {"status": "available"}
            label_vectors = references.cpu().numpy()
            for name, channels in zip(("reference", "target"), vectors, strict=True):
                neighbours = []
                for ch in channels:
                    if ch is None:
                        neighbours.append([])
                        continue
                    neighbours.append(nearest_labels(labels, ch @ label_vectors.T))
                catalogue[name] = neighbours
            comparison["catalogue"] = catalogue
            comparison["basis"] += (" The catalogue lists the closest AudioSet terms on the same recording and "
                                    "encoder, independently of your descriptions. These scores are uncalibrated. "
                                    "Use them to check whether either description fits what you heard.")
        return comparison

    def choose_by_example(self, wavs, reference):
        ref = read_audio(reference, RATE, channels=1)[:, 0]
        if not np.any(ref):
            return {"status": "silent_reference", "selected": None}
        sample = self.audio_features([ref], "pad")[0]
        scores = []
        for wav in wavs:
            signal = read_audio(wav, RATE, channels=1)[:, 0]
            score = float((self.audio_features([signal], "pad")[0] @ sample).item()) if np.any(signal) else None
            scores.append(score)
        active = [i for i, s in enumerate(scores) if s is not None]
        if not active:
            return {"status": "no_signal_at_encoder_rate", "selected": None}
        ordered = sorted(active, key=lambda i: -scores[i])
        tied = len(ordered) > 1 and scores[ordered[0]] - scores[ordered[1]] < 1e-5
        return {"status": "available", "selected": 0 if tied else ordered[0], "tied": tied, "model": CLAP_NAME,
                "cosines_mix_then_layers": scores, "basis": "Closest waveform embedding to the earlier recorded "
                "channel. A different event can sound similar and the reference source can be absent."}

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
        labels, references = self.reference_labels()
        text = torch.cat([self.text_features(descriptions), references]) if descriptions else references
        def compare(stamp, block, padding):
            ids = [i for i, channel in enumerate(block.T) if np.any(channel)]
            rows = [{"channel": i + 1, "cosines": [], "reference_neighbours": []} for i in range(block.shape[1])]
            if ids:
                values = self.similarities([block[:, i] for i in ids], text, padding=padding)
                for i, scores in zip(ids, values, strict=True):
                    rows[i]["cosines"] = [round(float(v), 4) for v in scores[:len(descriptions)]]
                    background = scores[len(descriptions):]
                    rows[i]["reference_neighbours"] = nearest_labels(labels, background)
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

    def compare_recordings(self, reference, target, alternatives=None):
        job = {"operation": "compare_recordings", "reference": str(Path(reference).resolve()),
               "target": str(Path(target).resolve())}
        if alternatives is not None:
            if (not isinstance(alternatives, (list, tuple)) or not alternatives
                    or any(not isinstance(s, str) or not s.strip() for s in alternatives)):
                raise ValueError("supply one or more nonempty sound descriptions")
            job["alternatives"] = list(alternatives)
        return self.request(job)

    def choose_by_example(self, wavs, reference):
        return self.request({"operation": "choose_by_example", "wavs": wavs, "reference": str(reference)})

    def choose_layer(self, wavs, focus):
        return self.request({"operation": "choose_layer", "wavs": wavs, "focus": focus})

    def inspect(self, out, start, end, descriptions, *, per_second=False, identity_reader=None):
        if __package__:
            from .identify_sound import IdentityReader, prepared_for
        else:
            from identify_sound import IdentityReader, prepared_for
        wav, start, end, first = match_excerpt(out, start, end)
        report = self.read(wav, start, end, descriptions, per_second=per_second, first_sample=first)
        if not per_second and prepared_for(descriptions):
            with (identity_reader.for_replay() if identity_reader is not None else IdentityReader()) as identity:
                report["sound_identity"] = identity.read(wav, start, end, first, descriptions)
        return report


def match_excerpt(out, start, end):
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
    return wav, start, end, first


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
                elif operation == "choose_by_example":
                    result = model.choose_by_example(**job)
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
