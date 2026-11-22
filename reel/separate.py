#!/usr/bin/env python3
"""Split a finished mix into stems so the rig can read what is under the music.

    python separate.py MIX --out DIR [--ss S --t D] [--crowd] [--model htdemucs]

Writes vocals.wav, drums.wav, bass.wav, other.wav and no_music.wav (mix minus drums, bass and other: voices and
whatever the model did not claim as an instrument). With --crowd it also writes crowd.wav and no_crowd.wav from a
model trained to pull an audience out of music; that is the stem to read for chants. All files are 44.1 kHz stereo and line up
sample for sample with the decoded span, so a time read off a stem is a time in the mix (add --ss).
Point REEL_SEP_PYTHON / REEL_CROWD_PYTHON at other environments if the separators need their own.
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

SR = 44100
STEMS = ("drums", "bass", "other", "vocals")


def decode(path, ss, t):
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if ss:
        cmd += ["-ss", f"{ss}"]
    if t:
        cmd += ["-t", f"{t}"]
    cmd += ["-i", str(path), "-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    if not raw:
        sys.exit(f"no audio decoded from {path}")
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).T.copy()


def rms_db(x):
    return float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12))


def crowd_line(y, sr, mix_db, ss):
    """Level of the crowd stem over time: is there crowd, and where is its loudest sustained run."""
    hop, win = int(0.25 * sr), int(0.5 * sr)
    n = max((len(y) - win) // hop + 1, 1)
    env = np.array([rms_db(y[i * hop:i * hop + win]) for i in range(n)])
    p90 = float(np.percentile(env, 90))
    on = env >= p90 - 15
    best, i = (0, 0), 0
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            if j - i > best[1] - best[0]:
                best = (i, j)
            i = j
        else:
            i += 1
    a, b = best[0] * hop / sr, min((best[1] - 1) * hop / sr + 0.5, len(y) / sr)
    under = p90 - mix_db
    present = under > -38 and (b - a) >= 4
    line = (f"crowd stem: p90 {p90:.1f} dBFS, {abs(under):.0f} dB under the mix; loudest sustained run "
            f"{a + ss:.1f} to {b + ss:.1f} s -> " + ("CROWD PRESENT, look at a card of crowd.wav for chant arcs" if present
                                                  else "no sustained crowd"))
    return {"p90_dbfs": round(p90, 1), "db_under_mix": round(under, 1), "run": [round(a + ss, 2), round(b + ss, 2)],
            "present": bool(present), "line": line}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mix")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ss", type=float, default=0.0)
    ap.add_argument("--t", type=float, default=0.0)
    ap.add_argument("--model", default="htdemucs", help="htdemucs (fast) or htdemucs_ft (4x slower, a little cleaner)")
    ap.add_argument("--shifts", type=int, default=1, help="average this many time-shifted passes (slower, steadier)")
    ap.add_argument("--device", default="auto", help="auto, mps or cpu")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--crowd", action="store_true",
                    help="also run the crowd model (crowd.py): writes crowd.wav, no_crowd.wav, mix.wav")
    a = ap.parse_args()

    torch.set_num_threads(a.threads)
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    wav = decode(a.mix, a.ss, a.t)
    dur = wav.shape[1] / SR

    dev = a.device
    if dev == "auto":
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
    model = get_model(a.model)
    model.eval()
    order = list(model.sources)

    x = torch.from_numpy(wav)
    ref = x.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    t0 = time.time()
    with torch.no_grad():
        try:
            est = apply_model(model, ((x - mean) / std)[None], device=dev, shifts=a.shifts, split=True, overlap=0.25)
        except Exception as e:  # mps can refuse an op; cpu always works
            if dev == "cpu":
                raise
            print(f"{dev} failed ({type(e).__name__}); retrying on cpu", file=sys.stderr)
            dev = "cpu"
            est = apply_model(model, ((x - mean) / std)[None], device=dev, shifts=a.shifts, split=True, overlap=0.25)
    est = (est[0].cpu() * std + mean).numpy()
    took = time.time() - t0

    stems = {name: est[order.index(name)] for name in STEMS}
    music = stems["drums"] + stems["bass"] + stems["other"]
    stems["no_music"] = wav - music
    report = {"mix": str(a.mix), "ss": a.ss, "seconds": round(dur, 3), "model": a.model, "device": dev,
              "separate_seconds": round(took, 2), "seconds_per_minute": round(took / dur * 60, 2),
              "mix_rms_db": round(rms_db(wav), 1), "stems_rms_db": {}}
    for name, y in stems.items():
        sf.write(out / f"{name}.wav", np.clip(y.T, -1.0, 1.0), SR, subtype="PCM_16")
        report["stems_rms_db"][name] = round(rms_db(y), 1)
    levels = "  ".join(f"{k} {v:+.1f}" for k, v in report["stems_rms_db"].items())
    print(f"{out}: {dur:.1f} s in {took:.1f} s on {dev} ({report['seconds_per_minute']} s per minute)")
    print(f"  mix {report['mix_rms_db']:+.1f} dB rms | {levels}")
    if a.crowd:
        here = Path(__file__).resolve().parent
        from config import CROWD_PY
        py = _pl.Path(CROWD_PY)
        if not py.exists():
            print("  --crowd skipped: the crowd model is not installed (see README, Install)", file=sys.stderr)
        else:
            sf.write(out / "mix.wav", np.clip(wav.T, -1.0, 1.0), SR, subtype="PCM_16")
            subprocess.run([str(py), str(here / "crowd.py"), str(out / "mix.wav"), "--out", str(out)], check=True)
            c, csr = sf.read(out / "crowd.wav", dtype="float32", always_2d=True)
            report["crowd"] = crowd_line(c.mean(1), csr, report["mix_rms_db"], a.ss)
            print("  " + report["crowd"]["line"])
    (out / "split.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
