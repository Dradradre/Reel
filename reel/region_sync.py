"""Read how closely the motion in specified screen regions lines up with a bounded soundtrack."""
from __future__ import annotations

from contextlib import redirect_stdout
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

if __package__:
    from .action_view import draw_peaks
    from .common import display_scale, font, open_recording, probe, sample_boundary, save_observation_png
    from .frames import frames_from_sheets
    from .config import SYNC_PY
    from .sound_glimpses import sheet_picture
else:
    from action_view import draw_peaks
    from common import display_scale, font, open_recording, probe, sample_boundary, save_observation_png
    from frames import frames_from_sheets
    from config import SYNC_PY
    from sound_glimpses import sheet_picture


def validate_regions(regions):
    if any(len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1) for box in regions):
        raise ValueError("each sync region needs 0 <= left < right <= 1 and 0 <= top < bottom <= 1")


def regional_timing(source, report, regions):
    pictures = [m for m in report["moments"] if m["picture"] is not None]
    if not pictures or not report["audio"]:
        return {"status": "unavailable", "reason": "no concurrent picture and recorded sound"}
    job = dict(source=str(source), wav=report["audio"]["wav"], origin=report["start_s"],
               start=pictures[0]["start_s"], end=pictures[-1]["end_s"], regions=regions)
    return run_timing(job)


def published_timing(scene, regions):
    """Compare only pictures and samples already delivered on this film clock."""
    validate_regions(regions)
    if not scene.get("pages") or not scene.get("wav"):
        return {"status": "unavailable", "reason": "no concurrent picture and recorded sound"}
    start = max(scene["start"], scene["pages"][0]["pts_us"][0] / 1_000_000)
    end = min(scene["end"], scene["picture_end"])
    if end <= start:
        return {"status": "unavailable", "reason": "no concurrent picture and recorded sound"}
    return run_timing(dict(source=None, wav=str(scene["wav"]), origin=scene["start"], start=start, end=end,
                           regions=regions, pages=scene["pages"], size=scene["size"], first_sample=scene["first_sample"]))


def run_timing(job):
    try:
        proc = subprocess.run([SYNC_PY, str(Path(__file__).resolve())], input=json.dumps(job),
                              capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        details = (getattr(error, "stderr", "") or "").strip() or str(error)
        reason = details.splitlines()[-1] if details else "sync helper unavailable"
        return {"status": "unavailable", "reason": reason}


def region_frames(source, start, end, box, fps, count, side):
    left, top, right, bottom = box
    begin, stop = (sample_boundary(t, 1_000_000) for t in (start, end))
    # Bound the frame PTS before rate conversion. A frame already on screen at start can carry in.
    # Square pixels in the input to Synchformer correspond to a square in display coordinates.
    sar = "if(gt(sar,0),sar,1)"
    filters = [f"settb=AVTB,trim=end_pts={stop},setpts=PTS-{begin}",
               f"fps={fps}:start_time=0:eof_action=pass",
               f"crop=w='max(1,iw*{right-left})':h='max(1,ih*{bottom-top})':x=iw*{left}:y=ih*{top}:exact=1",
               f"crop=w='max(1,min(iw,ih/({sar})))':h='max(1,min(ih,iw*({sar})))':exact=1",
               display_scale(source, f"{side}:{side}"), "setsar=1,format=rgb24"]
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-copyts", "-start_at_zero", "-noaccurate_seek", "-ss",
           f"{begin}us", "-i", str(source), "-map", f"0:{probe(source)['video_index']}", "-vf", ",".join(filters), "-frames:v", str(count),
           "-c:v", "rawvideo", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, side, side, 3)
    if not len(frames):
        raise ValueError("no displayed frame in the requested window")
    # The trained offset head expects five seconds; disclose padding in the response.
    return np.concatenate([frames, np.repeat(frames[-1:], count - len(frames), axis=0)])


def square_region(box, size):
    """Square at the centre of the requested box in square display pixels."""
    w, h = size
    left, top = math.floor(box[0] * w), math.floor(box[1] * h)
    right, bottom = math.ceil(box[2] * w), math.ceil(box[3] * h)
    width = min(right - left, bottom - top)
    x, y = left + (right - left - width) // 2, top + (bottom - top - width) // 2
    return x, y, x + width, y + width


