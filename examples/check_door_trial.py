"""Check the three completed --until reels produced from make_door_trial.py sources.

Usage: python examples/check_door_trial.py SOURCE_DIR REEL_DIR
Both directories contain A, B and C (A.mkv, etc. for the sources).
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np

from make_door_trial import CUTOFF, FPS, SR


def decode(path, media):
    options = (["-map", "0:v:0", "-pix_fmt", "rgb24", "-f", "rawvideo"] if media == "video"
               else ["-map", "0:a:0", "-ar", str(SR), "-ac", "1", "-f", "f32le"])
    return subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), *options, "-"])


def check(sources, reels):
    # Read original samples and frames, independently of Reel's clipping command.
    for media, count in (("video", int(CUTOFF * FPS) * 640 * 360 * 3),
                         ("audio", int(CUTOFF * SR) * 4)):
        original_a = decode(sources / "A.mkv", media)
        for arm in "ABC":
            original = decode(sources / f"{arm}.mkv", media)
            clipped = decode(reels / arm / "clip.mkv", media)
            assert clipped == original[:count], f"{arm}: {media} prefix differs from source"
            if arm == "B":
                if media == "video":
                    assert original == original_a, "A/B pictures differ"
                else:
                    assert original[:4 * SR * 4] == original_a[:4 * SR * 4]
                    assert original[:count] != original_a[:count]
                    assert original[count:] == original_a[count:]
            if arm == "C":
                assert original[:count] == original_a[:count]
                assert original[count:] != original_a[count:]
        print(f"{media}: all three clips match the decoded originals up to {CUTOFF}s")

    expected = {"pages/page_01.png", "stream.md", "meta.json"}
    for arm in "ABC":
        meta = json.loads((reels / arm / "meta.json").read_text())
        assert meta["dur"] == meta["until"] == CUTOFF and meta["film"] == "clip.mkv"
        assert meta["pages"] == 1
        if "current_frame" in meta:
            current = meta["current_frame"]
            assert current and 0 <= current["pts_us"] < CUTOFF * 1_000_000
            expected.add(current["file"])
    for filename in sorted(expected):
        a = (reels / "A" / filename).read_bytes()
        assert a == (reels / "C" / filename).read_bytes(), f"A/C differ: {filename}"
        print(f"A/C identical: {filename}  {hashlib.sha256(a).hexdigest()}")
    with np.load(reels / "A/features.npz") as a, np.load(reels / "C/features.npz") as c:
        assert set(a.files) == set(c.files)
        for field in a.files:
            np.testing.assert_array_equal(a[field], c[field], err_msg=field)
    print("A/C identical: all saved feature arrays")


def check_speech(reels, control):
    for arm in "AB":
        meta = json.loads((reels / arm / "meta.json").read_text())
        assert meta["speech_activity"] == [] and meta["words"] == [], f"{arm}: unexpected speech"
        stream = (reels / arm / "stream.md").read_text()
        assert "source unclear" in stream and "no speech detected" in stream
        assert ("sharp rise in level" in stream) == (arm == "A")
    meta = json.loads((control / "meta.json").read_text())
    assert meta["speech_activity"], "missed the control utterance"
    words = [word["w"].lower().strip(".,!?") for word in meta["words"]]
    assert words == ["thank", "you"], f"spoken control: {words}"
    assert all(1.5 <= word["t"] <= word["e"] <= 3 for word in meta["words"])
    print("Speech: zero words in A/B, timed 'Thank you' in the spoken control")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sources", type=Path)
    ap.add_argument("reels", type=Path)
    ap.add_argument("--speech-control", type=Path, help="also check A/B and the timed Thank you control reel")
    args = ap.parse_args()
    check(args.sources, args.reels)
    if args.speech_control:
        check_speech(args.reels, args.speech_control)
