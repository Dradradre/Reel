"""Inspect a region of a delivered picture at its original resolution."""
import json
import math
from contextlib import closing
from pathlib import Path
import tempfile
from fractions import Fraction

from PIL import Image, ImageDraw

if __package__:
    from .action_view import action_frames, action_views
    from .common import RecordedSource, font, probe, reel_source, sample_boundary
    from .frames import save_frame
else:
    from action_view import action_frames, action_views
    from common import RecordedSource, font, probe, reel_source, sample_boundary
    from frames import save_frame


def fixation_box(point):
    x, y = point
    left, top = min(max(0., x - 1 / 6), 2 / 3), min(max(0., y - 1 / 6), 2 / 3)
    return [left, top, left + 1 / 3, top + 1 / 3]


def focus_motion(out, source, packet, box, directory):
    """Show native crops of the exposures already released for this turn."""
    source = Path(source)
    if source.is_dir():
        source = reel_source(source, json.loads((source / "meta.json").read_text()))
    start, end = packet["start_s"], packet["end_s"]
    picture_end = packet.get("picture_end_s", end)
    published_pts = {pts for page in packet["pages"] for pts in page["pts_us"]}
    size, frames = action_frames(source, probe(source), start, min(end, picture_end), box)
    audio = packet.get("audio")
    wav = directory / audio["wav"] if audio else None
    first_sample = audio.get("first_sample_on_film", sample_boundary(start, audio["rate_hz"])) if audio else 0
    with closing(frames):
        exposures = (frame for frame in frames if frame[0] in published_pts)
        return action_views(out, exposures, size, start, end, picture_end, wav, first_sample, box,
                            chronological=True)["pages"]


def pixel_box(box, size):
    w, h = size
    return [math.floor(box[0] * w), math.floor(box[1] * h), math.ceil(box[2] * w), math.ceil(box[3] * h)]


def focus_still(path, point, box, dest):
    """Locate the selected detail in the full picture and preserve its pixels alongside it."""
    with Image.open(path) as image:
        bounds = pixel_box(box, image.size)
        detail = image.crop(bounds).convert("RGB")
        detail.thumbnail((1280, 960), Image.Resampling.LANCZOS)
        source_size = list(image.size)
        image.thumbnail((192, 144), Image.Resampling.LANCZOS)
        overview = image.convert("RGB")
    left, top, right, bottom = pixel_box(box, overview.size)
    draw = ImageDraw.Draw(overview)
    draw.rectangle((left, top, right - 1, bottom - 1), outline=(255, 195, 44), width=2)
    x = round(point[0] * (overview.width - 1))
    y = round(point[1] * (overview.height - 1))
    draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(255, 195, 44))
    margin = 8
    inset = overview.width + margin
    result = Image.new("RGB", (inset + detail.width + margin, max(overview.height, detail.height + 2 * margin)),
                       (18, 21, 25))
    result.paste(overview)
    result.paste(detail, (inset, margin))
    # Mark the row and column outside the crop. A point near the picture edge is not at the crop centre.
    pos = []
    for value, size, lo, hi, length in zip(point, source_size, bounds[:2], bounds[2:], detail.size):
        pixel = min(size - 1, int(value * size))
        pos.append(min(length - 1, max(0, int((pixel + .5 - lo) * length / (hi - lo)))))
    dx, dy = inset + pos[0], margin + pos[1]
    draw = ImageDraw.Draw(result)
    for line in ((dx, 0, dx, margin - 1), (dx, margin + detail.height, dx, 2 * margin + detail.height - 1),
                 (inset - margin, dy, inset - 1, dy), (inset + detail.width, dy, result.width - 1, dy)):
        draw.line(line, fill=(255, 195, 44), width=2)
    result.save(dest)
    return {"source_size": source_size, "pixel_box": bounds,
            "point_in_detail": pos, "detail_in_image": [inset, margin, inset + detail.width, margin + detail.height]}


def frame_detail(path, box, out, stem=None):
    if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        raise ValueError("a box needs 0 <= left < right <= 1 and 0 <= top < bottom <= 1")
    out = Path(out)
    with Image.open(path) as image:
        w, h = image.size
        pixels = pixel_box(box, image.size)
        filename = (stem or Path(path).stem) + "_detail_" + "-".join(str(x) for x in pixels) + ".png"
        with tempfile.TemporaryDirectory(dir=out) as temp:
            crop = Path(temp) / "crop.png"
            image.crop(pixels).save(crop)
            crop.replace(out / filename)
    return {"file": filename, "box": [pixels[0] / w, pixels[1] / h, pixels[2] / w, pixels[3] / h],
            "pixel_box": pixels, "source_size": [w, h]}


