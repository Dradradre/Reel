#!/usr/bin/env python3
"""perceive.py - turn a whole film into one continuous, gapless audiovisual reel that a reader who only sees still
images and text can read front to back, and so live through the film in order.

  python perceive.py FILM --out DIR [--page 30] [--lang ko]          build the reel
  python perceive.py zoom DIR --t0 S --t1 S                          re-watch a stretch closer (writes DIR/zoom_*.png)

Why it is built this way
  A reader of text and images experiences time as reading order. So the film is laid out as pages that run strictly
  forward, one after another, with nothing between them left out:
  - every frame of picture and every sample of sound leaves a mark somewhere on the page (max-pooled, never sampled),
    so a one-frame glitch or a 0.3 s repeat cannot fall between two samples;
  - eye and ear share one time axis on each page, so whether a sound belongs to its picture can be seen;
  - what is read on screen and what is said are written at the moment they happen, so the story can be followed;
  - the reel carries memory: when a picture or a voice comes back that was already seen or heard, the page says
    when it was first met, the way a viewer thinks "I heard that a second ago";
  - a 10-minute film is about 20 pages and one text stream, small enough to be held whole in mind while reading.

Read it with READING_REEL.md. Nothing here judges the film; it only makes it perceivable.
"""
from __future__ import annotations
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import argparse, json, math, os, pathlib, re, subprocess, sys, tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import numpy as np, cv2, librosa
from PIL import Image, ImageDraw
from common import RecordedSource, audio_timeline, display_scale, pcm_codec, probe, read_audio, reel_source, sample_boundary, video_end, font, lut, tc, polyline
from frames import native_frames, save_frame as _save_frame
from config import SEP_PY, ASR_PY, GAZE_PY, GENRES
from stereo import channel_pairs, pair_cues
from sounds import AST_NAME, AudioTagger, recorded_context, SOURCE_SCORE

HERE = pathlib.Path(__file__).resolve().parent
SR = 48000
HOP = 480                      # 10 ms audio frames
TW, TH = 267, 150              # thumbnail size on the page
PICTURE_TOP, PICTURE_FOOT = 34, 35  # space for quality, timestamp and frame-shape labels
THUMB_FPS = 4
LEFT, PW = 64, 1500
BG = (12, 13, 16)


# ============================================================== picture

