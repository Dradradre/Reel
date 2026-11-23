#!/usr/bin/env python3
"""gaze.py - where a viewer's eyes are likely to go, frame by frame.

  python gaze.py FILM --out DIR [--run runs/NAME] [--at 12.4,33.1] [--subject 28.9:0.52,0.48] [--device auto|mps|cpu] [--decode auto|hw|sw]
  python gaze.py FILM --out DIR --run runs/NAME --relabel      (redo shots and flags from DIR/gaze.json, no model run)

Uses UNISAL (Droste et al., ECCV 2020, Apache-2.0), a video saliency model trained on human eye-tracking recordings
(DHF1K, Hollywood-2, UCF Sports, SALICON). It predicts a probability map of gaze for every sampled frame (6 per
second, the rate it was trained at).

Writes into DIR:
  gaze.png       trace over the whole film: gaze x and y, focus (how concentrated the map is), jumps at cuts
  gaze_at.png    frames at the requested times with the gaze map laid over them (default: evenly spread)
  gaze.json      per sample: time, peak x/y (0..1), focus, plus the jump at every cut, a per-shot table and a summary
Reading it:
  focus    share of the gaze probability inside a circle of 12 % of the frame width around the peak. High = one clear
           place to look. Low = the gaze splits between several things (group, wide shot, text in several corners).
  jump     distance the predicted gaze point moves across a cut, in % of the frame diagonal. Small = the next shot's
           subject sits where the eye already is (a match cut). Large on fast cuts = hard to follow. Measured from the
           last sample before the cut to the first sample 0.12 s or more after it. The model carries memory of the
           previous shot across the cut, so the number is not plain geometry: with the memory wiped at every cut the
           median roughly doubles (3.7 -> 8.4, 5.8 -> 11.0, 2.0 -> 6.5 on three films) and in about one cut in five
           the first gaze point lands more than 10 % away. Read it against the references, measured the same way:
           there the median is 4, nine in ten are under 14, and under 1 % pass 30.
  settle   in each jump row: distance from the last gaze point before the cut to where gaze settles in the new shot
           (the median gaze point of that shot). Less affected by the model's memory when the new shot is 0.5 s or longer.
  --subject t:x,y   where the thing you WANT watched is at time t (0..1 from the top left). Reports how much gaze
           probability lands within 12 % of it, and whether the peak is on it.
  shots    one row per shot (needs --run): start, length, mean focus, gaze point (median of the shot's samples).
  nowhere_to_look   shots of 0.5 s or longer whose mean focus is under 0.40. In the eight professional reference films
           (449 shots of 0.5 s or longer) the per-shot mean focus has median 0.68, 10th percentile 0.48 and 5th
           percentile 0.44; 2 % of shots (0 to 5 per film) are under 0.40. So this is rare but normal: the ones the
           references have are group shots, wide game or court shots and end cards with text in several places, where
           the gaze map splits between several things. Read it as "several places to look". A hero shot should not be here.
  eye_cannot_follow   cuts with another cut less than 0.5 s away where the gaze point jumps more than 30 % of the
           diagonal. A saccade takes about 0.2 s to start, so the viewer arrives as the shot is ending.
  bursts   one row per rapid-fire run of cuts: mean focus and how far the gaze point moves from one sample
           to the next. Cuts inside a burst are too fast to measure one by one, so they are left out of jumps.
  blank    a black or flat frame (fade, dip to black). The model still puts a tidy blob in the middle of it (focus
           0.5 .. 0.8, its centre habit), so blank samples are marked and left out of the focus numbers.
The model predicts where people look, not what they understand; it knows nothing about who the player is.
It samples 6 frames a second, so shots under about 0.2 s can fall between samples (their row has focus null) and the
jump across a very fast cut is measured to the first sample after it, which may already be the following shot.
Runs on the GPU (MPS) when there is one and carries on with the CPU if the GPU refuses an operation; both give the
same numbers to the three decimals stored. Decoding uses the Mac's video hardware by default so it stays out of the
way of other jobs (same pixels as software decoding); --decode sw uses every core and is faster when the machine is
free. A 91 s 1080p60 film takes about 20 s (10 s with --decode sw); on the CPU alone about 100 s.
"""
from __future__ import annotations
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import argparse, json, math, os, pathlib, subprocess, sys, time
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")     # single unsupported ops run on the CPU
import numpy as np, cv2, torch
import torch.nn.functional as F
from PIL import Image, ImageDraw
from common import display_scale, probe, font, lut, tc, polyline

