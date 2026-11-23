"""Make three lossless sources for a blind first-viewing trial.

Usage: python examples/make_door_trial.py OUT KEY_RECORDING
Use a recording of jangling keys you may share. The pilot uses Joao_Janz's
"Keys Jingling 1_1" (CC0), https://freesound.org/s/485110/.

All pictures match before 8.5 s. The recording starts at 4 s in A and C.
A and B end in the same opening door; C has a different picture and sound
starting exactly at the cutoff. Give readers only the anonymous Reel output.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw
import soundfile as sf

FPS, SR = 24, 48000
DURATION, CUTOFF = 12, 8.5


def room(opening):
    frame = Image.new("RGB", (640, 360), "#dbd4c6")
    d = ImageDraw.Draw(frame)
    d.rectangle((0, 290, 640, 360), fill="#827264")
    d.rectangle((0, 282, 640, 291), fill="#f3e7d4")
    d.rectangle((227, 27, 407, 293), fill="#f6e7d3")
    d.rectangle((236, 36, 398, 290), fill="#343b43")
    edge = 394 - int(130 * opening)
    d.polygon([(239, 38), (edge, 38 + int(14 * opening)),
               (edge, 289 - int(14 * opening)), (239, 289)], fill="#966440")
    for top, bottom in ((56, 151), (174, 265)):
        d.rectangle((246, top, edge - 7, bottom), outline="#62422d", width=2)
    d.ellipse((edge - 19, 159, edge - 9, 169), fill="#e5be65", outline="#46382c")
    return frame


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", type=Path)
    ap.add_argument("recording", type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(args.recording),
                                   "-t", "3", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"])
    keys = np.frombuffer(raw, dtype=np.float32)
    if not keys.size or not np.max(np.abs(keys)):
        ap.error("expected an audible recording")
    keys = 0.32 * keys / np.max(np.abs(keys))
    room_tone = np.random.default_rng(17).normal(0, 0.0015, DURATION * SR)
    with tempfile.TemporaryDirectory() as tmp:
        audio_file = Path(tmp) / "track.wav"
        for condition in ("A", "B", "C"):
            audio = room_tone.copy()
            if condition in ("A", "C"):
                audio[4 * SR:4 * SR + len(keys)] += keys
            if condition == "C":
                tail = audio[int(CUTOFF * SR):]
                tail += 0.2 * np.sin(2 * np.pi * 440 * np.arange(len(tail)) / SR)
            sf.write(audio_file, audio, SR, subtype="PCM_24")
            cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pixel_format", "rgb24",
                   "-video_size", "640x360", "-framerate", str(FPS), "-i", "pipe:0",
                   "-i", str(audio_file), "-c:v", "ffv1", "-c:a", "pcm_s24le", str(args.out / f"{condition}.mkv")]
            with subprocess.Popen(cmd, stdin=subprocess.PIPE) as proc:
                for i in range(DURATION * FPS):
                    t = i / FPS
                    frame = room(np.clip(t - 9, 0, 1))
                    if condition == "C" and t >= CUTOFF:
                        frame = Image.new("RGB", frame.size, "blue")
                    proc.stdin.write(frame.tobytes())
                proc.stdin.close()
                if proc.wait():
                    raise subprocess.CalledProcessError(proc.returncode, cmd)


if __name__ == "__main__":
    main()