def video_pass(path, info, log):
    """One decode of every frame. Keeps per-frame numbers and a slit column, thumbnails at 4 fps plus the first frame
    of every new shot."""
    fps = info["fps"]
    W, H = 320, 180
    luma, mad, corr, slit, hashes, thumbs, th_idx = [], [], [], [], [], [], []
    times = []
    last_bucket = -1
    next_minute = 60
    prev = None
    end = 0
    for i, (pts, duration, buf) in enumerate(native_frames(path, W, H)):
        t = pts / 1_000_000
        times.append(t)
        end = min(info["dur"], t + (duration / 1_000_000 if duration else 1 / fps))
        fr = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        g = cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY)
        s = cv2.resize(g, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)
        luma.append(float(s.mean()))
        if prev is None:
            mad.append(0.0); corr.append(1.0)
        else:
            mad.append(float(np.abs(s - prev).mean()))
            a, b = s - s.mean(), prev - prev.mean()
            corr.append(float((a * b).sum() / (math.sqrt(float((a * a).sum()) * float((b * b).sum())) + 1e-6)))
        prev = s
        slit.append(cv2.resize(fr[H // 2 - 1:H // 2 + 1].mean(0, keepdims=True).astype(np.uint8), (64, 1),
                               interpolation=cv2.INTER_AREA)[0])
        new_shot = len(mad) > 1 and corr[-1] < 0.55 and mad[-1] > 9
        bucket = pts * THUMB_FPS // 1_000_000
        if i == 0 or bucket != last_bucket or new_shot:
            thumbs.append(cv2.resize(fr, (TW, TH), interpolation=cv2.INTER_AREA)); th_idx.append(i)
            hashes.append(cv2.resize(g, (32, 18), interpolation=cv2.INTER_AREA).astype(np.float32))
        last_bucket = bucket
        if t >= next_minute:
            log(f"  picture {t / 60:.0f} min")
            next_minute = (int(t // 60) + 1) * 60
    return dict(fps=fps, edges=np.array([*times, end]), luma=np.array(luma, np.float32), mad=np.array(mad, np.float32), corr=np.array(corr, np.float32),
                slit=np.array(slit, np.uint8), thumbs=np.array(thumbs, np.uint8), th_idx=np.array(th_idx),
                hashes=np.array(hashes, np.float32))


def frame_edges(v):
    if "edges" in v:
        return v["edges"]
    return np.arange(len(v["mad"]) + 1) / v["fps"]


def thumbnail_times(v):
    if "thumb_times" in v:  # a zoom can add frames that an older cache never decoded
        return v["thumb_times"]
    if "edges" in v:
        return v["edges"][v["th_idx"]]
    return v["th_idx"] / v["fps"]


def rolling_median(x, k):
    pad = np.pad(x, k, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(pad, 2 * k + 1), axis=1)


def find_cuts(v):
    mad, corr = v["mad"], v["corr"]
    med = rolling_median(mad, 6)
    cand = ((corr < 0.55) & (mad > 9)) | ((mad > 4 * med + 6) & (corr < 0.8) & (mad > 14))
    cand[0] = False
    cuts = list(np.flatnonzero(cand))
    merged = []
    edges = frame_edges(v)
    for c in cuts:                       # a fade or flash makes several candidates in a row: keep the first
        if merged and c - merged[-1] <= 2 and edges[c] - edges[merged[-1]] < 0.25:
            continue
        merged.append(int(c))
    return np.array(merged, int)


# ============================================================== sound

def ebur128(path):
    info = probe(path)
    p = subprocess.run(["ffmpeg", "-nostats", "-hide_banner", "-i", str(path), "-map", f"0:{info['audio_index']}", "-af",
                        f"{audio_timeline(info)},ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M:file=-", "-f", "null", "-"],
                       capture_output=True, text=True)
    t, v, cur = [], [], None
    for line in p.stdout.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            cur = float(m.group(1))
        m = re.search(r"lavfi\.r128\.M=(-?[\d.]+|-?inf|nan)", line)
        if m and cur is not None:
            try:
                x = float(m.group(1))
            except ValueError:
                x = -70.0
            t.append(cur); v.append(max(x, -70.0) if np.isfinite(x) else -70.0)
    return np.array(t, np.float32), np.array(v, np.float32)


def audio_pass(path, log, *, has_audio=True):
    channels = read_audio(path, SR, channels=None)
    starts = np.arange(0, len(channels), HOP)
    lengths = np.minimum(HOP, len(channels) - starts)
    powers = []
    S = None
    for channel in channels.T:
        mel = librosa.feature.melspectrogram(y=channel, sr=SR, n_fft=2048, hop_length=HOP,
                                             n_mels=128, fmin=30, fmax=16000)
        if S is None:
            S = mel
        else:
            S += mel
        powers.append(np.add.reduceat(np.square(channel), starts) / lengths)
    S /= channels.shape[1]
    db = librosa.power_to_db(S, ref=1.0, top_db=None).astype(np.float32)
    powers = np.asarray(powers, dtype=np.float32)
    channel_rms = 10 * np.log10(np.maximum(powers, 1e-12))
    rms = 10 * np.log10(np.maximum(powers.mean(axis=0), 1e-12))
    # sound-scene change: how different the last half second sounds from the next half second
    z = (db - db.mean(1, keepdims=True)) / (db.std(1, keepdims=True) + 1e-6)
    c = np.cumsum(np.pad(z, ((0, 0), (1, 0))), axis=1)
    w = 50
    nov = np.zeros(z.shape[1], np.float32)
    for i in range(w, z.shape[1] - w):
        a = (c[:, i] - c[:, i - w]) / w
        b = (c[:, i + w] - c[:, i]) / w
        nov[i] = float(np.linalg.norm(a - b) / math.sqrt(len(a)))
    from scipy.signal import find_peaks
    pk, _ = find_peaks(nov, height=float(np.percentile(nov, 97)), distance=60)
    mt, mv = ebur128(path)
    stereo = {}
    if has_audio and channels.shape[1] > 1:
        pairs = channel_pairs(channels.shape[1], probe(path)["audio_layout"])
        if pairs:
            channel_numbers = np.array([p["channels"] for p in pairs])
            starts = np.arange(0, len(channels), SR // 4)
            ends = np.minimum(starts + SR // 4, len(channels))
            lags = np.full((len(pairs), len(starts)), np.nan, dtype=np.float32)
            for i, (first, second) in enumerate(channel_numbers - 1):
                for j, (start, stop) in enumerate(zip(starts, ends)):
                    cue = pair_cues(channels[start:stop, [first, second]], SR)
                    if cue["second_minus_first_us"] is not None:
                        lags[i, j] = cue["second_minus_first_us"]
            stereo = dict(stereo_pairs=channel_numbers, stereo_us=lags, stereo_end=ends / SR)
    log("  sound done")
    return dict(db=db, rms=rms, channel_rms=channel_rms, nov=nov, changes=pk * HOP / SR,
                m_t=mt, m_v=mv, dur=len(channels) / SR, **stereo)


def voices(path, out, lang, log, prompt=None, deterministic=False):
    """Voices (commentary, interviews, crowd voices) separated from music, transcribed, and fingerprinted for memory."""
    stem_dir = out / "stems"
    voc = stem_dir / "vocals.wav"
    if not voc.exists() or not (stem_dir / "crowd.wav").exists():
        shifts = ["--shifts", "0"] if deterministic else []
        subprocess.run([SEP_PY, str(HERE / "separate.py"), str(path), "--out", str(stem_dir), "--crowd", *shifts],
                       capture_output=True)
    if voc.exists():
        y = read_audio(voc, SR).mean(1)
    else:
        log("  (no separation available; using the full mix for voices)")
        y = read_audio(path, SR).mean(1)
    # An empty successful transcript is a result. Retrying it with another decoder
    # can put words back into a stretch both the mix and the vocal stem marked unvoiced.
    source = voc if voc.exists() else path
    wj = out / "words.json"
    activity_file = out / "speech_activity.json"
    args = [str(HERE / "asr.py"), "speech", str(source), str(path), "--out", str(wj),
            "--activity-out", str(activity_file), "--lang", lang]
    activity = None
    words = []
    for python in dict.fromkeys((ASR_PY, sys.executable)):
        try:
            r = subprocess.run([python, *args], capture_output=True, text=True)
        except OSError as error:
            log(f"  (speech helper unavailable: {error})")
            continue
        if r.returncode == 0:
            words = json.loads(wj.read_text())
            activity = json.loads(activity_file.read_text())
            break
        detail = r.stderr.strip().splitlines()
        log(f"  (speech helper failed: {detail[-1] if detail else r.returncode})")
    else:
        wj.write_text("[]")
        activity_file.unlink(missing_ok=True)
        log("  (no transcript available)")
    S = librosa.feature.melspectrogram(y=y, sr=SR, n_fft=2048, hop_length=HOP, n_mels=40, fmin=80, fmax=8000)
    vdb = librosa.power_to_db(S, ref=1.0, top_db=None).astype(np.float32)
    k = len(y) // HOP
    vrms = 20 * np.log10(np.sqrt((y[:k * HOP].reshape(k, HOP) ** 2).mean(1) + 1e-12) + 1e-9).astype(np.float32)
    y8 = librosa.resample(y, orig_sr=SR, target_sr=8000).astype(np.float32)
    crowd = None
    if (stem_dir / "crowd.wav").exists():
        yc = read_audio(stem_dir / "crowd.wav", SR).mean(1)
        Sc = librosa.feature.melspectrogram(y=yc, sr=SR, n_fft=4096, hop_length=HOP, n_mels=64, fmin=300, fmax=4000)
        crowd = librosa.power_to_db(Sc, ref=1.0, top_db=None).astype(np.float32)
    reps = exact_repeats(y8)
    with_gain = [w for w in words if "gain_db" in w]
    words = [w for w in words if "gain_db" not in w]
    log(f"  voices done, {len(words)} words, {len(with_gain)} candidates after gain, {len(reps)} exact repeats")
    return dict(words=words, speech_with_gain=with_gain, speech_activity=activity,
                vdb=vdb, vrms=vrms, repeats=reps, crowd=crowd)


# ============================================================== quality and timbre

def sharpness_pass(path, dur):
    """How much fine detail the picture holds: fine-scale contrast over mid-scale contrast. Native 1080p footage sits
    near 0.9; 720p broadcast near 0.72; footage blown up from 270p near 0.62. One value per second."""
    out = []
    for i, fr in enumerate(_frames(path, 640, 360, 1)):
        g = cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY).astype(np.float32)
        if g.std() < 10:                                    # flat frames (black, cards) say nothing about detail
            out.append(np.nan); continue
        bm = g[:352].reshape(44, 8, 80, 8)
        lit = bm.mean((1, 3)) >= 16
        if lit.mean() < 0.3 or np.mean(bm.std((1, 3))[lit] < 1.0) > 0.6:
            out.append(np.nan); continue                    # drawn graphics: perfectly flat areas, which a camera never makes
        b1, b4 = cv2.GaussianBlur(g, (0, 0), 1.2), cv2.GaussianBlur(g, (0, 0), 4)
        out.append(float(np.abs(g - b1).mean() / (np.abs(b1 - b4).mean() + 1e-6)))
    return np.array(out, np.float32)


def _frames(path, w, h, fps):
    from common import read_frames
    return read_frames(path, w, h, fps)


GENERIC = {"Music", "Speech", "Inside, small room", "Inside, large room or hall", "Silence", "Sound effect"}


def source_guess(candidates):
    # AST sigmoid scores are multi-label scores, not calibrated probabilities.
    # Apply the same conservative display threshold on pages and in the text stream.
    names = [name.split(",")[0] for name, score in candidates if score >= SOURCE_SCORE]
    return " / ".join(names[:2])


def timbre_pass(path, end, log):
    """Short sound changes and source estimates supported by a longer listening window."""
    try:
        tagger = AudioTagger()
    except (ImportError, OSError, RuntimeError, ValueError) as ex:
        log(f"  (no timbre labels: {ex})"); return [], []
    # Keep the existing short-window downmix. Longer windows use the original channels.
    stop = sample_boundary(end, 16000)
    y = read_audio(path, 16000)[:stop].mean(1).astype(np.float32)
    n = (len(y) + 15999) // 16000
    clips = [y[i * 16000:(i + 1) * 16000] for i in range(n)]
    if clips and len(clips[-1]) < 400:
        clips.pop()  # AST needs a full 25 ms analysis frame.
    labels = tagger.labels
    res = []
    for k in range(0, len(clips), 16):
        pr = tagger.scores(clips[k:k + 16])
        for j, row in enumerate(pr):
            top = [(labels[i], float(row[i])) for i in np.argsort(row)[::-1][:5] if row[i] > 0.08]
            res.append((float(k + j), top))
    log(f"  timbre done, {len(res)} seconds labelled")
    channels = read_audio(path, 16000, channels=None)[:stop]
    return res, recorded_context(tagger, channels, 16000, end)


def hit_pass(path, rms, log):
    """Hits: moments where the level jumps by 15 dB or more within 50 ms to a loud peak, as an impact effect does.
    Each hit is listened to on its own (the second that starts with it) and labelled. Returns [(t, [(label, p)])]."""
    hop = HOP / SR
    hits, last = [], -9
    for i in range(5, len(rms)):
        if rms[i] > -22 and rms[i] - rms[max(i - 5, 0)] >= 15 and i * hop - last > 0.8:
            hits.append(i * hop); last = i * hop
    if not hits:
        return []
    try:
        tagger = AudioTagger(device="cpu")
    except (ImportError, OSError, RuntimeError, ValueError) as ex:
        log(f"  (sound onsets measured without source labels: {ex})")
        return [(t, []) for t in hits]
    y = read_audio(path, 16000).mean(1).astype(np.float32)
    clips = [y[int(t * 16000):int(t * 16000) + 16000] for t in hits]
    clips = [c if len(c) == 16000 else np.pad(c, (0, 16000 - len(c))) for c in clips]
    labels = tagger.labels
    out = []
    for k in range(0, len(clips), 16):
        pr = tagger.scores(clips[k:k + 16])
        for j, row in enumerate(pr):
            top = [(labels[i], float(row[i])) for i in np.argsort(row)[::-1][:6] if labels[i] not in GENERIC and row[i] > 0.04][:2]
            if top and top[0][1] >= 0.5 and any(w in top[0][0] for w in ("speech", "Narration", "Conversation", "Speech", "singing", "Singing")):
                continue                                    # a voice starting is not an impact
            out.append((hits[k + j], top))
    log(f"  {len(out)} sound onsets")
    return out


# ============================================================== what the ear and eye feel as abrupt

def ear_events(a):
    """Moments a listener feels as a jolt, from the 10 ms level and the mel spectrum:
    - "sound cuts dead": the level falls from audible to digital silence within 30 ms (a natural ending rings out);
    - "sound snaps back": it returns from digital silence within 30 ms;
    - "same sound, suddenly +N dB" / "-N dB": the level steps by 7 dB or more while the spectrum keeps its shape,
      i.e. the same sound was turned up or down, not a new sound arriving.
    Returns [(t, label, kind)] with kind in {"dead", "back", "jump"}."""
    r = a["rms"]
    hop = HOP / SR
    ev = []
    n = len(r)
    i = 3
    while i < n - 5:
        if r[i - 3:i].max() > -42 and r[i:i + 5].max() < -65 and r[i - 1] > -60:
            j = i
            while j < n and r[j] < -60:
                j += 1
            ev.append((i * hop, f"sound cuts dead ({(j - i) * hop:.1f} s)", "dead"))
            if j < n - 3 and r[j:j + 3].max() > -42:
                ev.append((j * hop, "sound snaps back", "back"))
            i = j + 1
            continue
        i += 1
    # gain steps: compare 0.5 s before and after, on a 50 ms median level, where the spectral shape stays the same
    from scipy.ndimage import median_filter
    L = median_filter(r, 5)
    db = a["db"]
    k = 50
    last = -9.0
    for i in range(k + 5, n - k - 5, 2):
        before, after = L[i - k:i - 2], L[i + 2:i + k]
        if before.min() < -50 or after.min() < -50:
            continue
        step = float(np.median(after) - np.median(before))
        if abs(step) < 7 or i * hop - last < 1.0:
            continue
        # the step must be sudden: most of it happens within 40 ms
        if abs(float(L[i + 2] - L[i - 2])) < 0.6 * abs(step):
            continue
        sa = db[:, i - k:i - 2].mean(1); sb = db[:, i + 2:i + k].mean(1)
        sa = sa - sa.mean(); sb = sb - sb.mean()
        shape = float((sa * sb).sum() / (np.sqrt((sa * sa).sum() * (sb * sb).sum()) + 1e-9))
        steady = max(float(before.std()), float(after.std())) <= 3.5     # a held sound, not speech or hits coming and going
        if shape > 0.985 and steady and step > 0:            # turned down: a voice ending sounds the same; the bump test covers a fader pulled back
            ev.append((i * hop, f"same sound, suddenly {step:+.0f} dB", "jump"))
            last = i * hop
    bumps = loud_bumps(r, a["db"])
    ev = [e for e in ev if not (e[2] == "jump" and any(abs(e[0] - b[0]) < 0.6 for b in bumps))] + bumps
    return sorted(ev)


def loud_bumps(r, db=None):
    """A stretch of 1 to 10 s that is suddenly much louder than what comes just before and just after it, with sharp
    edges at both ends: someone pushed the fader up and back, whatever the sound is (speech too, which the steady
    'same sound' test cannot follow). Returns [(t, label, "jump")]."""
    hop = HOP / SR
    n = len(r)
    p90 = lambda a_, b_: float(np.percentile(r[max(a_, 0):max(min(b_, n), max(a_, 0) + 1)], 90))
    rises = [i for i in range(3, n - 3) if r[i] - r[i - 2] >= 9 and r[i - 2] > -45]
    out, last_end = [], -1

    def _same_shape(i, j):                                 # turned up, not a new sound (a voice entering) arriving
        if db is None:
            return True
        c = lambda x, y: float(((x - x.mean()) * (y - y.mean())).sum() / (np.sqrt(((x - x.mean()) ** 2).sum() * ((y - y.mean()) ** 2).sum()) + 1e-9))
        ins_ = db[:, i + 5:max(j - 5, i + 6)].mean(1)
        return min(c(db[:, max(0, i - 150):i - 2].mean(1), ins_), c(db[:, j + 2:j + 150].mean(1), ins_)) >= 0.965
    for i in rises:
        if i <= last_end:
            continue
        for L in np.arange(1.0, 10.01, 0.5):
            j = i + int(L / hop)
            if j >= n - 3:
                break
            if r[j - 2] - r[j] >= 9 and r[j] > -50:
                ins, bef, aft = p90(i, j), p90(i - 200, i - 2), p90(j + 2, j + 200)
                if min(ins - bef, ins - aft) >= 6 and max(ins - bef, ins - aft) >= 7 and bef > -40 and aft > -40 and _same_shape(i, j):
                    # the fader moved at the last sharp rise whose 0.7 s before it sits well under the stretch
                    k = max([q for q in rises if i <= q < j - int(0.8 / hop) and p90(q, q + 70) - p90(q - 70, q - 2) >= 7] or [i])
                    out.append((k * hop, f"suddenly {p90(k, j) - p90(k - 200, k - 2):+.0f} dB for {(j - k) * hop:.1f} s, then back", "jump"))
                    last_end = j
                break
    return out


def picture_events(v):
    """How the picture goes to and comes back from black: a fade the eye follows, or a hard cut that jolts it."""
    lu = np.asarray(v["luma"], np.float32)
    edges = frame_edges(v)
    ev = []
    dark = lu < 8
    i = 1
    while i < len(lu):
        if dark[i] and not dark[i - 1]:
            j = i
            while j < len(lu) and dark[j]:
                j += 1
            th_in = [k_ for k_, fi in enumerate(v["th_idx"]) if i <= fi < j]
            has_content = any(v["thumbs"][k_].astype(np.float32).mean(2).std() > 10 for k_ in th_in)
            if edges[j] - edges[i] >= 0.3 and not has_content:
                k = i - 1                                   # how long the way down took
                while k > 0 and lu[k - 1] > lu[k] + 1.5 and edges[i] - edges[k] < 2:
                    k -= 1
                down = edges[i] - edges[k]
                m = j                                       # and the way back up
                while m < len(lu) - 1 and lu[m + 1] > lu[m] + 1.5 and edges[m] - edges[j] < 2:
                    m += 1
                up = edges[m] - edges[j]
                how_in = "fades to black" if down >= 0.25 else "hard cut to black"
                how_out = "still black" if j == len(lu) else ("fades back in" if up >= 0.25 else "hard cut back")
                ev.append((edges[i], edges[j] - edges[i], how_in, how_out))
            i = j
        i += 1
    return ev


def blips(v):
    """A flash or drop-out of one to four frames: the picture jumps away and comes straight back to where it was."""
    lu = np.asarray(v["luma"], np.float32)
    edges = frame_edges(v)
    out, i = [], 1
    while i < len(lu) - 1:
        if abs(lu[i] - lu[i - 1]) > 35:
            for n in range(1, min(5, len(lu) - i)):
                j = i + n
                if abs(lu[j] - lu[i - 1]) < 0.2 * abs(lu[i] - lu[i - 1]) and abs(lu[j] - lu[j - 1]) > 35 \
                        and edges[j] - edges[i] <= 0.25 and np.all(np.abs(lu[i:j] - lu[i]) < 15):
                    out.append((edges[i], edges[j] - edges[i], "white flash" if lu[i] > lu[i - 1] else "dark blink"))
                    i = j
                    break
        i += 1
    return out


def blur_runs(sharp, cuts_s=None):
    """Stretches of 3 s or more whose picture holds far less fine detail than the 10 s around them: under 60 %, or
    under 70 % when the softening starts or ends in the middle of a shot (footage does not lose its focus between
    two frames of one shot; an upscaled insert does)."""
    sh = np.asarray(sharp, np.float32)
    ratio = np.full(len(sh), np.nan, np.float32)
    for t in range(len(sh)):
        if np.isnan(sh[t]):
            continue
        nb = np.r_[sh[max(0, t - 10):max(0, t - 3)], sh[t + 4:t + 11]]
        nb = nb[~np.isnan(nb)]
        if len(nb) >= 5:
            ratio[t] = sh[t] / np.median(nb)
    cuts_s = np.asarray(cuts_s if cuts_s is not None else [], float)
    near_cut = lambda x: len(cuts_s) and bool(np.any(np.abs(cuts_s - x) <= 1.0))
    low = np.nan_to_num(ratio, nan=9) < 0.7
    out, t = [], 0
    while t < len(low):
        if low[t]:
            u = t
            while u < len(low) and low[u]:
                u += 1
            deep = np.nanmedian(ratio[t:u]) < 0.55
            mid = not near_cut(t) or not near_cut(u)
            if u - t >= 3 and (deep or mid):
                out.append((float(t), float(u - t)))
            t = u
        else:
            t += 1
    return out


# ============================================================== does the sound belong to the picture

from config import SYNC_PY


def sync_pass(film, out, log):
    """Run sync.py (Synchformer) along the film; cached in out/sync.json."""
    js = out / "sync.json"
    if not js.exists():
        if not pathlib.Path(SYNC_PY).exists():
            log("  (no sync sense: REEL_SYNC_PYTHON not set up)"); return None
        r = subprocess.run([SYNC_PY, str(HERE / "sync.py"), str(film), "--out", str(js)], capture_output=True, text=True)
        if not js.exists():
            log("  (sync sense failed: " + r.stderr.strip().splitlines()[-1][:200] + ")" if r.stderr.strip() else "  (sync sense failed)")
            return None
    d = json.load(open(js))
    log(f"  sync done, {len(d['windows'])} windows")
    return d


def sync_runs(S):
    """Stretches where the film is sure the sound is off from the picture: two or more neighbouring 5 s windows whose
    belief sits at 0.4 s or more away from in-sync, with little belief left at in-sync. Returns [(t0, t1, late_s)],
    late_s > 0 meaning the sound comes late. Returns [(t0, t1, late_s, sure)]."""
    if not S:
        return []
    g = np.asarray(S["grid"]); hop = S["hop"]
    P_ = np.array([w["p"] for w in S["windows"]])
    Ps = P_.copy()                                          # neighbouring windows overlap by half: hear them together
    if len(P_) >= 3:
        Ps[1:-1] = 0.25 * P_[:-2] + 0.5 * P_[1:-1] + 0.25 * P_[2:]
    flag = []
    for w, p, raw in zip(S["windows"], Ps, P_):
        k = int(np.argmax(p))
        near = p[max(k - 1, 0):k + 2].sum()
        insync = p[np.abs(g) <= 0.21].sum()
        kr = int(np.argmax(raw))                            # one window alone, when it is very sure
        strong = abs(g[kr]) >= 0.39 and raw[kr] >= 0.6 and raw[np.abs(g) <= 0.21].sum() < 0.2
        if strong and not (abs(g[k]) >= 0.39 and near >= 0.45 and insync < 0.25):
            k, near = kr, float(raw[max(kr - 1, 0):kr + 2].sum())
        flag.append((w["t"], strong or (abs(g[k]) >= 0.39 and near >= 0.45 and insync < 0.25), -g[k], near))
    runs, i = [], 0
    while i < len(flag):
        if flag[i][1]:
            j = i
            while j + 1 < len(flag) and (flag[j + 1][1] or (j + 2 < len(flag) and flag[j + 2][1])):
                nxt = flag[j + 1] if flag[j + 1][1] else flag[j + 2]
                if abs(nxt[2] - flag[j][2]) > 0.35 and flag[j][1]:   # the offset itself changes: a new stretch
                    break
                j += 1
            hits = [f for f in flag[i:j + 1] if f[1]]
            raw_sure = sum(1 for q in range(i, j + 1) if flag[q][1] and float(np.max(P_[q])) >= 0.45)
            if len(hits) >= 2 and (max(f[3] for f in hits) >= 0.8 or raw_sure >= 2):
                late = float(np.median([f[2] for f in hits]))
                sure = max(f[3] for f in hits) >= 0.8
                runs.append((flag[i][0] + 1.25, flag[j][0] + S["win"] - 1.25, late, sure))
            i = j + 1
        else:
            i += 1
    return runs


# ============================================================== memory

def exact_repeats(y, sr=8000, lo=0.3, hi=3.0, thr=0.9):
    """Stretches whose sound is a sample-for-sample copy of what played lo..hi seconds earlier. Music loops in a mix
    almost never are; an edit that replays a second of footage always is. Returns [(t, dur, lag, corr)]."""
    from scipy.signal import fftconvolve
    win, hop = int(0.5 * sr), int(0.1 * sr)
    L0, L1 = int(lo * sr), int(hi * sr)
    c2 = np.concatenate([[0.0], np.cumsum(y.astype(np.float64) ** 2)])
    hits = []
    for s0 in range(L1, len(y) - win, hop):
        a = y[s0:s0 + win]
        ea = math.sqrt(float((a * a).sum()))
        if ea < 1e-3 * math.sqrt(win):
            continue
        seg = y[s0 - L1:s0 - L0 + win]
        num = fftconvolve(seg, a[::-1], mode="valid")              # num[k] = <seg[k:k+win], a>
        starts = np.arange(len(num))
        eb = np.sqrt(np.maximum(c2[s0 - L1 + starts + win] - c2[s0 - L1 + starts], 1e-12))
        cc = num / (ea * eb)
        k = int(np.argmax(cc))
        if cc[k] > thr:
            hits.append((s0 / sr, (L1 - k) / sr, float(cc[k])))
    out = []
    for t, lag, c in hits:
        if out and t - (out[-1][0] + out[-1][1]) <= 0.15 and abs(lag - out[-1][2]) < 0.05:
            out[-1][1] = t + 0.5 - out[-1][0]; out[-1][3] = max(out[-1][3], c)
        else:
            out.append([t, 0.5, lag, c])
    return [tuple(x) for x in out]


def memory_picture(v, cuts):
    """For every 4-fps picture: the earlier moment (in a different shot, at least 1 s back) that looks the same.
    A flash or blink of a few frames is not a new shot: the picture before and after it is one continuous shot."""
    fl = blips(v)
    if fl:
        edges = frame_edges(v)
        span = [(edges.searchsorted(tb) - 1, edges.searchsorted(tb + du - 1e-9) + 1) for tb, du, _ in fl]
        cuts = np.array([c for c in cuts if not any(a_ <= c <= b_ for a_, b_ in span)], dtype=np.asarray(cuts).dtype)
    h = v["hashes"].reshape(len(v["hashes"]), -1)
    sd = h.std(1)
    z = (h - h.mean(1, keepdims=True)) / (sd[:, None] + 1e-6)
    z /= np.sqrt(z.shape[1])
    sim = z @ z.T
    t = thumbnail_times(v)
    shot = np.searchsorted(cuts, v["th_idx"], side="right")
    best_t = np.full(len(t), np.nan, np.float32); best_s = np.zeros(len(t), np.float32)
    for i in range(len(t)):
        if sd[i] < 8:                                         # flat frames (black, white) do not count as memories
            continue
        ok = (t < t[i] - 1.0) & (shot != shot[i]) & (sd >= 8)
        if not ok.any():
            continue
        j = int(np.argmax(np.where(ok, sim[i], -1)))
        if sim[i, j] > 0.965:
            best_t[i], best_s[i] = t[j], sim[i, j]
    return dict(t=t, src=best_t, score=best_s)


def memory_voice(vo):
    """For every 0.1 s: the earlier moment (at least 0.6 s back) whose voices sound the same."""
    db = vo["vdb"]
    n = db.shape[1]
    hop, win = 10, 50
    idx = np.arange(0, n - win, hop)
    feats, live = [], []
    for i in idx:
        p = db[:, i:i + win].reshape(40, 10, 5).mean(2)
        e = float(vo["vrms"][i:i + win].mean()) if i + win <= len(vo["vrms"]) else -90
        live.append(e > -45)
        p = p - p.mean()
        feats.append((p / (np.linalg.norm(p) + 1e-6)).ravel())
    F = np.array(feats, np.float32)
    live = np.array(live)
    t = idx * HOP / SR
    sim = F @ F.T
    best_t = np.full(len(t), np.nan, np.float32); best_s = np.zeros(len(t), np.float32)
    for i in range(len(t)):
        if not live[i]:
            continue
        ok = (t < t[i] - 3.0) & live
        if not ok.any():
            continue
        j = int(np.argmax(np.where(ok, sim[i], -1)))
        if sim[i, j] > 0.93:
            best_t[i], best_s[i] = t[j], sim[i, j]
    # keep only runs of 3 or more windows (0.3 s): single coincidences are not memories
    keep = ~np.isnan(best_t)
    run = np.zeros(len(t), bool)
    i = 0
    while i < len(t):
        if keep[i]:
            j = i
            while j < len(t) and keep[j]:
                j += 1
            if j - i >= 3:
                run[i:j] = True
            i = j
        else:
            i += 1
    best_t[~run] = np.nan
    return dict(t=t + win * HOP / SR / 2, src=best_t, score=best_s)


# ============================================================== reading

def ocr_pass(path, dur, log, step=1.0, subject=None):
    from ocrmac import ocrmac
    W, H = 1920, 1080
    scale = display_scale(path, f"{W}:{H}")
    stream = f"0:{probe(path)['video_index']}"
    times = np.arange(0.25, dur, step)
    tags = []
    def one(t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-map", stream, "-frames:v", "1", "-vf",
                              scale, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        if len(raw) < W * H * 3:
            return t, []
        img = Image.fromarray(np.frombuffer(raw, np.uint8).reshape(H, W, 3))
        try:
            r = ocrmac.OCR(img, language_preference=["ko-KR", "en-US"], recognition_level="accurate").recognize()
        except Exception:
            r = []
        out = []
        for text, conf, box in r:
            text = text.strip()
            if subject and any(al.lower() in text.lower() for al in subject.split(",")) and len(text) <= 14 and box[3] < 0.032:
                tags.append(dict(t=float(t), x=box[0] + box[2] / 2, y=1 - box[1] - box[3] / 2))   # the subject's name tag
            if conf < 0.9 or box[3] < 0.032:                         # what a viewer reads: big enough to notice
                continue
            letters = sum(ch.isalpha() for ch in text)
            hangul = sum("가" <= ch <= "힣" for ch in text)
            dated = re.search(r"(19|20)\d\d", text) is not None              # years and dates carry the story
            if hangul < 2 and letters < 4 and not dated:           # bare numbers and HUD fragments are not read
                continue
            out.append(dict(text=text, x=box[0], y=1 - box[1] - box[3], h=box[3], c=conf))
        return t, out
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(one, times))
    # merge the same text across consecutive samples into one item
    items, open_ = [], {}
    for t, found in res:
        seen = set()
        for f in found:
            key = (f["text"], round(f["x"], 1), round(f["y"], 1))
            seen.add(key)
            if key in open_:
                open_[key]["e"] = t + step
            else:
                open_[key] = dict(t=t, e=t + step, text=f["text"], y=f["y"], h=f["h"], c=f["c"])
        for key in list(open_):
            if key not in seen and open_[key]["e"] < t:
                items.append(open_.pop(key))
    items += list(open_.values())
    items = [it for it in items if it["e"] - it["t"] > step * 1.5 or (len(it["text"]) >= 6 and it.get("c", 1) >= 1.0)]
    items.sort(key=lambda d: d["t"])
    log(f"  on-screen text done, {len(items)} items, {len(tags)} sightings of {subject}")
    return items, sorted(tags, key=lambda d: d["t"])


# ============================================================== pages

def chant_windows(crowd_db, hop=0.01):
    """Where the crowd voices pulse at a steady beat (0.45-1.6 s), repeating at least twice: a chant, as a listener
    hears it, whether or not its words can be made out. Returns [(t0, t1, period)]."""
    if crowd_db is None or crowd_db.size == 0:
        return []
    e = crowd_db.mean(0)
    on = np.maximum(np.diff(e, prepend=e[0]), 0)
    ref = np.percentile(e, 99)
    W, H = int(4 / hop), int(0.5 / hop)
    out = []
    for s0 in range(0, len(on) - W, H):
        x = on[s0:s0 + W] - on[s0:s0 + W].mean()
        ac = np.correlate(x, x, "full")[W - 1:]
        ac /= ac[0] + 1e-9
        lo, hi = int(0.45 / hop), int(1.6 / hop)
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] > 0.40 and ac[min(2 * k, W - 1)] > 0.2 and np.percentile(e[s0:s0 + W], 90) > ref - 40:
            if out and s0 * hop <= out[-1][1]:
                out[-1] = (out[-1][0], s0 * hop + 4, k * hop)
            else:
                out.append((s0 * hop, s0 * hop + 4, k * hop))
    return out


GENRE_NAMES = {"lol": "T1,젠지,Gen.G,DRX,한화생명,HLE,KT,담원,DK,BLG,IG,JDG,G2,페이커,제우스,쵸비,케리아,구마유시,오너,룰러,캐니언,기인,"
                      "데프트,쇼메이커,LCK,롤드컵"}


def hear_chants(out, chants, ocr, genre, lang, log):
    """For each chant window, what the crowd is shouting, matched by sound to names the film shows (and the genre's)."""
    hear_py = pathlib.Path(ASR_PY)
    crowd = out / "stems/crowd.wav"
    if not chants or not crowd.exists():
        return []
    names = set(GENRE_NAMES.get(genre, "").split(","))
    for it in ocr:                                        # proper names shown on screen: team tags and player names
        for w_ in re.findall(r"\b[A-Z][A-Za-z0-9.]{1,6}\b", it["text"]):
            names.add(w_)
    wj = out / "chants.json"
    r = subprocess.run([str(hear_py), str(HERE / "asr.py"), "chant", str(crowd), "--windows",
                        ",".join(f"{a:.1f}:{b:.1f}" for a, b, _ in chants), "--names", ",".join(sorted(n for n in names if n)),
                        "--out", str(wj), "--lang", lang], capture_output=True, text=True)
    res = json.load(open(wj)) if r.returncode == 0 and wj.exists() else []
    # what the whole film's chants add up to: the name most windows land closest to
    votes = {}
    for c in res:
        if c["closest"] and c["repeats"] >= 3:
            votes[c["closest"][0]["name"]] = votes.get(c["closest"][0]["name"], 0) + 1
    if votes:
        top = max(votes, key=votes.get)
        for c in res:
            c["film_vote"] = f"{top} ({votes[top]} of {len(res)} chants)"
    log(f"  chants heard: {[c['heard'] for c in res]} -> {votes}")
    return res


class Page:
    def __init__(self, t0, t1, rows, title):
        self.t0, self.t1 = t0, t1
        self.y, y = {}, 26
        for name, h in rows:
            self.y[name] = (y, h); y += h + 4
        self.img = Image.new("RGB", (LEFT + PW + 8, y + 2), BG)
        self.d = ImageDraw.Draw(self.img, "RGBA")
        self.d.text((6, 4), title, font=font(13), fill=(235, 235, 235))

    def x(self, t):
        return LEFT + (t - self.t0) / (self.t1 - self.t0) * PW

    def cols(self, ts):
        return np.clip(((ts - self.t0) / (self.t1 - self.t0) * PW).astype(int), -1, PW)

    def label(self, name, a, b=None):
        y, h = self.y[name]
        self.d.text((4, y + 1), a, font=font(10), fill=(185, 190, 200))
        if b:
            self.d.text((4, y + 13), b, font=font(9), fill=(120, 125, 135))

    def box(self, name):
        y, h = self.y[name]
        self.d.rectangle([LEFT, y, LEFT + PW, y + h], fill=(20, 22, 27))


def pooled(values, ts, pg, how="max"):
    """One value per pixel column from a dense series, so nothing in between is lost."""
    c = pg.cols(ts)
    out = np.full(PW, np.nan, np.float32)
    m = (c >= 0) & (c < PW)
    if not m.any():
        return out
    cc, vv = c[m], values[m]
    if how == "max":
        np.fmax.at(out, cc, vv)
    else:
        s = np.zeros(PW); n = np.zeros(PW)
        np.add.at(s, cc, vv); np.add.at(n, cc, 1)
        out = np.where(n > 0, s / np.maximum(n, 1), np.nan).astype(np.float32)
    return out


def band_levels(db, n_fft, fmin, fmax):
    """Mel band power relative to a full-scale digital sample, using the stored Slaney/Hann analysis."""
    edges = librosa.mel_frequencies(n_mels=len(db) + 2, fmin=fmin, fmax=fmax)
    # Undo Slaney area normalization and account for a one-sided power spectrum.
    # A periodic Hann window of length N has squared sum 3N/8. Both filter banks exclude DC/Nyquist.
    scale = (edges[2:] - edges[:-2]) / (n_fft * (3 * n_fft / 8))
    return db + (10 * np.log10(scale)).astype(np.float32)[:, None]


def draw_spectrum(pg, lane, db, n_fft, fmin, fmax, end, palette):
    """Draw on a fixed -80..0 dBFS scale. A narrow, strong bin survives time compression."""
    rate = SR / HOP
    stop = min(pg.t1, end, db.shape[1] / rate)
    if stop <= pg.t0:
        return
    width = min(PW, math.ceil(pg.x(stop) - LEFT))
    times = np.minimum(stop, pg.t0 + np.arange(width + 1) * (pg.t1 - pg.t0) / PW)
    ticks = np.round(times * rate, 9)
    starts = np.minimum(np.floor(ticks[:-1]).astype(int), db.shape[1] - 1)
    stops = np.maximum(starts + 1, np.ceil(ticks[1:]).astype(int))
    # Pool every time bin overlapping a pixel, including when a zoom makes a bin several pixels wide.
    bands = np.stack([db[:, a:b].max(axis=1) for a, b in zip(starts, stops)], axis=1)
    levels = band_levels(bands, n_fft, fmin, fmax)
    y, height = pg.y[lane]
    colors = np.clip((levels[::-1] + 80) / 80, 0, 1)
    pixels = cv2.resize(colors, (width, height), interpolation=cv2.INTER_AREA)
    pg.img.paste(Image.fromarray(lut(palette)[(pixels * 255).astype(np.uint8)]), (LEFT, y))


def draw_picture_frames(pg, v, cuts_t, end):
    """Place separate, uncropped stills inside the time span of their shot."""
    y, height = pg.y["picture"]
    times = thumbnail_times(v)
    aspect = float(v.get("display_aspect", TW / TH))
    # Leave the quality and black-bar annotations outside the frame.
    top, bottom = y + PICTURE_TOP, y + height - PICTURE_FOOT
    visible = max(pg.t0, times[0]) if len(times) else end
    bounds = [visible, *cuts_t[(cuts_t > visible) & (cuts_t < end)], end]
    for start, stop in zip(bounds, bounds[1:]):
        left, right = round(pg.x(start)), round(pg.x(stop))
        if right <= left:
            continue
        baseline = bottom + 14
        pg.d.line((left, baseline - 3, left, baseline, right - 1, baseline), fill=(83, 89, 100))
        first, last = times.searchsorted([start, stop])
        if first == last:
            # The page can start between cached frames. Only carry a still from this shot.
            previous_cut = cuts_t.searchsorted(start, side="right") - 1
            shot_start = cuts_t[previous_cut] if previous_cut >= 0 else 0
            if first == 0 or times[first - 1] < shot_start:
                continue
            first -= 1
        count = min(last - first, math.ceil((right - left) / 178))
        edges = np.linspace(left, right, count + 1).round().astype(int)
        for x0, x1 in zip(edges[:-1], edges[1:]):
            center = (x0 + x1) / 2
            target = pg.t0 + (center - LEFT) * (pg.t1 - pg.t0) / PW
            index = first + np.argmin(np.abs(times[first:last] - target))
            space = max(1, x1 - x0 - 4)
            w = max(1, min(space, round((bottom - top) * aspect)))
            h = max(1, min(bottom - top, round(w / aspect)))
            frame = Image.fromarray(v["thumbs"][index]).resize((w, h), Image.Resampling.LANCZOS)
            pg.img.paste(frame, (int((x0 + x1 - w) // 2), top + (bottom - top - h) // 2))
            tick = max(left, min(right - 1, round(pg.x(times[index]))))
            pg.d.line((tick, baseline - 2, tick, baseline), fill=(176, 184, 197))
            label = tc(times[index])
            if font(10).getlength(label) <= space:
                pg.d.text((center, bottom), label, font=font(10), anchor="mt", fill=(190, 198, 209))
    pg.label("picture", "frames", "over time")


def render_page(F, t0, t1, idx, total, name):
    v, a, vo = F["v"], F["a"], F["vo"]
    rows = [("ruler", 12), ("picture", TH + PICTURE_TOP + PICTURE_FOOT), ("eye", 18), ("read", 40), ("change", 46), ("seen", 16),
            ("sound", 110), ("like", 52), ("level", 36)]
    channel_rms = a.get("channel_rms", ())
    if len(channel_rms) > 1:
        rows.append(("channels", 16 * len(channel_rms)))
    if len(a.get("stereo_pairs", ())):
        rows.append(("earlier", 26 * len(a["stereo_pairs"])))
    rows.extend([("sync", 42), ("crowd", 44), ("said", 34), ("heard", 22)])
    pg = Page(t0, t1, rows, f"{name}   reel page {idx}/{total}   {tc(t0)} - {tc(t1)}   (read pages in order)")
    d = pg.d
    # ruler
    y, h = pg.y["ruler"]
    t = math.ceil(t0)
    while t <= t1:
        x = pg.x(t)
        big = t % 5 == 0
        d.line([(x, y + (0 if big else 6)), (x, y + h)], fill=(150, 150, 160) if big else (80, 80, 90))
        if big:
            d.text((x + 2, y - 1), tc(t)[:-3], font=font(10), fill=(175, 180, 190))
        t += 1
    edges = frame_edges(v)
    fr_t = edges[:-1]
    cuts_t = fr_t[F["cuts"]]
    y, h = pg.y["picture"]
    th_t = thumbnail_times(v)
    draw_picture_frames(pg, F.get("detail_pictures", v), cuts_t, min(t1, F["dur"], edges[-1]))
    # frame shape, drawn as a thin strip under the pictures: grey = picture fills the frame, orange = black bars
    # or a boxed picture (rows or columns that are uniformly dark)
    yb = y + h - 6
    boxed_prev = False
    label_free = -1e9
    boxed_flags = {}
    for j, tt in enumerate(th_t):
        if t0 - 3 <= tt < t1 + 3:
            g_ = v["thumbs"][j].astype(np.float32).mean(2)
            rb = (g_.mean(1) < 16) & (g_.std(1) < 6); cb = (g_.mean(0) < 16) & (g_.std(0) < 6)
            boxed_flags[j] = ((1 - rb.mean()) < 0.9 or (1 - cb.mean()) < 0.9) and g_.mean() >= 12
    for j, tt in enumerate(th_t):
        if not (t0 <= tt < t1):
            continue
        g = v["thumbs"][j].astype(np.float32).mean(2)
        rbar = (g.mean(1) < 16) & (g.std(1) < 6)           # true bars are flat black; a vignette keeps texture
        cbar = (g.mean(0) < 16) & (g.std(0) < 6)
        fill_h, fill_w = 1 - rbar.mean(), 1 - cbar.mean()
        dark = g.mean() < 12
        boxed = (fill_h < 0.9 or fill_w < 0.9) and not dark
        x0 = pg.x(tt); x1 = pg.x(min(tt + 1 / THUMB_FPS, t1))
        d.rectangle([x0, yb, x1, yb + 5], fill=(255, 150, 40) if boxed else (60, 60, 66))
        run_len = 0
        k_ = j
        while boxed and boxed_flags.get(k_, False) and run_len < 8:
            run_len += 1; k_ += 1
        if boxed and not boxed_prev and run_len >= 8 and pg.x(tt) > label_free:
            lab = f"picture fills {int(fill_w * 100)}% wide x {int(fill_h * 100)}% high"
            label_free = pg.x(tt) + font(10).getlength(lab) + 40
            d.rectangle([x0, yb - 15, x0 + font(10).getlength(lab) + 6, yb - 1], fill=(0, 0, 0))
            d.text((x0 + 3, yb - 14), lab, font=font(10), fill=(255, 190, 90))
        boxed_prev = boxed
    pg.box("eye")
    _eye_lane(pg, F)
    pg.label("eye", "subject")
    # picture quality: a thin strip, blue where the picture holds less fine detail than native HD footage
    sh = F.get("sharp")
    if sh is not None and len(sh):
        yq = pg.y["picture"][0]
        prev_soft = False
        for k_, val in enumerate(sh):
            tt = float(k_)
            if not (t0 <= tt < t1) or np.isnan(val):
                prev_soft = False
                continue
            soft = val < 0.72
            colr = (90, 140, 255) if val < 0.65 else (150, 170, 230) if soft else (50, 52, 58)
            d.rectangle([pg.x(tt), yq, pg.x(min(tt + 1, t1)), yq + 4], fill=colr)
            if soft and not prev_soft and pg.x(tt) > getattr(pg, "_qfree", -1e9):
                pg._qfree = pg.x(tt) + 220
                lab = "soft picture (low detail)" if val < 0.65 else "slightly soft picture"
                fk = font(10)
                d.rectangle([pg.x(tt), yq + 5, pg.x(tt) + fk.getlength(lab) + 6, yq + 18], fill=(0, 0, 0))
                d.text((pg.x(tt) + 3, yq + 5), lab, font=fk, fill=(150, 180, 255))
            prev_soft = soft
    if sh is not None and len(sh):
        for tb, du in blur_runs(sh, cuts_t):
            if tb + du < t0 or tb >= t1:
                continue
            xa, xb = pg.x(max(tb, t0)), pg.x(min(tb + du, t1))
            yq = pg.y["picture"][0]
            d.rectangle([xa, yq, xb, yq + 4], fill=(255, 90, 200))
            lab = f"much softer than around it, {du:.0f} s"
            fk = font(10, True)
            d.rectangle([xa, yq + 19, xa + fk.getlength(lab) + 6, yq + 32], fill=(60, 10, 50))
            d.text((xa + 3, yq + 19), lab, font=fk, fill=(255, 170, 230))
    # what can be read on screen
    pg.box("read")
    y, h = pg.y["read"]
    lanes = [-1e9, -1e9]
    for it in F["ocr"]:
        if it["e"] < t0 or it["t"] > t1:
            continue
        xs = pg.x(max(it["t"], t0))
        if lanes[0] <= xs:
            k = 0
        elif lanes[1] <= xs:
            k = 1
        else:
            continue
        txt = it["text"][:60]
        f = font(12, True)
        d.line([(xs, y + 4 + k * 18), (min(pg.x(min(it["e"], t1)), LEFT + PW), y + 4 + k * 18)], fill=(120, 170, 255, 160), width=2)
        d.text((xs + 2, y + 5 + k * 18), txt, font=f, fill=(225, 230, 240))
        lanes[k] = xs + f.getlength(txt) + 8
    pg.label("read", "on screen", "text")
    # change: how much the picture changes from frame to frame (max per column, so one frame shows), brightness line
    pg.box("change")
    y, h = pg.y["change"]
    ch = pooled(np.log1p(v["mad"]), fr_t, pg, "max")
    lu = pooled(v["luma"], fr_t, pg, "mean")
    hold = np.zeros(len(v["mad"]), np.float32)
    starts = np.r_[0, np.flatnonzero(v["mad"][1:] >= 0.05) + 1]
    for i, j in zip(starts, np.r_[starts[1:], len(hold)]):
        if edges[j] - edges[i] >= 0.5:
            hold[i:j] = 1
    still = pooled(hold, fr_t, pg, "mean")
    # Between presentations the previous frame remains on screen, even across a long VFR hold.
    columns = t0 + (np.arange(PW) + 0.5) * (t1 - t0) / PW
    active = np.searchsorted(edges, columns, side="right") - 1
    missing = np.isnan(lu) & (active >= 0) & (active < len(fr_t))
    lu[missing] = v["luma"][active[missing]]
    still[missing] = hold[active[missing]]
    top = math.log1p(60)
    for xpx in range(PW):
        if not np.isnan(ch[xpx]):
            hh = min(ch[xpx] / top, 1) * (h - 2)
            d.line([(LEFT + xpx, y + h - 1), (LEFT + xpx, y + h - 1 - hh)], fill=(235, 235, 240))
        if not np.isnan(still[xpx]) and still[xpx] > 0.9 and not np.isnan(lu[xpx]) and lu[xpx] > 8:
            d.line([(LEFT + xpx, y), (LEFT + xpx, y + 5)], fill=(255, 90, 90))       # picture not moving at all
    polyline(d, LEFT + np.arange(PW), y + h - 1 - np.nan_to_num(lu, nan=np.nan) / 255 * (h - 2), (250, 215, 90), 1)
    pg.label("change", "change", "bright")
    if "blips" not in F:
        F["blips"] = blips(v)
    y, h = pg.y["change"]
    for tb, du, kind in F["blips"]:
        if t0 <= tb < t1:
            xs = pg.x(tb)
            lab = f"{kind}, {du:.2f} s"
            fk = font(10, True)
            d.rectangle([xs + 3, y + 14, xs + fk.getlength(lab) + 9, y + 27], fill=(70, 60, 10))
            d.text((xs + 6, y + 14), lab, font=fk, fill=(255, 240, 150))
    if "pic_ev" not in F:
        F["pic_ev"] = picture_events(v)
    y, h = pg.y["change"]
    for tb, dur_, how_in, how_out in F["pic_ev"]:
        if tb + dur_ < t0 or tb >= t1:
            continue
        lab = f"{how_in}, {dur_:.1f} s, {how_out}"
        fk = font(10, True)
        hard = "hard" in how_in or "hard" in how_out
        xs = pg.x(max(tb, t0))
        d.rectangle([xs + 2, y + h - 15, xs + fk.getlength(lab) + 8, y + h - 2], fill=(90, 10, 10) if hard else (20, 30, 50))
        d.text((xs + 5, y + h - 15), lab, font=fk, fill=(255, 190, 190) if hard else (170, 200, 255))
    # seen before
    pg.box("seen")
    mp, mv = F["mem_pic"], F["mem_voice"]
    if mv.get("score") is None or mp.get("score") is None:
        F["mem_voice"] = mv = memory_voice(vo) if mv.get("score") is None else mv
        F["mem_pic"] = mp = memory_picture(v, F["cuts"]) if mp.get("score") is None else mp
    ex_v = exact_replays(mv)
    ex_p = []                                               # the picture is the same footage when it matches at the voice's lag
    for ea, eb, es in ex_v:
        sel = (mp["t"] >= ea) & (mp["t"] < eb) & ~np.isnan(mp["src"])
        if sel.sum() and np.median(np.abs((mp["t"][sel] - mp["src"][sel]) - (ea - es))) < 0.4:
            ex_p.append((ea, eb, es))
    F["_exact"] = (ex_v, ex_p)
    _memory_lane(pg, "seen", mp["t"], mp["src"], (110, 200, 255), ex_p)
    pg.label("seen", "seen again")
    # sound: spectrogram with the sound-scene changes marked
    y, h = pg.y["sound"]
    draw_spectrum(pg, "sound", a["db"], 2048, 30, 16000, F["dur"], "magma")
    for ct in a["changes"]:
        if t0 <= ct <= t1:
            d.polygon([(pg.x(ct), y + 9), (pg.x(ct) - 5, y), (pg.x(ct) + 5, y)], fill=(80, 255, 160))
    pg.label("sound", "sound", "30 Hz-16k")
    # what it sounds like: AudioSet labels per second, written when they change
    pg.box("like")
    y, h = pg.y["like"]
    last_x, last_lab = [-1e9, -1e9], None
    for tt, top in F.get("timbre", []):
        if not t0 <= tt < t1:
            continue
        guess = source_guess(top)
        lab = "resembles " + guess if guess else "source unclear"
        if not guess:
            levels = a["rms"][round(tt * SR / HOP):round((tt + 1) * SR / HOP)]
            if not len(levels) or levels.max() < -60:
                continue
        if lab == last_lab:
            continue
        xs = pg.x(tt)
        k = 0 if last_x[0] <= xs else 1 if last_x[1] <= xs else None
        if k is None:
            continue
        fk = font(11, True)
        d.text((xs + 1, y + 2 + k * 16), lab, font=fk, fill=(255, 225, 150) if guess else (150, 150, 160))
        last_x[k] = xs + fk.getlength(lab) + 8
        last_lab = lab
    # hits: each impact, heard on its own
    for tt, top in F.get("hits", []):
        if not t0 <= tt < t1:
            continue
        xs = pg.x(tt)
        d.polygon([(xs, y + h - 1), (xs - 4, y + h - 8), (xs + 4, y + h - 8)], fill=(255, 120, 80))
    pg.label("like", "sounds", "like")
    # hit labels go on a line of their own just under the lane. Hits close together are told as one group,
    # counted by what each one sounds like, so no hit is hidden behind its neighbour's label.
    hs = [(tt, top) for tt, top in F.get("hits", []) if t0 <= tt < t1]
    groups = []
    for tt, top in hs:
        if groups and tt - groups[-1][-1][0] < 3.0:
            groups[-1].append((tt, top))
        else:
            groups.append([(tt, top)])
    free = -1e9
    fk = font(10)
    for g in groups:
        names = [("resembles " + guess) if (guess := source_guess(top)) else "source unclear" for _, top in g]
        if len(g) == 1:
            lab = "sharp rise: " + names[0]
        else:
            cnt = {}
            for n_ in names:
                cnt[n_] = cnt.get(n_, 0) + 1
            lab = f"{len(g)} sharp rises: " + ", ".join(f"{k} x{v}" if v > 1 else k for k, v in sorted(cnt.items(), key=lambda kv: -kv[1]))
        xs = max(pg.x(g[0][0]), free)
        if xs > pg.x(t1) - 40:
            continue
        d.rectangle([xs - 2, y + h - 22, xs + fk.getlength(lab) + 4, y + h - 9], fill=(40, 16, 10))
        d.text((xs + 1, y + h - 22), lab, font=fk, fill=(255, 150, 110))
        free = xs + fk.getlength(lab) + 10
    # level: 10 ms loudness, max per column, so a cut to silence shows as a cliff
    pg.box("level")
    y, h = pg.y["level"]
    lv = pooled(a["rms"], np.arange(len(a["rms"])) * HOP / SR, pg, "max")
    for xpx in range(PW):
        if not np.isnan(lv[xpx]):
            hh = np.clip((lv[xpx] + 60) / 60, 0, 1) * (h - 2)
            d.line([(LEFT + xpx, y + h - 1), (LEFT + xpx, y + h - 1 - hh)], fill=(120, 200, 255))
    pg.label("level", "level", "-60..0 dB")
    if len(channel_rms) > 1:
        channel_y, _ = pg.y["channels"]
        times = np.arange(channel_rms.shape[1]) * HOP / SR
        for i, levels in enumerate(channel_rms):
            top = channel_y + 16 * i
            name = ("L level", "R level")[i] if len(channel_rms) == 2 else f"ch {i + 1}"
            d.text((4, top + 1), name, font=font(10), fill=(185, 190, 200))
            heights = np.clip((pooled(levels, times, pg) + 60) / 60, 0, 1) * 13
            d.line((LEFT, top + 14, LEFT + PW, top + 14), fill=(45, 49, 54))
            for col in np.flatnonzero(heights >= 1):
                d.line((LEFT + col, top + 13, LEFT + col, top + 14 - heights[col]), fill=(120, 200, 255))
    if "earlier" in pg.y:
        pg.box("earlier")
        top, _ = pg.y["earlier"]
        ends = a["stereo_end"]
        for i, (first, second) in enumerate(a["stereo_pairs"]):
            mid = top + 26 * i + 12
            d.text((4, mid - 12), f"{first} early", font=font(10), fill=(130, 205, 245))
            d.text((4, mid), f"{second} early", font=font(10), fill=(246, 180, 105))
            for j in range(max(0, math.floor(t0 * 4)), min(len(ends), math.ceil(t1 * 4))):
                lag = a["stereo_us"][i, j]
                if np.isfinite(lag) and ends[j] > t0:
                    offset = math.copysign(2 + 8 * abs(lag) / 1000, lag) if lag else 0
                    color = (130, 205, 245) if lag > 0 else (246, 180, 105) if lag < 0 else (165, 168, 175)
                    left, right = pg.x(max(t0, j / 4)), pg.x(min(t1, ends[j]))
                    d.line((left, mid - offset, right, mid - offset), fill=color, width=2)
    if "ear" not in F:
        F["ear"] = ear_events(a)
    free = -1e9
    for tt, lab, kind in F["ear"]:
        if not (t0 <= tt < t1):
            continue
        xs = pg.x(tt)
        d.line([(xs, y), (xs, y + h - 1)], fill=(255, 70, 70), width=2)
        if xs < free:
            continue
        fk = font(10, True)
        d.rectangle([xs + 2, y + 1, xs + fk.getlength(lab) + 8, y + 14], fill=(90, 10, 10))
        d.text((xs + 5, y + 1), lab, font=fk, fill=(255, 190, 190))
        free = xs + fk.getlength(lab) + 12
    # sync: belief over where the sound sits against the picture, from 2 s early (top) to 2 s late (bottom)
    pg.box("sync")
    y, h = pg.y["sync"]
    S = F.get("sync")
    if S and S["windows"]:
        g = np.asarray(S["grid"]); W_ = S["win"]
        cen = np.array([w["t"] + W_ / 2 for w in S["windows"]]); P_ = np.array([w["p"] for w in S["windows"]])
        img = np.zeros((len(g), PW), np.float32)
        for xpx in range(PW):
            tt = t0 + (xpx + 0.5) / PW * (t1 - t0)
            if tt > F["dur"]:
                break
            k = int(np.searchsorted(cen, tt))
            if k <= 0:
                col = P_[0]
            elif k >= len(cen):
                col = P_[-1]
            else:
                a_ = (tt - cen[k - 1]) / (cen[k] - cen[k - 1]); col = P_[k - 1] * (1 - a_) + P_[k] * a_
            img[:, xpx] = col
        z = np.clip(img / 0.6, 0, 1)                       # row 0 = sound 2 s early ... last row = sound 2 s late
        z = z[::-1] if g[0] < g[-1] else z                  # top row: sound early; bottom row: sound late
        hm = cv2.resize(z, (PW, h - 2), interpolation=cv2.INTER_NEAREST)
        pg.img.paste(Image.fromarray(lut("magma")[(hm * 255).astype(np.uint8)]), (LEFT, y + 1))
        ym = y + 1 + (h - 2) / 2
        d.line([(LEFT, ym), (LEFT + PW, ym)], fill=(90, 200, 120), width=1)
        for ra, rb, late, sure in sync_runs(S):
            if rb < t0 or ra > t1:
                continue
            xa, xb = pg.x(max(ra, t0)), pg.x(min(rb, t1))
            d.rectangle([xa, y, xb, y + 2], fill=(255, 60, 60) if sure else (230, 140, 60))
            lab = f"sound {abs(late):.1f} s {'late' if late > 0 else 'early'} ({'sure' if sure else 'likely'})"
            fk = font(11, True)
            d.rectangle([xa + 2, y + 4, xa + fk.getlength(lab) + 8, y + 18], fill=(110, 10, 10))
            d.text((xa + 5, y + 4), lab, font=fk, fill=(255, 210, 210))
    else:
        d.text((LEFT + 6, y + 12), "(not measured)", font=font(10), fill=(110, 110, 120))
    pg.label("sync", "in sync?", "early/late")
    # crowd: the crowd separated from music and commentary, voice band; a chant shows as arcs repeating at a steady beat
    pg.box("crowd")
    if vo.get("crowd") is not None:
        draw_spectrum(pg, "crowd", vo["crowd"], 4096, 300, 4000, F["dur"], "inferno")
    if "chants" not in F:
        F["chants"] = chant_windows(vo.get("crowd"))
    y, h = pg.y["crowd"]
    for ca, cb, per in F["chants"]:
        if cb < t0 or ca > t1:
            continue
        xa = pg.x(max(ca, t0)); xb = pg.x(min(cb, t1))
        d.rectangle([xa, y, xb, y + 3], fill=(120, 255, 140))
        lab = f"crowd chanting, a shout every {per:.1f}s"
        hc = next((c for c in F.get("chant_heard", []) if abs(c["t0"] - ca) < 1 and c.get("heard")), None)
        if hc:
            lab += f': heard as "{hc["heard"]}" x{hc["repeats"]}'
            if hc["closest"]:
                lab += f", sounds most like {hc['closest'][0]['name']}"
            if hc.get("film_vote"):
                lab += f" (whole film: {hc['film_vote']})"
        fk = font(11, True)
        d.rectangle([xa, y + 4, xa + fk.getlength(lab) + 6, y + 19], fill=(0, 0, 0))
        d.text((xa + 3, y + 4), lab, font=fk, fill=(140, 255, 160))
    pg.label("crowd", "crowd stem", "0.3-4 kHz")
    # what is said
    pg.box("said")
    y, h = pg.y["said"]
    last = [-1e9, -1e9]
    speech_note = speech_status(vo, t0, t1)
    if speech_note:
        d.text((LEFT + 2, y + 2), speech_note, font=font(11), fill=(165, 165, 175))
    for wd in vo["words"]:
        if not (t0 <= wd["t"] < t1):
            continue
        xs = pg.x(wd["t"])
        if last[0] <= xs:
            k = 0
        elif last[1] <= xs:
            k = 1
        else:
            continue
        f = font(12, True)
        sure = wd.get("p", 1.0) >= 0.6
        named = F.get("subject") and any(al.strip() and al.strip() in wd["w"] for al in F["subject"].split(","))
        d.text((xs, y + 2 + k * 16), wd["w"], font=f, fill=(255, 214, 10) if named else (240, 240, 240) if sure else (120, 120, 128))
        last[k] = xs + f.getlength(wd["w"]) + 3
    pg.label("said", "said")
    pg.box("heard")
    mv = F["mem_voice"]
    _memory_lane(pg, "heard", mv["t"], mv["src"], (255, 170, 90), F.get("_exact", ([], []))[0])
    y, h = pg.y["heard"]
    for (rt, rd, lag, c) in vo.get("repeats", []):
        if rt + rd < t0 or rt > t1:
            continue
        xa, xb = pg.x(max(rt, t0)), pg.x(min(rt + rd, t1))
        d.rectangle([xa, y, max(xb, xa + 3), y + h], fill=(255, 40, 40))
        was = " ".join(w["w"] for w in vo["words"] if rt - lag - 0.2 <= w["t"] < rt - lag + rd + 0.2)[:40]
        lab = f"SAME SOUND AGAIN, {lag:.1f}s later" + (f": {was}" if was else "")
        d.text((xa + 3, y + 3), lab, font=font(12, True), fill=(255, 255, 255))
        # and mark where it was first heard
        xo = pg.x(rt - lag)
        if t0 <= rt - lag <= t1:
            d.rectangle([xo, y + h - 5, pg.x(rt - lag + rd), y + h], fill=(255, 40, 40))
    pg.label("heard", "heard again")
    # cuts through everything
    for ct in cuts_t:
        if t0 < ct < t1:
            x = pg.x(ct)
            for nm in ("change", "seen", "sound", "like", "level", "crowd", "said", "heard"):
                yy, hh = pg.y[nm]
                d.line([(x, yy), (x, yy + hh)], fill=(255, 70, 70, 110))
    return pg.img


def exact_replays(mv):
    """Stretches of 0.5 s or more whose voices are the very same recording as an earlier moment (similarity 0.99+),
    not merely the same speaker or singer again. Returns [(t0, t1, src_t0)]."""
    if mv.get("score") is None:
        return []
    ts, src, sc = np.asarray(mv["t"]), np.asarray(mv["src"], float), np.asarray(mv["score"])
    on = ~np.isnan(src) & (sc >= 0.99)
    out, i = [], 0
    while i < len(ts):
        if on[i]:
            j = i
            while j + 1 < len(ts) and (on[j + 1] or (j + 3 < len(ts) and on[j + 2:j + 4].any())) and ts[j + 1] - ts[i] < 30:
                j += 1
            if ts[j] - ts[i] >= 0.4:
                out.append((float(ts[i]), float(ts[j] + 0.1), float(src[i])))
            i = j + 1
        else:
            i += 1
    return out


def _memory_lane(pg, name, ts, src, colr, exact=()):
    """Runs where something earlier comes back. Bars first, then labels that never overlap one another. Stretches in
    `exact` are the very same recording replayed: drawn red and named so."""
    y, h = pg.y[name]
    d = pg.d
    on = ~np.isnan(src)
    runs, i, n = [], 0, len(ts)
    while i < n:
        if on[i] and pg.t0 <= ts[i] <= pg.t1:
            j = i
            while j + 1 < n and on[j + 1] and ts[j + 1] - ts[j] < 0.6 and abs(src[j + 1] - src[j] - (ts[j + 1] - ts[j])) < 0.6:
                j += 1
            runs.append((ts[i], min(ts[j] + 0.25, pg.t1), float(src[i])))
            i = j + 1
        else:
            i += 1
    for a, b, _ in runs:
        d.rectangle([pg.x(a), y + 2, max(pg.x(b), pg.x(a) + 2), y + h - 2], fill=colr)
    free = -1e9
    for ea, eb, es in exact:
        if eb < pg.t0 or ea > pg.t1:
            continue
        xa, xb = pg.x(max(ea, pg.t0)), pg.x(min(eb, pg.t1))
        d.rectangle([xa, y + 1, max(xb, xa + 2), y + h - 1], fill=(220, 30, 30))
        lab = f"SAME RECORDING AGAIN as {tc(es)[:-3]}" + (" (picture too)" if name == "seen" else "")
        fw = font(10, True).getlength(lab) + 6
        d.rectangle([xa, y + 1, xa + fw, y + h - 1], fill=(220, 30, 30))
        d.text((xa + 3, y + 1), lab, font=font(10, True), fill=(255, 255, 255))
        free = max(free, xa + fw + 2)
    for a, b, sv in runs:
        xa = pg.x(a)
        if xa < free or any(ea - 0.5 <= a <= eb for ea, eb, _ in exact):
            continue
        lag = a - sv
        lab = f"= {tc(sv)[:-3]}" if lag > 20 else f"= {lag:.1f}s ago"
        fw = font(10).getlength(lab) + 6
        d.rectangle([xa, y + 1, xa + fw, y + h - 1], fill=(250, 250, 250))
        d.text((xa + 3, y + 1), lab, font=font(10), fill=(20, 20, 20))
        free = xa + fw + 2


def genre_page(genre, name):
    """Page 0: what a normal frame of this kind of film looks like, so a reader notices when the film departs from it,
    the way a fan of the genre would."""
    frame = GENRES / f"{genre}.png"
    if genre != "lol" or not frame.exists():
        return None
    ref = Image.open(frame).convert("RGB")
    if ref.width > 1000:
        ref = ref.resize((1000, int(ref.height * 1000 / ref.width)))
    W = LEFT + PW + 8
    img = Image.new("RGB", (W, ref.height + 150), BG)
    d = ImageDraw.Draw(img)
    d.text((12, 8), f"{name}   page 0: before you watch, what a normal League of Legends broadcast frame looks like", font=font(14), fill=(235, 235, 235))
    img.paste(ref, (12, 40))
    x = 12 + ref.width + 24
    notes = ["A complete in-game broadcast frame",
             "(LCK, 16:9) shows:",
             "- top: teams, score, gold, clock",
             "- top centre: kill banners",
             "- left and right: the players",
             "- bottom: a full-width bar with the",
             "  player cameras and the scoreboard",
             "- bottom right: the minimap",
             "",
             "A fan expects all of this. If footage",
             "is cropped, zoomed or letterboxed so",
             "the bottom bar or the top bar is gone,",
             "or boxed small with dead space around",
             "it, a fan notices at once.",
             "",
             "The frame-shape strip under the page",
             "pictures shows how much of the frame",
             "the picture fills. Compare with this."]
    for i, t_ in enumerate(notes):
        d.text((x, 44 + i * 22), t_, font=font(16, True), fill=(225, 225, 230))
    return img


def _eye_lane(pg, F):
    """Where the subject's name tag is seen in the picture, and whether the predicted gaze is on it."""
    y, h = pg.y["eye"]
    d = pg.d
    gz = F.get("gaze")
    subj = (F.get("subject") or "").split(",")[0]
    last = -1e9
    for tg in F.get("tags", []):
        if not (pg.t0 <= tg["t"] < pg.t1) or not (0.08 < tg["y"] < 0.8 and 0.05 < tg["x"] < 0.95):
            continue
        col, word = (160, 160, 170), "seen"
        if gz is not None and len(gz["t"]):
            i = int(np.argmin(np.abs(gz["t"] - tg["t"])))
            dist = math.hypot((gz["x"][i] - tg["x"]) * 16, (gz["y"][i] - tg["y"]) * 9) / math.hypot(16, 9) * 100
            col, word = ((90, 230, 120), "eye on") if dist < 15 else ((250, 210, 80), "eye near") if dist < 30 else ((255, 90, 90), "eye away from")
        x0 = pg.x(tg["t"])
        d.rectangle([x0, y + 2, x0 + 8, y + h - 2], fill=col)
        if x0 - last > 120:
            lab = f"{word} {subj}"
            d.text((x0 + 10, y + 2), lab, font=font(11, True), fill=col)
            last = x0 + font(11, True).getlength(lab) + 12


# ============================================================== stream

def speech_status(voices, start, end):
    regions = voices.get("speech_activity")
    if regions is None or any(w["t"] < end and w["e"] >= start for w in voices["words"]):
        return ""
    if any(w["t"] < end and w["e"] > start for w in voices.get("speech_with_gain", [])):
        return "possible speech, words unclear at recorded level"
    if any(a < end and b > start for a, b in regions):
        return "possible speech, words unclear"
    return "no speech detected by the utterance detector"


def mix_level_text(levels, start, end):
    """Typical and loudest 10 ms mix levels within successive windows of this shot."""
    bins_per_s = SR // HOP
    first = sample_boundary(start, bins_per_s)
    stop = min(sample_boundary(end, bins_per_s), len(levels))
    # Use whole seconds for short shots and at most ten windows for a long take.
    span = max(1, math.ceil((stop - first) / (10 * bins_per_s))) * bins_per_s
    parts = []
    for i in range(first, stop, span):
        values = levels[i:min(i + span, stop)]
        peak = int(np.argmax(values))
        parts.append(f"{tc(i / bins_per_s)} {np.median(values):.0f}/{values[peak]:.0f}@{tc((i + peak) / bins_per_s)}")
    return ", ".join(parts)


def write_stream(F, out, name):
    cuts = frame_edges(F["v"])[F["cuts"]]
    bounds = [0., *cuts[(cuts > 0) & (cuts < F["dur"])], F["dur"]]
    a, vo = F["a"], F["vo"]
    lines = [f"# {name}: what happens, shot by shot", "",
             "time | length | on screen | said | loudness | seen before | heard before | observations | mix dBFS (window start, median/loudest 10 ms @ peak time)", ""]
    mp, mv = F["mem_pic"], F["mem_voice"]
    for sa, sb in zip(bounds[:-1], bounds[1:]):
        if sb <= sa:
            continue
        txt = " / ".join(dict.fromkeys(it["text"] for it in F["ocr"] if it["t"] < sb and it["e"] > sa))[:140]
        said = " ".join(w["w"] for w in vo["words"] if sa <= w["t"] < sb)[:160]
        mm = a["m_v"][(a["m_t"] >= sa) & (a["m_t"] < sb)]
        loud = f"{np.median(mm):.0f}" if len(mm) else ""
        sp = mp["src"][(mp["t"] >= sa) & (mp["t"] < sb)]
        sp = sp[~np.isnan(sp)]
        sv = mv["src"][(mv["t"] >= sa) & (mv["t"] < sb)]
        sv = sv[~np.isnan(sv)]
        subj_bits = ""
        if F.get("subject"):
            al = [x.strip() for x in F["subject"].split(",") if x.strip()]
            seen_n = sum(1 for tg in F.get("tags", []) if sa <= tg["t"] < sb)
            said_n = sum(1 for w in vo["words"] if sa <= w["t"] < sb and any(x in w["w"] for x in al))
            subj_bits = f" [{al[0]}: seen {seen_n}x, named {said_n}x]" if (seen_n or said_n) else f" [{al[0]}: not seen, not named]"
        guesses = [source_guess(top) for tt, top in F.get("timbre", []) if sa <= tt < sb]
        guesses = list(dict.fromkeys(guess for guess in guesses if guess))[:3]
        observations = ["resembles " + guess for guess in guesses]
        for window in F.get("sound_context", []):
            if sa < window["end_s"] <= sb:
                span = f'{tc(window["start_s"])}–{tc(window["end_s"])}'
                names = ", ".join(c["sound"] for c in window["candidates"])
                observations.append(f"possible sources across {span} (whole-window estimate): {names}")
        for tt, top in F.get("hits", []):
            if sa <= tt < sb:
                guess = source_guess(top)
                source = "resembles " + guess if guess else "source unclear"
                observations.append(f"sharp rise in level at {tc(tt)}, {source}")
        if not observations:
            levels = a["rms"][round(sa * SR / HOP):round(sb * SR / HOP)]
            if len(levels) and levels.max() >= -60:
                observations.append("sound present, source unclear")
        speech_note = speech_status(vo, sa, sb)
        if speech_note:
            observations.append(speech_note)
        evs = [lab for tt, lab, kind in F.get("ear") or ear_events(F["a"]) if sa <= tt < sb]
        evs += [f"picture {hi}, {du:.1f} s, {ho}" for tb, du, hi, ho in (F.get("pic_ev") or picture_events(F["v"])) if sa <= tb < sb]
        evs += [f"{k_} {du:.2f} s" for tb, du, k_ in (F.get("blips") or blips(F["v"])) if sa <= tb < sb]
        if F.get("sharp") is not None and len(F["sharp"]):
            evs += [f"picture much softer than around it ({du:.0f} s)" for tb, du in blur_runs(F["sharp"], cuts) if sa <= tb < sb]
        if evs:
            subj_bits += " [" + "; ".join(evs[:3]) + "]"
        for ra, rb, late, sure in sync_runs(F.get("sync")):
            if ra < sb and rb > sa:
                subj_bits += f" [sound {abs(late):.1f} s {'late' if late > 0 else 'early'} against the picture, {'sure' if sure else 'likely'}]"
        for ea, eb, es in exact_replays(F["mem_voice"]):
            if sa <= ea < sb:
                subj_bits += f" [SAME RECORDING AGAIN as {tc(es)[:-3]}, {eb - ea:.1f} s]"
        rp = [r for r in vo.get("repeats", []) if sa <= r[0] < sb]
        if rp:
            subj_bits += "  [SAME SOUND AGAIN " + ", ".join(f"at {tc(r[0])}, {r[2]:.1f}s after it first played" for r in rp) + "]"
        if subj_bits.strip():
            observations.append(subj_bits.strip())
        mix = mix_level_text(a["rms"], sa, sb) if a.get("has_audio", True) else "no audio track"
        duration = f"{sb - sa:.3f}s" if sb - sa < .1 else f"{sb - sa:.1f}s"
        lines.append(f"{tc(sa)} | {duration} | {txt} | {said} | {loud} | "
                     f"{tc(float(np.median(sp)))[:-3] if len(sp) else ''} | {tc(float(np.median(sv)))[:-3] if len(sv) else ''} | {', '.join(observations)} | "
                     f"{mix}")
    (out / "stream.md").write_text("\n".join(lines) + "\n")


# ============================================================== main

def build(film, out, page_s, lang, title=None, subject=None, genre=None, until=None):
    info = probe(film)
    if not info["has_video"]:
        raise ValueError("pages need a timed video track; use reel play for an audio recording")
    if until is None:
        return _build(film, out, page_s, lang, title, subject, genre)
    if not math.isfinite(until) or until <= 0:
        raise ValueError("--until must be a finite positive number of seconds")
    out = pathlib.Path(out)
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        raise ValueError("choose a new or empty --out directory for a time-limited reel")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        work = pathlib.Path(tmp)
        clip = work / "clip.mkv"
        # Trim decoded frames and samples. Stream copying can carry packets beyond the boundary.
        end = f"{until:.9f}"
        # trim removes the next presentation, which could be seconds away on a VFR hold.
        # Carry the final retained picture up to the cutoff or the actual end of its track.
        picture_stop = min(until, video_end(film, info))
        frame_clock = work / "frame-times.us"
        duration_filter = f"setts=duration='if(eq(NEXT_PTS,NOPTS),max(1,round({picture_stop:.9f}/TB)-PTS),DURATION)'"
        audio_opts = []
        if info["has_audio"]:
            stop = sample_boundary(until, info["audio_rate"])
            audio_opts = ["-map", f"0:{info['audio_index']}", "-af", f"{audio_timeline(info)},atrim=end_sample={stop}", "-c:a", pcm_codec(info)]
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(film),
                        "-map", f"0:{info['video_index']}", "-map_metadata", "-1", "-map_chapters", "-1",
                        "-vf", f"settb=AVTB,trim=end={end}", *audio_opts,
                        "-fps_mode", "passthrough", "-enc_time_base:v", "1:1000000", "-c:v", "ffv1",
                        "-stats_enc_pre:v", str(frame_clock), "-stats_enc_pre_fmt:v", "{pts}",
                        "-bsf:v", duration_filter, str(clip)], check=True)
        # Save the last retained source PTS before the container rounds it to milliseconds.
        with frame_clock.open() as stamps:
            last_pts = next(iter(deque(stamps, maxlen=1)), None)
        if last_pts is None:
            raise ValueError("no video frames in the requested interval")
        # All helpers, including OCR, ASR and zoom, only get the clipped media.
        _build(clip, work, page_s, lang, title or "Reel", subject, genre, until,
               picture_stop=picture_stop, last_source_pts=int(last_pts))
        bundle = work / "snapshot"
        bundle.mkdir()
        for name in ("clip.mkv", "pages", "stream.md", "meta.json", "features.npz", "sync.json", "now.png"):
            artifact = work / name
            if artifact.exists():
                artifact.rename(bundle / name)
        bundle.rename(out)
    print(f"[perceive] snapshot -> {out / 'pages'}", flush=True)
    if (out / "now.png").is_file():
        print(f"[perceive] picture at stop -> {out / 'now.png'}", flush=True)


def _build(film, out, page_s, lang, title, subject, genre, until=None, *, picture_stop=None, last_source_pts=None):
    out.mkdir(parents=True, exist_ok=True)
    log = lambda s: print(s, flush=True)
    info = probe(film)
    analysis = out
    if info["alternate_streams"]:
        # Old stems and sync estimates may describe a different track in this same file.
        version = "-".join(map(str, RecordedSource.version(pathlib.Path(film))))
        analysis = out / f"tracks-{info['video_index']}-{info.get('audio_index', 'none')}-{version}"
        analysis.mkdir(exist_ok=True)
    if until is not None:
        info["dur"] = min(info["dur"], until)
    audio_src = film
    if not info["has_audio"]:                          # a silent film is still watchable: give it a silent track
        audio_src = out / "silence.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=stereo", "-t",
                        f"{info['dur']:.3f}", str(audio_src)], check=True)
        log("  (the film has no sound track)")
    name = title or pathlib.Path(film).name
    log(f"[perceive] {name} {info['dur']:.1f}s")
    with ThreadPoolExecutor(3) as ex:
        fv = ex.submit(video_pass, film, info, log)
        fa = ex.submit(audio_pass, audio_src, log, has_audio=info["has_audio"])
        fo = ex.submit(ocr_pass, film, info["dur"], log, 1.0, subject)
        v, a, (ocr, tags) = fv.result(), fa.result(), fo.result()
    a["has_audio"] = info["has_audio"]
    if not info["has_audio"]:
        a.pop("channel_rms", None)
    seen_words = {}
    for it in ocr:
        for w_ in re.findall(r"[가-힣A-Za-z0-9]{2,}", it["text"]):
            seen_words[w_] = seen_words.get(w_, 0) + 1
    prompt = " ".join(w_ for w_, _ in sorted(seen_words.items(), key=lambda kv: -kv[1])[:60])[:220] or None
    if not len(v["mad"]):
        raise ValueError("no video frames in the requested interval")
    if picture_stop is not None:
        # Matroska rounds track duration to milliseconds. The source track tells us
        # whether the last image was still on screen in a fractional final millisecond.
        v["edges"][-1] = max(v["edges"][-2], picture_stop)
    if until is not None:
        for item in ocr:
            item["e"] = min(item["e"], info["dur"])
    if info["has_audio"]:
        vo = voices(film, analysis, lang, log, None, deterministic=until is not None)  # a prompt built from screen text made whisper drop speech; keep it off
    else:
        n = a["db"].shape[1]
        vo = dict(words=[], vdb=np.full((40, n), -100, np.float32), vrms=np.full(n, -100, np.float32), repeats=[], crowd=None)
    if until is not None:
        vo["words"] = [word for word in vo["words"] if 0 <= word["t"] < info["dur"]]
        for word in vo["words"]:
            word["e"] = min(word["e"], info["dur"])
    cuts = find_cuts(v)
    v["display_aspect"] = info["display_aspect"]
    # A container with a nonzero starting timestamp can report an end time as its duration.
    # Keep a real audio tail while ending a picture-only reel at the last displayed frame.
    media_end = max(v["edges"][-1], a["dur"] if info["has_audio"] else 0)
    F = dict(v=v, a=a, vo=vo, ocr=ocr, cuts=cuts, dur=min(until if until is not None else info["dur"], media_end), tags=tags, subject=subject)
    if until is not None and vo.get("speech_with_gain"):
        vo["speech_with_gain"] = [w for w in vo["speech_with_gain"] if 0 <= w["t"] < F["dur"]]
        for w in vo["speech_with_gain"]:
            w["e"] = min(w["e"], F["dur"])
            w["context_end_s"] = min(w["context_end_s"], F["dur"])
    F["sharp"] = sharpness_pass(film, F["dur"])
    F["timbre"], F["sound_context"] = timbre_pass(film, F["dur"], log) if info["has_audio"] else ([], [])
    F["hits"] = hit_pass(film, a["rms"], log) if info["has_audio"] else []
    F["sync"] = sync_pass(film, analysis, log) if info["has_audio"] else None
    F["chants"] = chant_windows(vo.get("crowd"))
    F["chant_heard"] = hear_chants(analysis, F["chants"], ocr, genre, lang, log)
    if subject:
        subprocess.run([GAZE_PY, str(HERE / "gaze.py"), str(film), "--out", str(analysis / "gaze")], capture_output=True)
        if (analysis / "gaze/gaze.json").exists():
            sm = json.load(open(analysis / "gaze/gaze.json"))["samples"]
            F["gaze"] = dict(t=np.array([q["t"] for q in sm]), x=np.array([q["x"] for q in sm]), y=np.array([q["y"] for q in sm]))
    F["mem_pic"] = memory_picture(v, cuts)
    F["mem_voice"] = memory_voice(vo)
    log("  memory done")
    pages = out / "pages"; pages.mkdir(exist_ok=True)
    for old in pages.glob("*.png"):
        old.unlink()
    total = math.ceil(F["dur"] / page_s)
    g = genre_page(genre, name)
    if g is not None:
        g.save(pages / "page_00.png")
    for i in range(total):
        end = (i + 1) * page_s
        if until is not None:
            end = min(end, F["dur"])
        render_page(F, i * page_s, end, i + 1, total, name).save(pages / f"page_{i + 1:02d}.png")
    write_stream(F, out, name)
    np.savez_compressed(out / "features.npz", **{f"v_{k}": val for k, val in v.items() if k != "fps"},
                        **{f"a_{k}": val for k, val in a.items() if k not in ("changes", "dur")}, a_changes=a["changes"],
                        vo_vdb=vo["vdb"], vo_vrms=vo["vrms"], cuts=cuts, vo_repeats=np.array(vo["repeats"], np.float32).reshape(-1, 4),
                        vo_crowd=vo["crowd"] if vo["crowd"] is not None else np.zeros((0, 0), np.float32),
                        mp_t=F["mem_pic"]["t"], mp_src=F["mem_pic"]["src"], mv_t=F["mem_voice"]["t"], mv_src=F["mem_voice"]["src"],
                        mp_score=F["mem_pic"]["score"], mv_score=F["mem_voice"]["score"])
    source = str(pathlib.Path(film).resolve()) if until is None else "clip.mkv"
    snapshot = {} if until is None else {"until": F["dur"]}
    if until is not None:
        # The last picture in a history strip can be older than the stopping point.
        # An audio tail can also continue after the picture has ended.
        snapshot["picture_end_s"] = picture_stop
        snapshot["current_frame"] = None
        if F["dur"] <= picture_stop:
            clip_last_us = round(float(v["edges"][-2]) * 1_000_000)
            _save_frame(film, clip_last_us, out / "now.png")
            snapshot["current_frame"] = {"file": "now.png", "pts_us": last_source_pts}
    metadata = dict(film=source, **snapshot, fps=v["fps"], dur=F["dur"], page_s=page_s, pages=total,
                   words=vo["words"], speech_activity=vo.get("speech_activity"), speech_with_gain=vo.get("speech_with_gain", []), ocr=ocr, tags=tags,
                   subject=subject, genre=genre, chants=[list(c) for c in F["chants"]], chant_heard=F["chant_heard"],
                   sharp=[None if np.isnan(x) else float(x) for x in F["sharp"]], timbre=F["timbre"],
                   sound_context=F["sound_context"], hits=F["hits"],
                   gaze={k: list(map(float, v_)) for k, v_ in F["gaze"].items()} if "gaze" in F else None)
    if analysis != out:
        metadata["analysis_directory"] = analysis.name
    (out / "meta.json").write_text(json.dumps(metadata, ensure_ascii=False))
    if until is None:
        log(f"[perceive] {total} pages -> {pages}")


def load(out):
    out = pathlib.Path(out)
    m = json.load(open(out / "meta.json"))
    z = np.load(out / "features.npz")
    v = {k[2:]: z[k] for k in z.files if k.startswith("v_")}; v["fps"] = m["fps"]
    a = {k[2:]: z[k] for k in z.files if k.startswith("a_")}
    vo = dict(words=m["words"], speech_activity=m.get("speech_activity"), speech_with_gain=m.get("speech_with_gain", []), vdb=z["vo_vdb"], vrms=z["vo_vrms"], repeats=[tuple(r) for r in z["vo_repeats"]] if "vo_repeats" in z.files else [],
              crowd=z["vo_crowd"] if "vo_crowd" in z.files and z["vo_crowd"].size else None)
    extra = dict(tags=m.get("tags", []), subject=m.get("subject"), chants=[tuple(c) for c in m.get("chants", [])],
                 chant_heard=m.get("chant_heard", []), timbre=[(t_, [tuple(x) for x in top]) for t_, top in m.get("timbre", [])],
                 sound_context=m.get("sound_context", []), hits=[(t_, [tuple(x) for x in top]) for t_, top in m.get("hits", [])],
                 sharp=np.array([np.nan if x is None else x for x in m.get("sharp", [])], np.float32))
    if m.get("gaze"):
        extra["gaze"] = {k: np.array(v_) for k, v_ in m["gaze"].items()}
    sync_file = out / m.get("analysis_directory", ".") / "sync.json"
    extra["sync"] = json.loads(sync_file.read_text()) if sync_file.exists() else None
    return dict(**extra, v=v, a=a, vo=vo, ocr=m["ocr"], cuts=z["cuts"], dur=m["dur"],
                mem_pic=dict(t=z["mp_t"], src=z["mp_src"], score=z["mp_score"] if "mp_score" in z.files else None),
                mem_voice=dict(t=z["mv_t"], src=z["mv_src"], score=z["mv_score"] if "mv_score" in z.files else None)), m


def frame_at(out, at):
    """Return the full frame on display at this instant, including in a variable-rate film."""
    out = pathlib.Path(out)
    meta = json.loads((out / "meta.json").read_text())
    end = meta.get("until", meta["dur"])
    if not 0 <= at < end:
        raise ValueError(f"frame time must be at least 0 and less than {end:g} seconds")
    source = reel_source(out, meta)
    picture_end = meta["picture_end_s"] if "picture_end_s" in meta else video_end(source, probe(source))
    if at >= picture_end:
        raise ValueError(f"no video frame on display at {at:g} seconds")
    us = int(at * 1_000_000)
    return _save_frame(source, us, out / f"frame_{us}us.png")


def zoom(out, t0, t1):
    """Redraw the lanes for a stretch, using native frames and their presentation times."""
    F, m = load(out)
    film = str(reel_source(out, m))
    if "until" in m:
        if not 0 <= t0 < t1 <= m["until"]:
            raise ValueError(f"zoom range must be within 0 to {m['until']:g} seconds")
    if "display_aspect" not in F["v"] and pathlib.Path(film).is_file():
        F["v"]["display_aspect"] = probe(film)["display_aspect"]
    if not 0 <= t0 < t1 or not math.isfinite(t1):
        raise ValueError("zoom requires a finite, increasing time interval")
    cut_times = frame_edges(F["v"])[F["cuts"]]
    times, thumbs = [], []
    last_bucket = last_shot = None
    for pts, _, pixels in native_frames(film, TW, TH, int(t0 * 1e6), sample_boundary(t1, 1_000_000)):
        t = pts / 1e6
        bucket = pts * 12 // 1_000_000
        shot = cut_times.searchsorted(t, side="right")
        if t1 - t0 <= 5 or bucket != last_bucket or shot != last_shot:
            times.append(t)
            thumbs.append(np.frombuffer(pixels, np.uint8).reshape(TH, TW, 3))
        last_bucket, last_shot = bucket, shot
    F["detail_pictures"] = dict(thumb_times=np.asarray(times), thumbs=thumbs,
                                display_aspect=F["v"].get("display_aspect", TW / TH))
    img = render_page(F, t0, t1, 0, 0, pathlib.Path(film).name + "  ZOOM")
    path = pathlib.Path(out) / f"zoom_{t0:.1f}_{t1:.1f}.png"
    img.save(path)
    print(path)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "zoom":
        ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("out")
        ap.add_argument("--t0", type=float, required=True); ap.add_argument("--t1", type=float, required=True)
        x = ap.parse_args(); zoom(x.out, x.t0, x.t1)
    else:
        ap = argparse.ArgumentParser(); ap.add_argument("film"); ap.add_argument("--out", required=True)
        ap.add_argument("--page", type=float, default=30.0); ap.add_argument("--lang", default="ko")
        ap.add_argument("--until", type=float)
        ap.add_argument("--title", default=None, help="name printed on the pages (default: the file name)")
        ap.add_argument("--subject", default=None, help="the person the film is about, as written on name tags (e.g. Zeus)")
        ap.add_argument("--genre", default=None, help="'lol' adds page 0 with a normal League of Legends broadcast frame")
        x = ap.parse_args(); build(x.film, pathlib.Path(x.out), x.page, x.lang, x.title, x.subject, x.genre, x.until)