HERE = pathlib.Path(__file__).resolve().parent
MW, MH = 96, 54                      # stored map size
IW, IH = 384, 224                    # model input
CHUNK = 48                           # frames per forward pass
LOW_FOCUS = 0.40                     # per-shot mean focus under this = "nowhere to look" (reference shots: p10 0.42)
MIN_LOOK = 0.5                       # ... if the shot lasts at least this long, s
BIG_JUMP = 30.0                      # % of the diagonal
FAST_GAP = 0.5                       # s; a big jump with another cut this close cannot be followed
BLANK_SD = 6.0                       # luma spread (0..255) under which a frame counts as blank (black, white, a fade)


def load_model():
    import io, contextlib, types, importlib
    pkg = types.ModuleType("unisal")                       # skip the package __init__ (it pulls in training-only deps)
    from config import UNISAL
    pkg.__path__ = [str(UNISAL / "unisal")]
    sys.modules["unisal"] = pkg
    with contextlib.redirect_stdout(io.StringIO()):
        um = importlib.import_module("unisal.model")
        m = um.UNISAL()
        m.load_best_weights(UNISAL / "training_runs/pretrained_unisal")
    return m.eval()


def sampled_frames(path, fps, step, hw=False):
    """Every step-th frame of the constant-fps stream, picked before scaling, so only the frames we use get resized.
    hw: decode on the Mac's video hardware (same pixels, a tenth of the CPU time, about twice the wait)."""
    scale = display_scale(path, f"{IW}:{IH}:flags=area")
    cmd = ["ffmpeg", "-v", "error"] + (["-hwaccel", "videotoolbox"] if hw else []) + ["-i", str(path), "-map", f"0:{probe(path)['video_index']}", "-vf",
           f"fps={fps},select='not(mod(n\\,{step}))',{scale}", "-fps_mode", "passthrough",
           "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=IW * IH * 3 * 16)
    n = IW * IH * 3
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        yield np.frombuffer(buf, np.uint8).reshape(IH, IW, 3)
    p.stdout.close(); p.wait()


def forward(m, x, h, source="DHF1K"):
    """UNISAL.forward for one clip, with the per-frame CNN and decoder run as one batch. x: [T, 3, H, W] normalised.
    Same arithmetic as the model's own forward (BatchNorm is in eval mode, so batching frames changes nothing)."""
    m.this_source = source
    s = f"_{source.lower()}"
    f1, f2, f4 = m.cnn(x)
    f2, f4 = m.skip_2x(f2), m.skip_4x(f4)
    if m.n_gaussians > 0:
        f1 = torch.cat((f1, m._get_gaussian_maps(f1, s)), dim=1)
    f1 = m.post_cnn(f1)
    if h is None:
        h = [torch.zeros(1, c.hidden_ch, f1.shape[-2], f1.shape[-1], device=x.device) for c in m.rnn.cell_list]
    seq, h = m.rnn(f1[None], hidden=h)
    f = f1 + m.post_rnn(seq[0]) if m.res_rnn else m.post_rnn(seq[0])
    f = m.upsampling_2(torch.cat((m.upsampling_1(f), f2), dim=1))
    f = m.post_upsampling_2(torch.cat((f, f4), dim=1))
    f = getattr(m, "adaptation" + (s if m.ds_adaptation else ""))(f)
    f = F.interpolate(f, size=x.shape[-2:], mode="nearest")
    f = F.pad(f, [m.smoothing_ksize // 2] * 4, mode="replicate")
    f = getattr(m, "smoothing" + (s if m.ds_smoothing else ""))(f)
    f = F.interpolate(f, size=(MH, MW), mode="bilinear", align_corners=False)
    return torch.log_softmax(f.flatten(1), dim=1).view_as(f), h


class PlaybackGaze:
    """Advance on a fixed six-Hz film clock; endpoint-only inspections never update the recurrent state."""
    def __init__(self):
        self.model = load_model()
        # Small, intermittent batches run on CPU to leave the GPU available to the audio reader.
        torch.set_num_threads(2)
        self.mean = torch.tensor([.485, .456, .406]).view(1, 3, 1, 1)
        self.std = torch.tensor([.229, .224, .225]).view(1, 3, 1, 1)
        self.hidden = self.last_map = self.last_pts = None

    def infer(self, images, commit):
        pixels = np.stack([np.asarray(image.resize((IW, IH), Image.Resampling.BOX)) for image in images])
        x = (torch.from_numpy(pixels).permute(0, 3, 1, 2).float() / 255 - self.mean) / self.std
        with torch.inference_mode():
            # UNISAL replaces entries in its input state list, even for a one-frame inspection.
            hidden = None if self.hidden is None else self.hidden.copy()
            maps, hidden = forward(self.model, x, hidden)
        densities = maps[:, 0].exp().numpy()
        if commit:
            self.hidden = hidden
            self.last_map = densities[-1].copy()
        return densities

    @staticmethod
    def point(density, image):
        if all(a == b for a, b in image.getextrema()):
            return None
        y, x = np.unravel_index(density.argmax(), density.shape)
        return [(float(x) + .5) / density.shape[1], (float(y) + .5) / density.shape[0]]

    def read(self, start_s, end_s, current_frame, size, pages):
        from attention import fixation_box
        from common import sample_boundary
        from sound_glimpses import sheet_picture
        picture = sheet_picture(pages, size)
        batch = []
        samples = []
        def flush():
            densities = self.infer([image for _, _, image in batch], True)
            for (tick, pts, image), density in zip(batch, densities):
                samples.append({"tick": tick, "pts_us": pts, "point": self.point(density, image)})
            self.last_pts = batch[-1][1]
            batch.clear()

        for tick in range(sample_boundary(start_s, 6), sample_boundary(end_s, 6)):
            frame = picture(tick * 1_000_000 // 6)
            if frame is None:
                continue
            pts, image = frame
            batch.append((tick, pts, image))
            if len(batch) >= CHUNK:
                flush()
        if batch:
            flush()
        result = {"status": "available", "model": "UNISAL/DHF1K", "rate_hz": 6, "samples": samples,
                  "basis": "Predicted fixation from pictures shown by each tick. Audio and viewer eye positions are unknown."}
        if current_frame is None:
            return result
        pts = result["pts_us"] = current_frame["pts_us"]
        current = picture(pts)
        if current is None or current[0] != pts:
            raise ValueError("current picture missing from the delivered frame sheets")
        image = current[1]
        density = self.last_map if pts == self.last_pts else self.infer([image], False)[0]
        point = self.point(density, image)
        if point is None:
            result["reason"] = "uniform picture"
            return result
        result.update(point=point, box=fixation_box(point))
        return result


def serve():
    from contextlib import redirect_stdout
    reader = None
    for line in sys.stdin:
        try:
            with redirect_stdout(sys.stderr):
                if reader is None:
                    reader = PlaybackGaze()
                result = reader.read(**json.loads(line))
        except Exception as error:
            result = {"status": "unavailable", "model": "UNISAL/DHF1K", "reason": str(error),
                      "startup_failure": reader is None}
            reader = None
        print(json.dumps(result), flush=True)


def predict(path, info, rate=6, device="auto", decode="auto"):
    fps = info["fps"]
    step = max(int(round(fps / rate)), 1)
    dev = torch.device("mps" if device in ("auto", "mps") and torch.backends.mps.is_available() else "cpu")
    if dev.type == "mps":
        torch.set_num_threads(2)                            # the GPU does the work; leave the cores to other jobs
    m = load_model().to(dev)
    mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1); std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
    times, maps, thumbs, buf, h = [], [], [], [], None
    def flush():
        nonlocal h, buf, dev, m
        if not buf:
            return
        raw = torch.from_numpy(np.stack(buf)).permute(0, 3, 1, 2)
        for attempt in (0, 1):
            try:
                x = (raw.to(dev).float() / 255 - mean.to(dev)) / std.to(dev)
                with torch.no_grad():
                    out, h2 = forward(m, x, h)
                maps.extend(np.exp(out[:, 0].float().cpu().numpy()))
                h = h2
                break
            except (RuntimeError, NotImplementedError) as e:
                if dev.type == "cpu" or attempt:
                    raise
                print(f"gaze: {dev} failed ({str(e).splitlines()[0][:120]}); continuing on the CPU", file=sys.stderr)
                dev = torch.device("cpu"); m = m.to(dev)
                h = [v.to(dev) for v in h] if h is not None else None
        buf = []
    hw = decode == "hw" or (decode == "auto" and sys.platform == "darwin")
    for use_hw in ((True, False) if hw else (False,)):          # no frames from the hardware decoder: decode in software
        for k, fr in enumerate(sampled_frames(path, fps, step, use_hw)):
            times.append(k * step / fps); thumbs.append(cv2.resize(fr, (192, 108), interpolation=cv2.INTER_AREA))
            buf.append(fr)
            if len(buf) == CHUNK:
                flush()
        flush()
        if times:
            break
    return np.asarray(times), np.asarray(maps, np.float32), np.asarray(thumbs), dev.type


def describe(maps):
    n = len(maps)
    yy, xx = np.mgrid[0:MH, 0:MW]
    px, py, focus = np.zeros(n), np.zeros(n), np.zeros(n)
    r = 0.12 * MW
    for i, p in enumerate(maps):
        p = p / (p.sum() + 1e-12)
        k = int(np.argmax(cv2.GaussianBlur(p, (0, 0), 2)))
        cy, cx = divmod(k, MW)
        px[i], py[i] = (cx + 0.5) / MW, (cy + 0.5) / MH
        focus[i] = float(p[(xx - cx) ** 2 + (yy - cy) ** 2 <= r * r].sum())
    return px, py, focus


def mass_near(p, x, y):
    yy, xx = np.mgrid[0:MH, 0:MW]
    p = p / (p.sum() + 1e-12)
    r = 0.12 * MW
    return float(p[(xx - x * MW) ** 2 + (yy - y * MH) ** 2 <= r * r].sum())


def overlay(thumb, p, size=(384, 216)):
    img = cv2.resize(thumb, size, interpolation=cv2.INTER_CUBIC).astype(np.float32)
    q = cv2.resize(p / (p.max() + 1e-12), size, interpolation=cv2.INTER_CUBIC)
    heat = lut("inferno")[(np.clip(q, 0, 1) * 255).astype(np.uint8)].astype(np.float32)
    a = np.clip(q * 1.3, 0, 0.75)[..., None]
    return (img * (1 - a) * 0.75 + heat * a + img * 0.0).clip(0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("--out", required=True)
    ap.add_argument("--run", default=None, help="a folder with cuts.json, for cut times")
    ap.add_argument("--at", default="", help="times for the overlay sheet, comma separated")
    ap.add_argument("--subject", action="append", default=[], help="t:x,y where the intended subject is")
    ap.add_argument("--device", default="auto", choices=("auto", "mps", "cpu"))
    ap.add_argument("--decode", default="auto", choices=("auto", "hw", "sw"), help="hw = video hardware (little CPU, the default on a Mac), sw = all CPU cores (fastest)")
    ap.add_argument("--relabel", action="store_true", help="redo shots and flags from DIR/gaze.json without the model")
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    info = probe(a.video); name = pathlib.Path(a.video).name
    t0 = time.time()
    old = json.load(open(out / "gaze.json")) if a.relabel else None
    if old:
        times, px, py, focus = (np.array([s[k] for s in old["samples"]], float) for k in ("t", "x", "y", "focus"))
        blank = np.array([bool(s.get("blank")) for s in old["samples"]])
        maps = thumbs = None; device = old["summary"].get("device")
    else:
        times, maps, thumbs, device = predict(a.video, info, device=a.device, decode=a.decode)
        px, py, focus = describe(maps)
        blank = np.array([float(t.mean(axis=2).std()) < BLANK_SD for t in thumbs])     # black or flat frame: nothing to look at
    seen = ~blank if (~blank).any() else np.ones(len(times), bool)
    dur = float(times[-1]) if len(times) else 0
    def dist(i0, i1):                                           # gaze point movement, % of the frame diagonal
        return math.hypot((px[i1] - px[i0]) * 16, (py[i1] - py[i0]) * 9) / math.hypot(16, 9) * 100
    cuts, allcuts, bursts, burst_rows, shots = [], [], [], [], []
    if a.run:
        j = json.load(open(pathlib.Path(a.run) / "analysis.json"))
        allcuts = sorted(float(c) for c in j["cuts"])
        for b in j.get("bursts", []):
            s0, s1 = b["f"] / info["fps"], (b["f"] + b["n"]) / info["fps"]
            bursts.append((s0, s1))
            k = np.flatnonzero((times >= s0) & (times <= s1))
            steps = [dist(i, i + 1) for i in k[:-1]]
            burst_rows.append(dict(t=round(s0, 2), len=round(s1 - s0, 2), cuts=b.get("cuts"),
                                   focus=round(float(focus[k].mean()), 3) if len(k) else None,
                                   gaze_step_median_pct=round(float(np.median(steps)), 1) if steps else None,
                                   gaze_steps_over_30_pct=round(float(np.mean([v > BIG_JUMP for v in steps]) * 100), 1) if steps else None))
        cuts = [c for c in allcuts if not any(s <= c <= e for s, e in bursts)]
        edges = [0.0] + allcuts + [max(info["dur"], dur)]
        for s0, s1 in zip(edges[:-1], edges[1:]):
            if s1 - s0 < 1e-3:
                continue
            k = np.flatnonzero((times >= s0 - 1e-3) & (times < s1 - 1e-3))
            row = dict(t=round(s0, 2), len=round(s1 - s0, 2), focus=None, x=None, y=None)
            if len(k) and not seen[k].any():
                row["blank"] = True
            k = k[seen[k]]
            if len(k):
                row.update(focus=round(float(focus[k].mean()), 3), x=round(float(np.median(px[k])), 3), y=round(float(np.median(py[k])), 3))
            if any(s <= s0 <= e for s, e in bursts):
                row["in_burst"] = True
            shots.append(row)
    nowhere = [dict(t=r["t"], len=r["len"], focus=r["focus"]) for r in shots
               if r["focus"] is not None and r["focus"] < LOW_FOCUS and r["len"] >= MIN_LOOK]
    jumps = []
    for c in cuts:
        b = np.flatnonzero(times <= c - 0.05); f = np.flatnonzero(times >= c + 0.12)
        if len(b) and len(f) and (not jumps or c - jumps[-1]["t"] > 0.2):
            gap = min([abs(c - o) for o in allcuts if o != c] or [1e9])
            row = dict(t=round(float(c), 2), jump_pct=round(dist(b[-1], f[0]), 1), gap=round(min(gap, 99.0), 2))
            nxt = next((r for r in shots if abs(r["t"] - c) < 0.006 and r["x"] is not None), None)
            if nxt:
                row["settle_pct"] = round(math.hypot((nxt["x"] - px[b[-1]]) * 16, (nxt["y"] - py[b[-1]]) * 9) / math.hypot(16, 9) * 100, 1)
            if row["jump_pct"] > BIG_JUMP and gap < FAST_GAP:
                row["eye_cannot_follow"] = True
            jumps.append(row)
    lost = [dict(t=r["t"], jump_pct=r["jump_pct"], gap=r["gap"]) for r in jumps if r.get("eye_cannot_follow")]
    if old:
        subj = old.get("subjects", [])
    else:
        subj = []
        for s in a.subject:
            t, xy = s.split(":"); x, y = [float(v) for v in xy.split(",")]
            i = int(np.argmin(np.abs(times - float(t))))
            subj.append(dict(t=float(t), x=x, y=y, gaze_on_subject=round(mass_near(maps[i], x, y), 2),
                             peak_dist_pct=round(math.hypot((px[i] - x) * 16, (py[i] - y) * 9) / math.hypot(16, 9) * 100, 1)))
    S = dict(focus_median=round(float(np.median(focus[seen])), 2), focus_p10=round(float(np.percentile(focus[seen], 10)), 2),
             focus_p90=round(float(np.percentile(focus[seen], 90)), 2), blank_pct=round(float(blank.mean() * 100), 1),
             jump_median_pct=round(float(np.median([j["jump_pct"] for j in jumps])), 1) if jumps else None,
             jumps_over_30_pct=round(float(np.mean([j["jump_pct"] > BIG_JUMP for j in jumps]) * 100), 1) if jumps else None,
             settle_median_pct=round(float(np.median([j["settle_pct"] for j in jumps if "settle_pct" in j])), 1) if any("settle_pct" in j for j in jumps) else None,
             jumps=len(jumps), fast_cut_jumps=sum(j["gap"] < FAST_GAP for j in jumps), eye_cannot_follow=len(lost),
             shots=len(shots), nowhere_to_look=len(nowhere), nowhere_to_look_s=round(sum(r["len"] for r in nowhere), 1),
             nowhere_to_look_pct=round(sum(r["len"] for r in nowhere) / max(dur, 1e-6) * 100, 1),
             device=device, seconds=old["summary"].get("seconds") if old else round(time.time() - t0, 1))
    json.dump(dict(summary=S, subjects=subj, jumps=jumps, eye_cannot_follow=lost, shots=shots, nowhere_to_look=nowhere, bursts=burst_rows,
                   samples=[dict(t=round(float(t), 3), x=round(float(x), 3), y=round(float(y), 3), focus=round(float(f), 3), **({"blank": 1} if bl else {}))
                            for t, x, y, f, bl in zip(times, px, py, focus, blank)]),
              open(out / "gaze.json", "w"))
    report = dict(summary=S, subjects=subj, nowhere_to_look=nowhere, eye_cannot_follow=lost)
    if old:
        print(json.dumps(report, indent=1)); return
    # trace page
    X0, PW = 60, 1480
    img = Image.new("RGB", (X0 + PW + 10, 400), (14, 15, 18)); d = ImageDraw.Draw(img, "RGBA")
    d.text((8, 5), f"gaze  {name}   focus median {S['focus_median']}   jump median {S['jump_median_pct']} % of diagonal   red band = nowhere to look ({S['nowhere_to_look']})   white cap = eye cannot follow ({S['eye_cannot_follow']})", font=font(13), fill=(240, 240, 240))
    def tx(t):
        return X0 + t / max(dur, 1e-6) * PW
    n = len(times)
    for k in range(0, PW, 96):                                  # thumbnails
        i = int(min((k + 48) / PW * n, n - 1))
        img.paste(Image.fromarray(overlay(thumbs[i], maps[i], (96, 54))), (X0 + k, 28))
    rows = (("gaze x", px, 92, 70, (255, 170, 60)), ("gaze y", py, 170, 70, (90, 200, 255)), ("focus", focus, 248, 70, (120, 255, 160)))
    for lab, v, y, h, col in rows:
        d.rectangle([X0, y, X0 + PW, y + h], fill=(22, 24, 29)); d.text((4, y + 2), lab, font=font(10), fill=(190, 195, 205))
        polyline(d, [tx(t) for t in times], y + h - 2 - np.clip(v, 0, 1) * (h - 4) if lab == "focus" else y + 2 + np.clip(v, 0, 1) * (h - 4), col, 1)
    d.text((4, 104), "left..right", font=font(8), fill=(110, 115, 125)); d.text((4, 182), "top..bottom", font=font(8), fill=(110, 115, 125))
    y = 326
    d.rectangle([X0, y, X0 + PW, y + 46], fill=(22, 24, 29)); d.text((4, y + 2), "jump", font=font(10), fill=(190, 195, 205)); d.text((4, y + 14), "at cuts", font=font(8), fill=(110, 115, 125))
    for j in jumps:
        hgt = min(j["jump_pct"], 60) / 60 * 44
        col = (90, 230, 120) if j["jump_pct"] <= 12 else (250, 210, 80) if j["jump_pct"] <= 30 else (255, 90, 90)
        d.line([(tx(j["t"]), y + 45), (tx(j["t"]), y + 45 - hgt)], fill=col, width=2)
        if j.get("eye_cannot_follow"):                         # white cap: big jump on a fast cut
            d.rectangle([tx(j["t"]) - 2, y + 1, tx(j["t"]) + 2, y + 4], fill=(255, 255, 255))
    for r in nowhere:                                           # red band in the focus row: nowhere to look
        d.rectangle([tx(r["t"]), 248, tx(r["t"] + r["len"]), 318], fill=(255, 70, 70, 70))
    for s, e in bursts:
        d.rectangle([tx(s), 92, tx(e), 372], fill=(120, 60, 160, 60))
    step = 5 if dur <= 150 else 30
    t = 0
    while t <= dur:
        d.line([(tx(t), 374), (tx(t), 380)], fill=(170, 170, 180)); d.text((tx(t) + 2, 380), tc(t), font=font(9), fill=(170, 175, 185)); t += step
    img.save(out / "gaze.png")
    # overlay sheet
    at = [float(x) for x in a.at.split(",") if x.strip()] or list(np.linspace(dur * 0.04, dur * 0.96, 12))
    cols = 4
    rowsn = math.ceil(len(at) / cols)
    sheet = Image.new("RGB", (cols * 388 + 8, rowsn * 238 + 30), (14, 15, 18)); d = ImageDraw.Draw(sheet)
    d.text((8, 5), f"gaze map over picture  {name}   bright = where eyes go; white ring = predicted gaze point", font=font(13), fill=(240, 240, 240))
    for k, t in enumerate(at):
        i = int(np.argmin(np.abs(times - t)))
        r_, c_ = divmod(k, cols)
        x0, y0 = 6 + c_ * 388, 28 + r_ * 238
        sheet.paste(Image.fromarray(overlay(thumbs[i], maps[i])), (x0, y0))
        cx, cy = x0 + px[i] * 384, y0 + py[i] * 216
        d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], outline=(255, 255, 255), width=2)
        for s in subj:
            if abs(s["t"] - t) < 0.2:
                sx, sy = x0 + s["x"] * 384, y0 + s["y"] * 216
                d.rectangle([sx - 10, sy - 10, sx + 10, sy + 10], outline=(80, 255, 120), width=2)
        d.text((x0 + 2, y0 + 218), f"{tc(times[i])}  focus {focus[i]:.2f}", font=font(11), fill=(225, 225, 230))
    sheet.save(out / "gaze_at.png")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    if sys.argv[1:] == ["--serve"]:
        serve()
    else:
        main()
