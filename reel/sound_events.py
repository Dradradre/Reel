"""Listen for brief sound categories in overlapping windows on the native recording clock."""
from pathlib import Path
from zipfile import ZipFile
import argparse
import importlib.util
import json
import sys

import numpy as np

if __package__:
    from .common import open_recording, resample_channels, sample_boundary
    from .config import MODELS
    from .local_reader import LocalReader
else:
    from common import open_recording, resample_channels, sample_boundary
    from config import MODELS
    from local_reader import LocalReader

MODEL = "Google YAMNet"
URL = "https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite"
CHECKSUM = "4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf"
WEIGHTS = MODELS / "yamnet" / "yamnet.tflite"
RATE, WINDOW, STEP = 16000, 15600, 7680
BASIS = ("Possible sounds in the stated recording windows. Windows overlap and can begin in the previous turn. "
         "The times bound the evidence, not the event onset or offset. Scores are uncalibrated. "
         "No candidate leaves the source open. Speech labels supply no words.")


def ready():
    return WEIGHTS.is_file() and importlib.util.find_spec("ai_edge_litert") is not None


def setup():
    if importlib.util.find_spec("ai_edge_litert") is None:
        raise ValueError("install the sound-event runtime with pip install -e '.[events]'")
    if __package__:
        from .scene_audio import fetch
    else:
        from scene_audio import fetch
    fetch(URL, WEIGHTS, CHECKSUM)
    Classifier()


class Classifier:
    def __init__(self):
        from ai_edge_litert.interpreter import Interpreter
        self.model = Interpreter(model_path=str(WEIGHTS), num_threads=1)
        self.model.allocate_tensors()
        inp, = self.model.get_input_details()
        out, = self.model.get_output_details()
        with ZipFile(WEIGHTS) as archive:
            self.labels = archive.read("yamnet_label_list.txt").decode().splitlines()
        if tuple(inp["shape"]) != (WINDOW,) or tuple(out["shape"]) != (1, len(self.labels)):
            raise ValueError("unexpected YAMNet waveform or class dimensions")
        self.input, self.output = inp["index"], out["index"]

    def __call__(self, signal, rate):
        signal = resample_channels(signal, rate, RATE)
        # Rounding the two bounds to native samples can move the length by one
        # native sample. Never borrow a recording sample outside those bounds.
        if len(signal) < WINDOW:
            signal = np.pad(signal, (0, WINDOW - len(signal)))
        self.model.set_tensor(self.input, np.ascontiguousarray(signal[:WINDOW], dtype=np.float32))
        self.model.invoke()
        scores = self.model.get_tensor(self.output)[0]
        return [(self.labels[k], round(float(scores[k]), 4)) for k in np.argsort(-scores)[:3]
                if scores[k] >= .5 and self.labels[k] != "Silence"]


def active_channels(window):
    """Identical waveforms share a reading, silent channels have no label."""
    groups = []
    for number, channel in enumerate(window.T, 1):
        if not np.any(channel):
            continue
        for indices, signal in groups:
            if np.array_equal(signal, channel):
                indices.append(number)
                break
        else:
            groups.append(([number], channel))
    return groups


class EventHistory:
    def __init__(self, classify):
        self.classify = classify
        self.tail = None
        self.next_sample = None
        self.rate = None

    def boundary(self, model_sample):
        return self.origin + (model_sample * self.rate + RATE - 1) // RATE

    def read(self, wav, first_sample, end_s):
        result = []
        previous = {}
        evaluated = 0
        with open_recording(wav) as recording:
            rate, channels = recording.samplerate, recording.channels
            if (self.next_sample != first_sample or self.rate != rate or self.tail is None
                    or self.tail.shape[1] != channels):
                self.origin, self.offset, self.rate = first_sample, 0, rate
                self.tail = np.empty((0, channels), np.float32)
                self.last_end = None
            available = min(len(recording), max(0, sample_boundary(end_s, rate) - first_sample))
            while recording.tell() < available:
                start = self.boundary(self.offset)
                block = recording.read(min(rate, available - recording.tell()), dtype="float32", always_2d=True)
                data = np.concatenate((self.tail, block))
                left = start
                right = self.boundary(self.offset + WINDOW)
                while right <= start + len(data):
                    window = data[left - start:right - start]
                    current = {}
                    for group, signal in active_channels(window):
                        for sound, score in self.classify(signal, rate):
                            key = (tuple(group), sound)
                            row = previous.get(key)
                            if row is None:
                                row = dict(channels=group, sound=sound, start_s=left / rate,
                                           windows=0, peak_score=score)
                                result.append(row)
                            row.update(end_s=min(end_s, right / rate), windows=row["windows"] + 1,
                                       peak_score=max(row["peak_score"], score))
                            current[key] = row
                    previous = current
                    evaluated += 1
                    self.last_end = right
                    self.offset += STEP
                    left, right = self.boundary(self.offset), self.boundary(self.offset + WINDOW)
                self.tail = data[left - start:].copy()
            self.next_sample = first_sample + available
        return dict(status="available", model=MODEL, basis=BASIS, windows_evaluated=evaluated,
                    through_s=min(end_s, self.last_end / rate) if self.last_end is not None else None,
                    candidates=result)


class EventReader(LocalReader):
    def __init__(self):
        super().__init__([sys.executable, str(Path(__file__).resolve()), "--serve"], MODEL)

    def read(self, wav, first_sample, end_s):
        return self.request(dict(wav=str(Path(wav).resolve()), first_sample=first_sample, end_s=end_s))


def inspect_events(wav, first_sample, end_s):
    if not ready():
        return {"status": "not_prepared", "model": MODEL, "setup": "reel setup --sound-events"}
    with EventReader() as reader:
        return reader.read(wav, first_sample, end_s)


def serve():
    history = None
    for line in sys.stdin:
        try:
            if history is None:
                history = EventHistory(Classifier())
            result = history.read(**json.loads(line))
        except Exception as error:
            result = {"status": "unavailable", "model": MODEL, "reason": str(error), "startup_failure": history is None}
            history = None  # A half-read interval cannot provide history for the next request.
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    for option in ("--setup", "--check", "--serve"):
        group.add_argument(option, action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve()
    elif args.setup:
        setup()
    else:
        Classifier()
