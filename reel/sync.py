#!/usr/bin/env python3
"""sync.py - does the sound belong to the picture at this moment, or does it run early or late?

  python sync.py FILM --out sync.json [--model audioset|lrs3] [--hop 2.5]

A person notices sound running late when a ball is struck, a door slams, a foot lands or lips move before the sound
comes. Synchformer (Iashin et al., ICASSP 2024; MIT) was trained to read exactly that from short clips: given 5 s of
picture and 5 s of sound, it scores 21 offsets from sound 2 s early to sound 2 s late, in 0.2 s steps. This script
slides that window along the whole film and keeps, for every window, the full spread of belief over the 21 offsets,
so the reel can show where the film is sure it is in sync, where it is sure it is not, and where nothing in the
picture says anything about the sound (music over scenery).

Output: {"hop": s, "win": 5.0, "grid": [-2.0 .. 2.0], "windows": [{"t": start_s, "p": [21 probs]}, ...]}
Offsets are in the model's sign: positive = the sound comes EARLY; negative = the sound comes LATE.
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import argparse, json, os, subprocess, sys, pathlib
import numpy as np
from common import display_scale, probe, read_audio

HERE = pathlib.Path(__file__).resolve().parent
from config import SYNCHFORMER as REPO
MODELS = {"audioset": "24-01-04T16-39-21", "lrs3": "23-12-23T18-33-57"}
VFPS, AFPS, SIDE, WIN = 25, 16000, 256, 5.0


def load_model(name, device):
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "model" / "modules" / "feat_extractors" / "visual"))
    import torch
    from omegaconf import OmegaConf
    from scripts.train_utils import get_model, get_transforms
    exp = MODELS[name]
    cfg = OmegaConf.load(REPO / "logs" / "sync_models" / exp / f"cfg-{exp}.yaml")
    cfg.model.params.afeat_extractor.params.ckpt_path = None
    cfg.model.params.vfeat_extractor.params.ckpt_path = None
    cfg.model.params.transformer.target = cfg.model.params.transformer.target.replace(".modules.feature_selector.", ".sync_model.")
    _, model = get_model(cfg, device)
    ck = torch.load(REPO / "logs" / "sync_models" / exp / f"{exp}.pt", map_location="cpu", weights_only=False)
    model.load_state_dict(ck["model"])
    model.eval()
    grid = np.linspace(-cfg.data.max_off_sec, cfg.data.max_off_sec, cfg.model.params.transformer.params.off_head_cfg.params.out_features)
    return model, get_transforms(cfg, ["test"])["test"], grid


def offset_distributions(model, transform, device, inputs):
    import torch
    items = []
    for clip, audio in inputs:
        item = dict(video=torch.from_numpy(clip).permute(0, 3, 1, 2).contiguous(),
                    audio=torch.from_numpy(audio.copy()),
                    meta={"video": {"fps": [VFPS]}, "audio": {"framerate": [AFPS]}}, path="", split="test",
                    targets={"v_start_i_sec": 0.0, "offset_sec": 0.0})
        items.append(transform(item))
    batch = torch.utils.data.default_collate(items)
    with torch.no_grad():
        _, logits = model(batch["video"].float().to(device), batch["audio"].float().to(device))
    return torch.softmax(logits.float(), -1).cpu().numpy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("film"); ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="audioset", choices=list(MODELS)); ap.add_argument("--hop", type=float, default=2.5)
    ap.add_argument("--batch", type=int, default=4)
    a = ap.parse_args()
    import torch
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    model, tf, grid = load_model(a.model, device)

    info = probe(a.film)
    aspect, dur = info["display_aspect"], info["dur"]
    if aspect >= 1:
        h = SIDE; w = int(round(aspect * SIDE / 2)) * 2
    else:
        w = SIDE; h = int(round(SIDE / aspect / 2)) * 2
    audio = read_audio(a.film, AFPS, channels=1)[:, 0]
    scale = display_scale(a.film, f"{w}:{h}")
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(a.film), "-map", f"0:{info['video_index']}", "-vf", f"fps={VFPS},{scale}",
                            "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    fsz = w * h * 3
    nwin_f = int(WIN * VFPS)
    starts = [round(s, 3) for s in np.arange(0, max(dur - WIN, 0) + 1e-6, a.hop)]
    frames, first = [], 0                       # frames[k] is frame number first + k
    out, pending = [], []

    def run(batch):
        inputs = []
        for t0, clip in batch:
            a0 = int(round(t0 * AFPS))
            audio_window = audio[a0:a0 + int(WIN * AFPS)]
            if len(audio_window) < int(WIN * AFPS):
                audio_window = np.pad(audio_window, (0, int(WIN * AFPS) - len(audio_window)))
            inputs.append((clip, audio_window))
        pr = offset_distributions(model, tf, device, inputs)
        for (t0, _), p in zip(batch, pr):
            out.append(dict(t=t0, p=[round(float(x), 4) for x in p]))
            print(f"  {t0:7.1f}s  best {grid[int(np.argmax(p))]:+.1f}  p={float(np.max(p)):.2f}", flush=True)

    si, n = 0, 0
    while si < len(starts):
        buf = dec.stdout.read(fsz)
        if len(buf) == fsz:
            frames.append(np.frombuffer(buf, np.uint8).reshape(h, w, 3)); n += 1
        done = len(buf) < fsz
        while si < len(starts):
            f0 = int(round(starts[si] * VFPS))
            if first + len(frames) >= f0 + nwin_f or (done and first + len(frames) > f0 + VFPS):
                clip = np.stack(frames[f0 - first:f0 - first + nwin_f])
                if len(clip) < nwin_f:
                    clip = np.concatenate([clip, np.repeat(clip[-1:], nwin_f - len(clip), 0)])
                pending.append((starts[si], clip)); si += 1
                if len(pending) >= a.batch:
                    run(pending); pending = []
            else:
                break
        if si < len(starts):                    # drop frames no later window needs
            keep_from = int(round(starts[si] * VFPS))
            if keep_from > first:
                frames = frames[keep_from - first:]; first = keep_from
        if done:
            break
    if pending:
        run(pending)
    dec.stdout.close(); dec.wait()
    json.dump(dict(model=a.model, hop=a.hop, win=WIN, grid=[round(float(g), 2) for g in grid], windows=out), open(a.out, "w"))
    print(f"[sync] {len(out)} windows -> {a.out}")


if __name__ == "__main__":
    main()
