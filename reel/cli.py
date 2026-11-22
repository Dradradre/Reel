#!/usr/bin/env python3
"""reel - turn a film into a reel a reader can watch.

  reel watch FILM --out DIR [--subject NAME] [--genre lol] [--lang ko] [--page 30] [--title T]
  reel zoom DIR --t0 S --t1 S
  reel doctor            which senses are available on this machine
  reel setup [--sync]    fetch the gaze model (UNISAL); with --sync also the picture-sound sync model (1.1 GB)
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import argparse, importlib, pathlib, shutil, subprocess, sys
from config import HOME, UNISAL, MODELS, GENRES, SEP_PY, CROWD_PY, ASR_PY, GAZE_PY, SYNC_PY, SYNCHFORMER, SYNC_CKPT, SYNC_URL

HERE = pathlib.Path(__file__).resolve().parent


def has(py, mod):
    return subprocess.run([py, "-c", f"import {mod}"], capture_output=True).returncode == 0


def doctor():
    rows = [("ffmpeg (decode)", shutil.which("ffmpeg") is not None, "brew install ffmpeg"),
            ("picture, sound, memory", has(sys.executable, "cv2") and has(sys.executable, "librosa"), "pip install -e ."),
            ("voices: Demucs", has(SEP_PY, "demucs"), "pip install -e '.[separate]'"),
            ("crowd: Mel-Band Roformer", has(CROWD_PY, "audio_separator"), "pip install -e '.[crowd]'"),
            ("speech: mlx-whisper (Apple GPU)", has(ASR_PY, "mlx_whisper"), "pip install -e '.[mlx]'"),
            ("speech fallback: faster-whisper", has(ASR_PY, "faster_whisper"), "pip install -e '.[speech]'"),
            ("on-screen text: Apple Vision (ocrmac)", has(sys.executable, "ocrmac"), "pip install -e '.[ocr]'"),
            ("sounds like: AudioSet tagger (AST)", has(sys.executable, "transformers"), "pip install -e '.[tags]'"),
            ("gaze: PyTorch", has(GAZE_PY, "torch"), "pip install -e '.[gaze]'"),
            ("sync: Synchformer libraries", has(SYNC_PY, "omegaconf") and has(SYNC_PY, "timm") and has(SYNC_PY, "einops"), "pip install -e '.[sync]'"),
            ("sync: Synchformer weights", (SYNCHFORMER / "logs/sync_models" / SYNC_CKPT / f"{SYNC_CKPT}.pt").exists(), "reel setup --sync"),
            ("gaze: UNISAL weights", (UNISAL / "training_runs/pretrained_unisal/weights_best.pth").exists(), "reel setup"),
            ("genre page: LoL reference frame", (GENRES / "lol.png").exists(), f"put a full broadcast frame at {GENRES / 'lol.png'}")]
    w = max(len(r[0]) for r in rows)
    for name, ok, fix in rows:
        print(f"  {'ok ' if ok else '-- '} {name.ljust(w)}  {'' if ok else fix}")
    print(f"\n  models and caches: {HOME}")


def setup():
    HOME.mkdir(parents=True, exist_ok=True)
    if not UNISAL.exists():
        subprocess.run(["git", "clone", "--depth", "1", "https://github.com/rdroste/unisal.git", str(UNISAL)], check=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    GENRES.mkdir(parents=True, exist_ok=True)
    if "--sync" in sys.argv:
        if not SYNCHFORMER.exists():
            subprocess.run(["git", "clone", "--depth", "1", "https://github.com/v-iashin/Synchformer.git", str(SYNCHFORMER)], check=True)
        d = SYNCHFORMER / "logs" / "sync_models" / SYNC_CKPT
        d.mkdir(parents=True, exist_ok=True)
        for name in (f"cfg-{SYNC_CKPT}.yaml", f"{SYNC_CKPT}.pt"):
            if not (d / name).exists():
                print("downloading", name)
                subprocess.run(["curl", "-L", "-o", str(d / name), f"{SYNC_URL}/{SYNC_CKPT}/{name}"], check=True)
        print("sync model ready")
    print("gaze model ready; the crowd model downloads itself on first use")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "doctor":
        doctor(); return
    if cmd == "setup":
        setup(); return
    perceive = importlib.import_module("perceive")
    if cmd == "zoom":
        ap = argparse.ArgumentParser(prog="reel zoom"); ap.add_argument("out")
        ap.add_argument("--t0", type=float, required=True); ap.add_argument("--t1", type=float, required=True)
        x = ap.parse_args(sys.argv[2:]); perceive.zoom(x.out, x.t0, x.t1); return
    if cmd == "watch":
        ap = argparse.ArgumentParser(prog="reel watch"); ap.add_argument("film"); ap.add_argument("--out", required=True)
        ap.add_argument("--page", type=float, default=30.0); ap.add_argument("--lang", default="ko")
        ap.add_argument("--title"); ap.add_argument("--subject"); ap.add_argument("--genre")
        x = ap.parse_args(sys.argv[2:])
        perceive.build(x.film, pathlib.Path(x.out), x.page, x.lang, x.title, x.subject, x.genre); return
    print(__doc__)


if __name__ == "__main__":
    main()
