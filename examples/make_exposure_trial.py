"""Move one brief sound across twelve otherwise identical movies.

Usage: python examples/make_exposure_trial.py SOURCE.mp4 OUTPUT --start 2
Give a reader one randomly ordered movie under a neutral name. Keep key.json
and the other answers out of the reader's session. Use --seed for new numbers.
"""
import argparse
import json
import math
from pathlib import Path
import random
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import soundfile as sf


def make(source, directory, start, seed):
    if not math.isfinite(start) or start < 0:
        raise ValueError("start must be a finite nonnegative time in seconds")
    width, height = 320, 180
    count, fps, rate = 12, 15, 24000
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-ss", str(start), "-i", str(source), "-map", "0:v:0",
        "-vf", f"fps={fps},scale={width}:{height}", "-frames:v", str(count), "-f", "rawvideo",
        "-pix_fmt", "rgb24", "pipe:1",
    ])
    if len(raw) != count * width * height * 3:
        raise ValueError("choose a start with at least 0.8 seconds of picture remaining")
    rng = random.Random(seed)
    numbers = rng.sample(range(10, 100), count)
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 36)
    frames = []
    for number, rgb in zip(numbers, np.frombuffer(raw, np.uint8).reshape(count, height, width, 3), strict=True):
        image = Image.fromarray(rgb)
        draw = ImageDraw.Draw(image)
        draw.rectangle((125, 62, 195, 112), fill=(245, 244, 232))
        draw.text((160, 88), str(number), font=font, anchor="mm", fill=(14, 18, 26))
        frames.append(image.tobytes())
    pictures = b"".join(frames)
    tone = .68 * np.sin(2 * np.pi * 1560 * np.arange(240) / rate) * np.hanning(240)
    phases = list(range(count))
    rng.shuffle(phases)
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError("use an empty output directory for a new trial")
    rows = []
    with tempfile.TemporaryDirectory(dir=directory.parent) as tmp:
        staged = Path(tmp) / "clips"
        staged.mkdir()
        for index, frame in enumerate(phases):
            samples = np.zeros(count * rate // fps)
            first = frame * rate // fps + 480
            samples[first:first + len(tone)] = tone
            wav = Path(tmp) / "recording.wav"
            sf.write(wav, samples, rate, subtype="PCM_16")
            film = staged / f"clip_{index + 1:02}.mkv"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-video_size",
                f"{width}x{height}", "-framerate", str(fps), "-i", "pipe:0", "-i", str(wav),
                "-c:v", "ffv1", "-c:a", "pcm_s16le", str(film),
            ], input=pictures, check=True)
            rows.append(dict(file=film.name, frame=frame, answer=numbers[frame],
                             tone_samples=[first, first + len(tone)]))
        (staged / "key.json").write_text(json.dumps(rows, indent=2) + "\n")
        staged.replace(directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--seed", type=int, default=962741)
    args = parser.parse_args()
    make(args.source, args.output, args.start, args.seed)
