"""Local AudioSet estimates for playback, the timeline and bounded listening."""
if __package__:
    from .config import MODELS
else:
    from config import MODELS

AST_NAME = "MIT/ast-finetuned-audioset-10-10-0.4593"
SOURCE_SCORE = 0.5


def recorded_context(tagger, samples, rate, end):
    """Classify complete, nonoverlapping listening windows at recorded level."""
    import numpy as np

    width = 10 * rate
    windows = []
    for start in range(0, len(samples), width):
        segment = samples[start:start + width]
        if len(segment) <= rate:
            continue  # The ordinary one-second track already covers this interval.
        channels = [ch for ch in segment.T if np.any(ch)]
        if not channels:
            continue
        # Combining scores preserves sounds that would cancel in a waveform downmix.
        scores = tagger.scores(channels).mean(axis=0)
        names = [{"sound": tagger.labels[i], "score": round(float(scores[i]), 4)}
                 for i in np.argsort(-scores) if scores[i] >= SOURCE_SCORE and tagger.labels[i] != "Silence"]
        if names:
            windows.append({"start_s": start / rate, "end_s": min(end, (start + len(segment)) / rate),
                            "candidates": names[:3]})
    return windows


class AudioTagger:
    def __init__(self, *, device=None, local_only=False):
        import torch
        from transformers import ASTFeatureExtractor, ASTForAudioClassification

        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        from huggingface_hub import try_to_load_from_cache
        cache = str(MODELS / "hub")
        for location in (cache, None):
            if any(isinstance(try_to_load_from_cache(AST_NAME, f, cache_dir=location), str)
                   for f in ("model.safetensors", "pytorch_model.bin")):
                cache = location
                break
        options = {"cache_dir": cache, "local_files_only": local_only}
        self.extractor = ASTFeatureExtractor.from_pretrained(AST_NAME, **options)
        self.model = ASTForAudioClassification.from_pretrained(AST_NAME, **options).eval().to(self.device)
        self.labels = [self.model.config.id2label[i] for i in range(self.model.config.num_labels)]

    def scores(self, clips):
        import torch

        batch = self.extractor(clips, sampling_rate=16000, return_tensors="pt")
        with torch.inference_mode():
            logits = self.model(batch.input_values.to(self.device)).logits
            return torch.sigmoid(logits).cpu().numpy()


class PlaybackSounds:
    """Classify a short trailing recording without losing context at a playback pause."""

    def __init__(self, tagger):
        self.tagger = tagger
        self.tail = None
        self.next_sample = None
        self.rate = None

    def read(self, wav, first_sample, end_s):
        import math
        import numpy as np
        from scipy.signal import resample_poly
        if __package__:
            from .common import open_recording
        else:
            from common import open_recording

        windows = []
        with open_recording(wav) as recording:
            rate = recording.samplerate
            width = 4 * rate
            if (self.next_sample != first_sample or self.rate != rate or
                    self.tail is None or self.tail.shape[1] != recording.channels):
                self.tail = np.empty((0, recording.channels), dtype=np.float64)
            self.rate = rate
            cursor = first_sample
            while recording.tell() < len(recording):
                block = recording.read(width, dtype="float64", always_2d=True)
                keep = width - len(block)
                context = np.concatenate((self.tail[-keep:], block)) if keep and len(self.tail) else block
                end = cursor + len(block)
                gcd = math.gcd(16000, rate)
                signals = context.astype(np.float32)
                if rate != 16000:
                    signals = resample_poly(signals, 16000 // gcd, rate // gcd, axis=0)
                channels = []
                unique = []
                indices = []
                for i, signal in enumerate(signals.T):
                    present = bool(np.any(block[:, i]))
                    channels.append({"channel": i + 1, "signal": "nonzero" if present else "digital_silence",
                                     "candidates": []})
                    # AST needs at least one 25 ms analysis frame. Previous sounds alone
                    # cannot identify a channel that is digitally silent in this block.
                    if present and len(signal) >= 400 and np.any(signal):
                        index = next((j for j, other in enumerate(unique) if np.array_equal(signal, other)), None)
                        if index is None:
                            index = len(unique)
                            unique.append(signal)
                        indices.append((i, index))
                if unique:
                    scores = self.tagger.scores(unique)
                    for channel, index in indices:
                        row = scores[index]
                        channels[channel]["candidates"] = [
                            {"sound": self.tagger.labels[j], "score": round(float(row[j]), 4)}
                            for j in np.argsort(-row) if row[j] >= SOURCE_SCORE and self.tagger.labels[j] != "Silence"
                        ][:3]
                windows.append({"start_s": cursor / rate, "end_s": min(end_s, end / rate),
                                "context_start_s": (end - len(context)) / rate, "channels": channels})
                self.tail = context
                cursor = end
            self.next_sample = cursor
        return {"status": "available", "model": AST_NAME, "basis": "Signal is measured on this block. Possible "
                "sources use up to four seconds of preceding and current recorded sound on the same channel. "
                "Scores are uncalibrated. An empty list leaves the source open. Use speech for words.",
                "windows": windows}


def serve():
    from contextlib import redirect_stdout
    import json
    import sys

    reader = None
    for line in sys.stdin:
        try:
            with redirect_stdout(sys.stderr):
                if reader is None:
                    reader = PlaybackSounds(AudioTagger(local_only=True))
                job = json.loads(line)
                result = reader.read(**job)
        except Exception as error:
            result = {"status": "unavailable", "model": AST_NAME,
                      "reason": "local AudioSet model or dependencies missing" if reader is None else str(error),
                      "startup_failure": reader is None}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    for option in ("--setup", "--check", "--serve"):
        mode.add_argument(option, action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve()
    else:
        AudioTagger(device="cpu", local_only=args.check)
