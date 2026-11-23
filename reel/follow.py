"""Follow a reader's point through pictures a playback has already delivered."""
from bisect import bisect_right
from collections import namedtuple
from fractions import Fraction
import json
import math
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

if __package__:
    from .attention import fixation_box, focus_still
    from .common import font
    from .frames import cell_box, save_frame, sheet_size
else:
    from attention import fixation_box, focus_still
    from common import font
    from frames import cell_box, save_frame, sheet_size

MAX_SECONDS = 8
Exposure = namedtuple("Exposure", "pts file box still")


def validate_anchor(point, time):
    if (point is None or len(point) != 2 or not all(0 <= p <= 1 for p in point)
            or not math.isfinite(time) or time < 0):
        raise ValueError("follow_from_s needs a nonnegative film time and focus=[x,y] within the picture")


def released_exposures(directory, packet, start):
    """Walk backwards only as far as the turn displaying the anchor. Held frames share one PTS."""
    turns = [(directory, packet)]
    serial = directory.name.removeprefix("turn_")
    if directory.name.startswith("turn_") and serial.isdigit():
        for index in range(int(serial) - 1, 0, -1):
            next_start = turns[-1][1]["start_s"]
            if next_start <= start:
                break
            previous = directory.parent / f"turn_{index:05}"
            manifest = previous / "view.json"
            if not manifest.is_file():
                break
            past = json.loads(manifest.read_text())
            if past.get("schema") != "reel.play.v1" or past["end_s"] != next_start:
                break
            turns.append((previous, past))
    first = turns[-1][1]
    if not first["start_s"] <= start < first.get("picture_end_s", first["end_s"]) or not first["pages"]:
        return []
    seen = {}
    for folder, view in reversed(turns):
        size = view.get("picture_size")
        if not size and view.get("current_frame"):
            with Image.open(folder / view["current_frame"]["file"]) as still:
                size = sheet_size({"display_aspect": still.width / still.height})
        if not size:
            if view["pages"]:
                return []
            continue
        stop = Fraction(str(view.get("picture_end_s", view["end_s"]))) * 1_000_000
        still = view.get("current_frame") or {}
        for page in view["pages"]:
            for i, pts in enumerate(page["pts_us"]):
                if pts < stop:
                    native = str(folder / still["file"]) if pts == still.get("pts_us") else None
                    if native or pts not in seen:
                        seen[pts] = Exposure(pts, str((folder / page["file"]).resolve()), cell_box(i, size), native)
    frames = sorted(seen.values())
    index = bisect_right(frames, Fraction(str(start)) * 1_000_000, key=lambda f: f.pts) - 1
    return frames[index:] if index >= 0 else []


def pictures(frames):
    sheet = None
    previous = None
    try:
        for _, path, box, _ in frames:
            if path != previous:
                if sheet:
                    sheet.close()
                sheet = Image.open(path)
                previous = path
            yield sheet.crop(box).convert("RGB")
    finally:
        if sheet:
            sheet.close()


def optical_track(frames, point):
    """Follow local texture; stop if the reverse match fails to find the starting pixel."""
    options = dict(winSize=(21, 21), maxLevel=3)
    path = [list(point)]
    previous = None
    for image in pictures(frames):
        gray = np.asarray(image.convert("L"))
        size = np.array(image.size)
        if previous is None:
            pos = np.clip(np.array(point) * size - .5, 0, size - 1).astype(np.float32).reshape(1, 1, 2)
        else:
            if gray.shape != previous.shape:
                break
            target, forward, _ = cv2.calcOpticalFlowPyrLK(previous, gray, pos, None, **options)
            if not forward.item() or not np.isfinite(target).all():
                break
            back, reverse, _ = cv2.calcOpticalFlowPyrLK(gray, previous, target, None, **options)
            if not reverse.item() or not np.linalg.norm(back - pos) <= 1.5:
                break
            pos = target
            xy = (pos.reshape(2) + .5) / size
            if not np.all((xy >= 0) & (xy <= 1)):
                break
            path.append(xy.tolist())
        previous = gray
    return path + [None] * (len(frames) - len(path))


