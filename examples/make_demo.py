#!/usr/bin/env python3
"""make_demo.py - build a 70-second demo film with planted editing faults, from nothing copyrighted.

  python examples/make_demo.py examples/out/demo.mp4 [--music path/to/CC-BY-track.wav]

Picture: procedural moving shapes and title cards. Narration: macOS `say`. Music: any track you may use (the README
demo uses Kevin MacLeod's "Crypto", CC BY 4.0). Planted faults, at these times:
  0:13-0:15  the same second of narration plays twice (an edit echo)
  0:27-0:30  a hard cut to black and silence in the middle of a sentence
  0:38-0:41  the picture freezes while the sound goes on
  0:46-0:58  the "interview" subtitles run 2.5 s ahead of the voice
  0:50-0:58  the picture is letterboxed
  story      the year cards run 2019, 2021, 2018, 2022
"""
import argparse, math, os, pathlib, subprocess, tempfile
import numpy as np, soundfile as sf
from PIL import Image, ImageDraw, ImageFont

W, H, FPS, SR = 1280, 720, 30, 48000
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"


def f(size, bold=True):
    return ImageFont.truetype(FONT, size, index=6 if bold else 2)


def say(text, voice="Samantha"):
    tmp = tempfile.mkdtemp()
    aiff = os.path.join(tmp, "s.aiff")
    subprocess.run(["say", "-v", voice, "-o", aiff, text], check=True)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", aiff, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def scene(t, seed, kind):
    """A moving abstract 'footage' frame."""
    r = np.random.default_rng(seed)
    img = Image.new("RGB", (W, H), tuple(int(x) for x in r.integers(10, 60, 3)))
    d = ImageDraw.Draw(img)
    for k in range(14):
        cx = (r.uniform(0, W) + t * r.uniform(-140, 140)) % W
        cy = (r.uniform(0, H) + t * r.uniform(-60, 60)) % H
        rad = r.uniform(30, 140)
        col = tuple(int(x) for x in r.integers(60, 255, 3))
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col)
    if kind == "interview":
        d.rectangle([W * 0.36, H * 0.22, W * 0.64, H * 0.95], fill=(40, 40, 46))
        d.ellipse([W * 0.44, H * 0.18, W * 0.56, H * 0.40], fill=(220, 190, 160))
    return img


def card(lines, sizes):
    img = Image.new("RGB", (W, H), (8, 8, 10))
    d = ImageDraw.Draw(img)
    y = H / 2 - sum(sizes) / 2
    for t_, s in zip(lines, sizes):
        ff = f(s)
        d.text(((W - ff.getlength(t_)) / 2, y), t_, font=ff, fill=(240, 240, 240)); y += s + 14
    return img


def subtitle(img, text):
    d = ImageDraw.Draw(img)
    ff = f(34)
    x = (W - ff.getlength(text)) / 2
    d.text((x, H - 90), text, font=ff, fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))
    return img


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("--music")
    a = ap.parse_args()
    dur = 70.0
    n = int(dur * FPS)
    audio = np.zeros(int(dur * SR), np.float32)
    voice = np.zeros_like(audio)

    def put(y, t):
        i = int(t * SR); m = min(len(y), len(voice) - i)
        voice[i:i + m] += y[:m]

    # narration and the planted echo
    l1 = say("Every season, the same question came back. Could this team finally win it all?")
    put(l1, 4.0)
    l2 = say("They had come close before, in front of a home crowd, and fallen short.")
    put(l2, 11.0)
    s0 = int(1.6 * SR)                                   # the edit echo: 1 s of l2 is played twice
    rep = l2[s0:s0 + SR].copy()
    shifted = np.concatenate([l2[:s0 + SR], rep, l2[s0 + SR:]])
    voice[int(11.0 * SR):int(11.0 * SR) + len(l2)] = 0
    put(shifted, 11.0)
    l3 = say("Then came the final, and the arena went quiet as the last fight began.")
    put(l3, 21.0)
    l4 = say("Nobody expected the comeback that followed.")
    put(l4, 31.5)
    l5 = say("I think we played our own game. We stayed calm, and we trusted each other.", "Daniel")
    put(l5, 47.0)
    l6 = say("Next year, we come back stronger.", "Daniel")
    put(l6, 54.5)

    if a.music and pathlib.Path(a.music).exists():
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", a.music, "-t", str(dur), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                             capture_output=True).stdout
        mus = np.frombuffer(raw, np.float32)
        audio[:len(mus)] += mus[:len(audio)] * 0.25
    audio += voice * 0.9
    audio[int(27.0 * SR):int(30.0 * SR)] = 0              # planted: hard cut to silence
    tmp = tempfile.mkdtemp()
    sf.write(os.path.join(tmp, "a.wav"), np.stack([audio, audio], 1), SR)

    cards = {0: (["2019"], [96]), 8: (["2021"], [96]), 18: (["2018"], [96]), 43: (["2022", "Interview"], [96, 40]), 60: (["Next season"], [64])}
    subs_early = [(44.5, 50.5, "I think we played our own game."), (50.5, 54.0, "We stayed calm, and we trusted each other."),
                  (52.0, 55.5, "Next year, we come back stronger.")]
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                          "-i", "-", "-i", os.path.join(tmp, "a.wav"), "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                          "-c:a", "aac", "-b:a", "192k", "-shortest", a.out], stdin=subprocess.PIPE)
    frozen = None
    for i in range(n):
        t = i / FPS
        key = max([k for k in cards if k <= t], default=0)
        if key <= t < key + 2.5:
            img = card(*cards[key])
        elif 27.0 <= t < 30.0:
            img = Image.new("RGB", (W, H), (0, 0, 0))       # planted: blackout
        elif 38.0 <= t < 41.0:
            if frozen is None:
                frozen = scene(t, 3, "match")
            img = frozen                                    # planted: freeze
        elif t >= 43:
            img = scene(t, 7, "interview")
            for a0, a1, s in subs_early:                    # planted: subtitles 2.5 s early
                if a0 <= t < a1:
                    img = subtitle(img, s)
                    break
            if 50 <= t < 58:                                # planted: letterbox
                arr = np.asarray(img).copy(); arr[:int(H * 0.14)] = 0; arr[-int(H * 0.14):] = 0
                img = Image.fromarray(arr)
        else:
            img = scene(t, int(t // 6), "match")
        p.stdin.write(np.asarray(img.convert("RGB")).tobytes())
    p.stdin.close(); p.wait()
    print("wrote", a.out)


if __name__ == "__main__":
    main()