def inspect_turn(out, box):
    out = Path(out)
    view = json.loads((out / "view.json").read_text())
    frame = view.get("current_frame")
    if view.get("schema") != "reel.play.v1" or not frame:
        raise ValueError("no current picture in this playback interval")
    result = frame_detail(out / frame["file"], box, out)
    result["file"] = str((out / result["file"]).resolve())
    result["pts_us"] = frame["pts_us"]
    return result


def inspect_past(out, source, at, box):
    """Crop native pixels at a PTS that the player has already published."""
    out = Path(out)
    directory = out
    view = json.loads((directory / "view.json").read_text())
    if not 0 <= at < view["end_s"]:
        raise ValueError("look time must fall within the delivered viewing")
    while at < view["start_s"]:
        serial = directory.name.removeprefix("turn_")
        if not serial.isdigit() or int(serial) <= 1:
            raise ValueError("the earlier viewing is unavailable")
        directory = directory.parent / f"turn_{int(serial) - 1:05}"
        previous = json.loads((directory / "view.json").read_text())
        if previous.get("schema") != "reel.play.v1" or previous["end_s"] != view["start_s"]:
            raise ValueError("the earlier viewing is unavailable")
        view = previous
    if at >= view.get("picture_end_s", view["end_s"]):
        raise ValueError("no picture on display at this time")
    us = int(Fraction(str(at)) * 1_000_000)
    pts = next((p for page in reversed(view["pages"]) for p in reversed(page["pts_us"]) if p <= us), None)
    if pts is None:
        raise ValueError("no picture on display at this time")
    if view.get("current_frame") and view["current_frame"]["pts_us"] == pts:
        crop = frame_detail(directory / view["current_frame"]["file"], box, out, stem=f"frame_{pts}")
    else:
        if source is None:
            raise ValueError("the original recording is unavailable for this picture")
        original = source if isinstance(source, RecordedSource) else RecordedSource(source)
        with tempfile.TemporaryDirectory(dir=out) as tmp:
            with original.reading() as movie:
                frame = save_frame(movie, pts, Path(tmp) / f"frame_{pts}.png")
            crop = frame_detail(frame, box, out)
    return {**crop, "file": str((out / crop["file"]).resolve()), "pts_us": pts}


def recent_details(source, dest, history, current):
    """Show the last second of fixation crops. Each crop uses the picture displayed at its tick."""
    # Count back from the newest tick in half-second steps, then show them in film order.
    samples = list(history)[::-3][::-1]
    w, h = 320, 200
    strip = Image.new("RGB", (w * len(samples), h + 20), (16, 17, 19))
    draw = ImageDraw.Draw(strip)
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        for i, sample in enumerate(samples):
            pts = sample["pts_us"]
            if current and pts == current["pts_us"]:
                path = dest / current["file"]
            else:
                path = Path(tmp) / f"frame_{pts}.png"
                if not path.is_file():
                    save_frame(source, pts, path)
            with Image.open(path) as image:
                # A uniform picture looks the same in any part of the frame.
                box = fixation_box(sample["point"] or [.5, .5])
                part = image.crop(pixel_box(box, image.size))
            part.thumbnail((w, h))
            strip.paste(part, (i * w + (w - part.width) // 2, (h - part.height) // 2))
            draw.text((i * w + 4, h + 1), f"{sample['tick'] / 6:.3f} s", font=font(14), fill="white")
    filename = "gaze_recent.png"
    strip.save(dest / filename)
    return {"file": filename, "samples": samples}


def gaze_detail(helper, dest, packet, size, end, source, history):
    request = {"start_s": packet["start_s"], "end_s": end, "current_frame": packet["current_frame"], "size": size,
               "pages": [{**page, "file": str((dest / page["file"]).resolve())} for page in packet["pages"]]}
    result = helper.request(request)
    samples = result.get("samples", [])
    for sample in samples:
        if history and history[-1]["tick"] + 1 != sample["tick"]:
            history.clear()
        history.append(sample)
    if result.get("status") != "available":
        history.clear()
    if history:
        try:
            result["recent_details"] = recent_details(source, dest, history, packet["current_frame"])
        except (ValueError, OSError) as error:
            result["recent_details"] = {"status": "unavailable", "reason": str(error)}
    if result.get("box"):
        detail = frame_detail(dest / packet["current_frame"]["file"], result.pop("box"), dest)
        result["detail"] = detail
    return result
