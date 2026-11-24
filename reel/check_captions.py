"""Check retrieved descriptions against the released sound using a second audio encoder."""
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

if __package__:
    from .caption_matches import CSV, ROOT, caption_rows
    from .compare_sounds import RATE, WINDOW_SECONDS, native_windows
    from .identify_sound import HOP_SAMPLES, MODEL, READY, REVISION, PerceptionEncoder
    from .local_reader import LocalReader
    from .localize_sound import PYTHON
else:
    from caption_matches import CSV, ROOT, caption_rows
    from compare_sounds import RATE, WINDOW_SECONDS, native_windows
    from identify_sound import HOP_SAMPLES, MODEL, READY, REVISION, PerceptionEncoder
    from local_reader import LocalReader
    from localize_sound import PYTHON

ANCHORS = 128
MIN_WINS = 125
BASIS = ("PE-AV compared each proposed caption with the same recorded channel. Only captions scoring above "
         "at least 125 of 128 fixed comparison captions are shown. Each comparison caption comes from a "
         "different development recording. This checks the sentence as a whole; individual details can be wrong.")


def reference_panel(captions, groups):
    order = sorted(range(len(captions)), key=lambda i: hashlib.sha256(
        ("reel-contrast-20261125/" + captions[i]).encode()).digest())
    anchors, seen = [], set()
    for i in order:
        if groups[i] not in seen:
            anchors.append(captions[i])
            seen.add(groups[i])
            if len(anchors) == ANCHORS:
                return anchors
    raise ValueError("too few distinct recordings for the caption comparison")


class Verifier:
    def __init__(self, phrases):
        self.encoder = PerceptionEncoder()
        anchors = reference_panel(*caption_rows(ROOT / CSV))
        vocabulary = sorted(set(anchors).union(phrases))
        vectors = self.encoder.encode_captions(vocabulary).cpu().numpy()
        self.vectors = dict(zip(vocabulary, vectors, strict=True))
        self.anchors = np.stack([self.vectors[s] for s in anchors])
        self.encoder.audio_only()

    def check(self, signal, matches):
        audio = self.encoder.audio(signal, RATE)[0].cpu().numpy()
        controls = self.anchors @ audio
        retained = []
        for match in matches:
            score = float(self.vectors[match["caption"]] @ audio)
            wins = int(np.sum(score > controls))
            if wins >= MIN_WINS:
                retained.append({**match, "verification_dot_product": round(score, 5), "above_controls": wins})
        return retained


def verify(wav, start, end, first, windows, make_verifier=Verifier):
    phrases = {m["caption"] for w in windows for c in w["active_channels"] for m in c["matches"]}
    if phrases:
        reader = None
        # Same native sample boundaries, resampling, channel order and recorded level as the retrieval.
        for w, (stamp, pcm) in zip(windows, native_windows(wav, start, end, first, WINDOW_SECONDS), strict=True):
            if stamp["wav_samples"] != w["wav_samples"]:
                raise ValueError("verification samples differ from the retrieval")
            for ch in w["active_channels"]:
                if not ch["matches"]:
                    continue
                proposed = ch["matches"]
                if len(pcm) < HOP_SAMPLES:
                    ch.update(status="insufficient_verification_input", matches=[])
                else:
                    if reader is None:
                        reader = make_verifier(phrases)
                    ch["matches"] = reader.check(pcm[:, ch["channels"][0] - 1], proposed)
                ch["withheld"] = len(proposed) - len(ch["matches"])
    return {"status": "available", "windows": windows,
            "verification": {"status": "available", "model": MODEL, "revision": REVISION, "controls": ANCHORS,
                             "minimum_above_controls": MIN_WINS, "basis": BASIS}}


def checked_report(report, wav, start, end, first):
    if report["status"] != "available":
        return report
    fields = {k: v for k, v in report.items() if k != "windows"}
    if not READY.is_file() or not Path(PYTHON).is_file():
        return {**fields, "status": "not_prepared", "setup": "reel setup --identify-sounds", "windows": []}
    with LocalReader([PYTHON, str(Path(__file__).resolve())], MODEL) as worker:
        result = worker.request(dict(wav=str(Path(wav).resolve()), start=start, end=end, first=first,
                                     windows=report["windows"]))
    if result["status"] != "available":
        return {**fields, "status": "unavailable", "verification": result, "windows": []}
    return {**fields, **result}


if __name__ == "__main__":
    for line in sys.stdin:
        try:
            with redirect_stdout(sys.stderr):
                result = verify(**json.loads(line))
        except Exception as error:
            result = {"status": "unavailable", "model": MODEL, "reason": str(error)}
        print(json.dumps(result), flush=True)
