"""Shared helpers for Reel: ffmpeg decode, fonts, colour maps, small drawing tools."""
from __future__ import annotations
import functools, json, subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont

MONO = "/System/Library/Fonts/Menlo.ttc"
KOR = "/System/Library/Fonts/AppleSDGothicNeo.ttc"


def run(cmd):
    return subprocess.run([str(c) for c in cmd], check=True, capture_output=True)


def probe(path):
    j = json.loads(run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path]).stdout)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    info = {"path": str(path), "dur": float(j["format"]["duration"]), "has_audio": a is not None, "has_video": v is not None}
    if v:
        n, d = v["avg_frame_rate"].split("/")
        info.update(fps=float(n) / float(d), w=int(v["width"]), h=int(v["height"]))
    return info


def read_frames(path, w, h, fps, ss=None, t=None):
    """Yield RGB frames resampled to a constant fps, so frame i sits at i / fps (+ ss)."""
    cmd = ["ffmpeg", "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{max(ss, 0):.6f}"]
    cmd += ["-i", str(path)]
    if t is not None:
        cmd += ["-t", f"{t:.6f}"]
    cmd += ["-an", "-vf", f"fps={fps},scale={w}:{h}:flags=area", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=w * h * 3 * 16)
    n = w * h * 3
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    p.stdout.close()
    p.wait()


def read_audio(path, sr=48000, ss=None, t=None):
    cmd = ["ffmpeg", "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{max(ss, 0):.6f}"]
    cmd += ["-i", str(path)]
    if t is not None:
        cmd += ["-t", f"{t:.6f}"]
    cmd += ["-vn", "-ac", "2", "-ar", str(sr), "-f", "f32le", "-"]
    raw = run(cmd).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


@functools.lru_cache(None)
def font(size, kor=False):
    return ImageFont.truetype(KOR if kor else MONO, size)


@functools.lru_cache(None)
def lut(name="magma"):
    from matplotlib import colormaps
    return (colormaps[name](np.linspace(0, 1, 256))[:, :3] * 255).astype(np.uint8)


def tc(t):
    m = int(t // 60)
    return f"{m}:{t - 60 * m:05.2f}"


def polyline(d: ImageDraw.ImageDraw, xs, ys, fill, width=1):
    pts = [(float(x), float(y)) for x, y in zip(xs, ys) if np.isfinite(y)]
    if len(pts) >= 2:
        d.line(pts, fill=fill, width=width)


def norm_rows(a, lo, hi):
    return np.clip((a - lo) / max(hi - lo, 1e-9), 0, 1)