def region_views(out, scene, regions):
    """Place changes in the same input squares beside the original channel peaks."""
    if not scene.get("pages") or not scene.get("wav") or scene["start"] >= scene["picture_end"]:
        return []
    size = scene["size"]
    start, end = (Fraction(str(scene[k])) for k in ("start", "end"))
    bounds = [square_region(box, size) for box in regions]
    rows = [[] for _ in regions]
    previous = None
    first = None
    for pts, _, pixels in frames_from_sheets(scene["pages"], size):
        if pts >= sample_boundary(scene["picture_end"], 1_000_000):
            break
        frame = np.frombuffer(pixels, dtype=np.uint8).reshape(size[1], size[0], 3)
        if first is None:
            first = (pts, Image.fromarray(frame))
        if previous is not None:
            delta = np.abs(frame.astype(np.int16) - previous)
            for changes, (x, y, right, bottom) in zip(rows, bounds, strict=True):
                changes.append((pts, float(delta[y:bottom, x:right].mean())))
        previous = frame
        last_pts = pts
    if previous is None:
        return []
    stills = [first]
    if first[0] != last_pts:
        stills.append((last_pts, Image.fromarray(previous)))
    for _, image in stills:
        image.thumbnail((316, 240), Image.Resampling.LANCZOS)
    maximum = max((v for row in rows for _, v in row), default=0.)
    left, width = 95, 660
    colours = ["#efb34b", "#59c6e3", "#de96cc", "#a7d776"]
    result = []
    with open_recording(scene["wav"]) as wav:
        for base in range(0, len(regions), len(colours)):
            count = min(len(colours), len(regions) - base)
            top = stills[0][1].height + 42
            panel = Image.new("RGB", (left + width + 8, top + count * 38 + wav.channels * 30 + 32), "#15171b")
            draw = ImageDraw.Draw(panel)
            group = bounds[base:base + count]
            for index, (pts, still) in enumerate(stills):
                x0 = left + index * (width // 2)
                panel.paste(still, (x0, 0))
                for j, (x, y, right, bottom) in enumerate(group):
                    x1, y1 = x0 + x * still.width // size[0], y * still.height // size[1]
                    x2 = max(x1, x0 + math.ceil(right * still.width / size[0]) - 1)
                    y2 = max(y1, math.ceil(bottom * still.height / size[1]) - 1)
                    draw.rectangle((x1, y1, x2, y2), outline=colours[j], width=2)
                    draw.text((x1, y1), str(base + j + 1), font=font(14), fill=colours[j], stroke_width=1, stroke_fill="black")
                draw.text((x0, still.height + 1), f"Picture at {pts / 1e6:.3f} s", font=font(13), fill="white")
            for j in range(count):
                colour = colours[j]
                bottom = top + (j + 1) * 38 - 4
                draw.text((4, bottom - 21), f"region {base + j + 1}", font=font(14), fill=colour)
                draw.line((left, bottom, left + width - 1, bottom), fill="#47545c")
                for pts, value in rows[base + j]:
                    col = math.floor((pts / 1e6 - float(start)) * width / float(end - start))
                    if maximum and value > 0 and 0 <= col < width:
                        height = max(1, round(28 * value / maximum))
                        draw.line((left + col, bottom - height, left + col, bottom - 1), fill=colour)
            draw.text((left, top - 22), f"Mean pixel change, common scale 0 to {maximum:.2f} / 255",
                      font=font(13), fill="white")
            top += count * 38
            # Read at most about one second of native PCM into memory at once.
            parts = math.ceil(end - start)
            for i in range(parts):
                a, b = width * i // parts, width * (i + 1) // parts
                draw_peaks(draw, wav, scene["first_sample"], start + (end - start) * a / width,
                           start + (end - start) * b / width, left + a, top, b - a)
            bottom = top + wav.channels * 30
            for i in range(5):
                t = start + (end - start) * i / 4
                x = left + round((width - 1) * i / 4)
                draw.line((x, bottom, x, bottom + 3), fill="white")
                draw.text((x, bottom + 5), f"{float(t):.3f}", anchor="mt" if i < 4 else "rt", font=font(12), fill="white")
            result.append({"file": save_observation_png(out, panel, "region_motion"), "picture_pts_us": [pts for pts, _ in stills],
                           "picture_interval_s": [max(float(start), first[0] / 1e6), scene["picture_end"]],
                           "region_numbers": list(range(base + 1, base + count + 1)),
                           "input_squares_px": bounds[base:base + count], "picture_size": size})
    return result


def released_region_frames(picture, start, end, bounds, fps, count, side):
    """Sample the exposure on display at each encoder tick, then hold the last sampled image."""
    first, stop = Fraction(str(start)) * 1_000_000, Fraction(str(end)) * 1_000_000
    clip = np.empty((count, side, side, 3), dtype=np.uint8)
    previous = None
    for i in range(count):
        at = first + Fraction(i * 1_000_000, fps)
        if at >= stop:
            clip[i:] = clip[i - 1]
            break
        frame = picture(math.floor(at))
        if frame is None:
            raise ValueError("no released picture at the requested encoder tick")
        pts, image = frame
        if pts != previous:
            tile = image.crop(bounds).resize((side, side), Image.Resampling.BILINEAR)
            clip[i] = np.asarray(tile)
        else:
            clip[i] = clip[i - 1]
        previous = pts
    return clip


def measure(source, wav, origin, start, end, regions, *, pages=None, size=None, first_sample=None):
    from scipy.signal import resample_poly
    if __package__:
        from .sync import AFPS, SIDE, VFPS, WIN, load_model, offset_distributions
    else:
        from sync import AFPS, SIDE, VFPS, WIN, load_model, offset_distributions

    validate_regions(regions)
    picture = sheet_picture(pages, size) if pages else None
    model = None
    windows = []
    begin, stop, width = (Fraction(str(t)) for t in (start, end, WIN))
    n = max(1, math.ceil((stop - begin) / width))
    with open_recording(wav) as recording:
        rate = recording.samplerate
        ratio = Fraction(AFPS, rate)
        if first_sample is None:
            first_sample = sample_boundary(origin, rate)
        for i in range(n):
            # Overlap full encoder windows when possible, including the last real five seconds.
            offset = i * (stop - begin - width) / (n - 1) if n > 1 else 0
            t0 = begin + offset
            t1 = min(stop, t0 + width)
            left, right = float(t0), float(t1)
            a, b = (sample_boundary(t, rate) for t in (t0, t1))
            if left == origin:
                a = first_sample
            b = min(b, first_sample + len(recording))
            duration = t1 - t0
            window = {"start_s": left, "end_s": right, "padded_s": round(float(width - duration), 6)}
            windows.append(window)
            recording.seek(a - first_sample)
            sound = recording.read(b - a, dtype="float32", always_2d=True)
            if duration < 1 or not len(sound):
                window["status"] = "insufficient simultaneous media"
                continue
            channels = []
            for j, channel in enumerate(sound.T):
                same = next((row for row in channels if np.array_equal(channel, sound[:, row["channels"][0] - 1])), None)
                if same is None:
                    channels.append({"channels": [j + 1], "digital_silence": not bool(np.any(channel))})
                else:
                    same["channels"].append(j + 1)
            window["channels"] = channels
            audible = [c for c in channels if not c["digital_silence"]]
            if not audible:
                continue
            waveforms = []
            for c in audible:
                channel = sound[:, c["channels"][0] - 1]
                wave = resample_poly(channel, ratio.numerator, ratio.denominator)[:int(WIN * AFPS)]
                waveforms.append(np.pad(wave, (0, int(WIN * AFPS) - len(wave))))
                c["near_zero_mass"] = []
            for box_index, box in enumerate(regions):
                if picture is None:
                    clip = region_frames(source, left, right, box, VFPS, int(WIN * VFPS), SIDE)
                else:
                    clip = released_region_frames(picture, left, right, square_region(box, size),
                                                  VFPS, int(WIN * VFPS), SIDE)
                if not np.any(clip != clip[0]):
                    window.setdefault("static_region_indices", []).append(box_index)
                    for c in audible:
                        c["near_zero_mass"].append(None)
                    continue
                if model is None:
                    import torch
                    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
                    model, transform, grid = load_model("audioset", device)
                for c, wave in zip(audible, waveforms):
                    dist, = offset_distributions(model, transform, device, [(clip, wave)])
                    c["near_zero_mass"].append(round(float(dist[np.abs(np.round(grid, 2)) <= .2].sum()), 4))
    return {"status": "available", "model": "Synchformer AudioSet", "regions": regions,
            "basis": "For each box in order, offset-model mass within 0.2 s of zero. The encoder uses the centre "
                     "of each box. This measures timing; coincident motion and offscreen sound are possible. "
                     "Static means unchanged at the 25 Hz encoder ticks. Short windows hold their last sampled image "
                     "and pad their sound with silence.", "windows": windows}


if __name__ == "__main__":
    with redirect_stdout(sys.stderr):
        result = measure(**json.load(sys.stdin))
    print(json.dumps(result))
