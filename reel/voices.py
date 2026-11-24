"""Session-local voice continuity in speech-active released channels."""
import importlib.util
import json
import os
from pathlib import Path
import sys

import numpy as np

if __package__:
    from .asr import speech_regions
    from .common import open_recording, resample_channels
    from .config import MODELS
    from .local_reader import LocalReader
    from .scene_audio import fetch
else:
    from asr import speech_regions
    from common import open_recording, resample_channels
    from config import MODELS
    from local_reader import LocalReader
    from scene_audio import fetch

MODEL = "Wespeaker/wespeaker-voxceleb-resnet34"
REVISION = "ff1ac5bca8ef11e90662b879aa923979e0bd277b"
WEIGHTS = MODELS / ("wespeaker-" + REVISION[:8]) / "voxceleb_resnet34.onnx"
HASH = "9fea6516d7ad6bf0a76c7689f5a49b65d330fad6dde96c91bb4435ffbfe056a1"
PYTHON = os.environ.get("REEL_VOICE_PYTHON", sys.executable)


def ready():
    return WEIGHTS.is_file() and all(importlib.util.find_spec(m) for m in
                                     ("onnxruntime", "kaldi_native_fbank", "faster_whisper"))


def setup():
    fetch(f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{WEIGHTS.name}", WEIGHTS, HASH)
    VoiceEncoder()


class VoiceEncoder:
    def __init__(self):
        import kaldi_native_fbank as knf
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(WEIGHTS), options, providers=["CPUExecutionProvider"])
        self.options = knf.FbankOptions()
        self.options.frame_opts.dither = 0
        self.options.frame_opts.window_type = "hamming"
        self.options.mel_opts.num_bins = 80

    def __call__(self, speech):
        import kaldi_native_fbank as knf
        bank = knf.OnlineFbank(self.options)
        bank.accept_waveform(16000, (speech * 32768).tolist())
        bank.input_finished()
        features = np.stack([bank.get_frame(i) for i in range(bank.num_frames_ready)])
        inputs = {"feats": (features - features.mean(axis=0))[None]}
        vector = self.session.run(["embs"], inputs)[0][0]
        return vector / max(np.linalg.norm(vector), 1e-8)


class VoiceHistory:
    def __init__(self, encode):
        self.encode = encode
        self.profiles = []
        self.tail = None
        self.next_sample = None
        self.format = None

    def match(self, vector):
        scores = np.array([v @ vector for v in self.profiles])
        order = np.argsort(-scores)
        if not len(scores) or scores[order[0]] < .35:
            if len(scores) == 64:
                return {"status": "unresolved"}
            self.profiles.append(vector)
            return {"status": "new_voice_candidate", "track": f"voice-{len(self.profiles)}"}
        best = int(order[0])
        row = {"cosine": round(float(scores[best]), 4)}
        if scores[best] < .55 or (len(order) > 1 and scores[best]-scores[order[1]] < .08):
            return {**row, "status": "unresolved"}
        return {**row, "status": "similar_voice", "track": f"voice-{best+1}"}

    def read(self, wav, first):
        rows = []
        with open_recording(wav) as recording:
            rate, channels = recording.samplerate, recording.channels
            hop = round(.75 * rate)
            width = 2 * hop
            if self.format != (rate, channels) or self.next_sample != first:
                self.tail = np.empty((0, channels), np.float32)
            self.format = rate, channels
            self.next_sample = first + len(recording)
            origin = first - len(self.tail)
            for block in recording.blocks(blocksize=width, dtype="float32", always_2d=True):
                pcm = np.concatenate((self.tail, block))
                offset = 0
                while offset + width <= len(pcm):
                    window = resample_channels(pcm[offset:offset+width], rate, 16000)
                    seen = {}
                    for i, x in enumerate(window.T, 1):
                        key = x.tobytes()
                        if key in seen:
                            seen[key]['channels'].append(i)
                            continue
                        if not np.any(x):
                            continue
                        spans = speech_regions(x, pad_ms=0)
                        count = sum(b-a for a,b in spans)
                        if count < 12000:
                            continue
                        speech = np.concatenate([x[a:b] for a,b in spans])
                        row = dict(kind="voice", channels=[i], start_s=(origin+offset)/rate,
                                   end_s=(origin+offset+width)/rate, speech_seconds=round(count/16000, 3),
                                   **self.match(self.encode(speech)))
                        seen[key] = row
                        rows.append(row)
                    offset += hop
                origin += offset
                self.tail = pcm[offset:].copy()
        return {"status": "available", "model": MODEL, "windows": rows,
                "basis": "Anonymous voice resemblance over speech-active samples. Overlapping voices may merge. "
                         "Labels belong to this playback. Words require separate timed speech evidence."}


class VoiceReader(LocalReader):
    def __init__(self):
        super().__init__([PYTHON, str(Path(__file__).resolve()), "--serve"], MODEL)


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(0 if ready() else 1)
    if sys.argv[1:] == ["--setup"]:
        setup()
    elif sys.argv[1:] == ["--serve"]:
        history = None
        for line in sys.stdin:
            try:
                if history is None:
                    history = VoiceHistory(VoiceEncoder())
                result = history.read(**json.loads(line))
            except Exception as error:
                result = {"status": "unavailable", "model": MODEL, "reason": str(error), "startup_failure": history is None}
                # Keep established anonymous IDs after an unreadable turn.
            print(json.dumps(result), flush=True)