def follow_point(directory, packet, start, point, reader=None):
    validate_anchor(point, start)
    end = packet["end_s"]
    if start >= end:
        return {"status": "anchor_outside_view"}, []
    if end - start > MAX_SECONDS + 1e-9:
        return {"status": "anchor_too_old", "earliest_anchor_s": end - MAX_SECONDS}, []
    frames = released_exposures(directory, packet, start)
    if not frames:
        return {"status": "no_picture_at_anchor"}, []
    result = reader.track(frames, point) if reader is not None else {}
    if result.get("status") == "available":
        path, method = result["points"], result["model"]
    else:
        path, method = optical_track(frames, point), "Lucas-Kanade optical flow with reverse check"
    trail = [{"pts_us": frame.pts, "point": xy} for frame, xy in zip(frames, path, strict=True)]
    current = packet.get("current_frame") or {}
    endpoint = trail[-1] if current.get("pts_us") == frames[-1].pts else None
    report = {"status": "available", "method": method, "requested_s": start, "anchor": trail[0],
              "frame_count": len(trail), "located_frames": sum(x is not None for x in path),
              "last_located": next(sample for sample in reversed(trail) if sample["point"] is not None),
              "current": endpoint, "trajectory": trail}
    if reader is not None and result.get("status") == "unavailable":
        report["tracker_note"] = result.get("reason", "learned tracker unavailable")
    return report, frames


def exposure_detail(frame, point, dest, original):
    source = frame.still
    basis = "published still"
    if source is None:
        source = dest.with_name("input.png")
        basis = "published frame sheet"
        if original:
            try:
                with original.reading() as movie:
                    save_frame(movie, frame.pts, source)
                basis = "original recording"
            except (OSError, ValueError):
                pass
        if basis == "published frame sheet":
            with Image.open(frame.file) as sheet:
                sheet.crop(frame.box).save(source)
    focus_still(source, point, fixation_box(point), dest)
    return basis


def draw_follow(out, directory, packet, report, frames, original=None):
    if not frames:
        return []
    # Select illustrations by elapsed presentation time; every exposure contributed to the estimate.
    times = np.linspace(frames[0].pts, frames[-1].pts, 6)
    ids = sorted({bisect_right(frames, time, key=lambda f: f.pts) - 1 for time in times})
    width, height = 240, 162
    sheet = Image.new("RGB", (min(3, len(ids)) * width, math.ceil(len(ids) / 3) * height), (20, 22, 26))
    draw = ImageDraw.Draw(sheet)
    for cell, (index, pic) in enumerate(zip(ids, pictures([frames[i] for i in ids]))):
        stamp = report["trajectory"][index]
        xy = stamp["point"]
        x, y = cell % 3 * width, cell // 3 * height
        pic.thumbnail((width - 4, height - 26), Image.Resampling.LANCZOS)
        left, top = x + (width - pic.width) // 2, y + (height - 26 - pic.height) // 2
        sheet.paste(pic, (left, top))
        if xy is not None:
            cx, cy = left + round(xy[0] * (pic.width - 1)), top + round(xy[1] * (pic.height - 1))
            draw.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), outline="#ffce40", width=2)
        label = f"{stamp['pts_us'] / 1e6:.3f} s" + ("   untracked" if xy is None else "")
        draw.text((x + 6, y + height - 21), label, font=font(14), fill="white")
    trail = out / "follow.png"
    sheet.save(trail)
    images = [(trail, {"followed_picture_pts_us": [frames[i].pts for i in ids]})]
    current = report["current"]
    # Keep the originally selected detail available for checking identity, even after a lost match.
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        anchor = frames[0]
        xy = report["anchor"]["point"]
        first = tmp / "first.png"
        basis = exposure_detail(anchor, xy, first, original)
        rows = [(first, f"Selected at {anchor.pts / 1e6:.3f} s")]
        description = {"selected_detail_pts_us": anchor.pts, "selected_detail_source": basis}
        match = report["last_located"]
        if match["pts_us"] != anchor.pts:
            frame = next(f for f in reversed(frames) if f.pts == match["pts_us"])
            latest = tmp / "last.png"
            basis = exposure_detail(frame, match["point"], latest, original)
            current_match = current is not None and current["point"] is not None
            label, key = ("Estimated", "followed") if current_match else ("Last located", "last_located")
            rows.append((latest, f"{label} at {frame.pts / 1e6:.3f} s"))
            description.update({f"{key}_detail_pts_us": frame.pts, f"{key}_detail_source": basis})
        panels = []
        for file, label in rows:
            with Image.open(file) as panel:
                panel.thumbnail((1000, 480), Image.Resampling.LANCZOS)
                panels.append((panel.copy(), label))
        pair = Image.new("RGB", (max(panel.width for panel, _ in panels),
                                 sum(panel.height + 24 for panel, _ in panels)), (20, 22, 26))
        top = 0
        for panel, label in panels:
            ImageDraw.Draw(pair).text((5, top + 2), label, font=font(14), fill="white")
            pair.paste(panel, (0, top + 24))
            top += panel.height + 24
        detail = out / "detail.png"
        pair.save(detail)
    images.append((detail, description))
    return images
