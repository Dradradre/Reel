"""Read the actual sequence of displayed pictures on the film clock."""
from __future__ import annotations

from collections import deque
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading

import numpy as np
from PIL import Image, ImageDraw

if __package__:
    from .common import display_scale, font, hdr_picture, probe, reel_source, sample_boundary, video_end
else:
    from common import display_scale, font, hdr_picture, probe, reel_source, sample_boundary, video_end


def native_frames(source, width, height, start_us=0, stop_us=None, *, box=None):
    """Yield (PTS in microseconds, duration in microseconds, RGB bytes), retaining a frame held at start."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-nostdin", "-threads:v", "2",
           "-filter_threads", "2", "-copyts", "-start_at_zero"]
    if start_us:
        cmd.extend(["-noaccurate_seek", "-ss", f"{start_us}us"])
    filters = ["settb=AVTB"]
    if stop_us is not None:
        filters.append(f"trim=end_pts={stop_us}")
    geometry = f"{width}:{height}:flags=area"
    if box is not None:
        left, top, right, bottom = box
        # FFmpeg autorotates first. Crop display-relative edges before reducing the picture.
        if hdr_picture(source):
            # Convert before the first 8-bit RGB step so small crops retain their original colour.
            filters.append(display_scale(source, "iw:ih"))
        filters.extend(["format=rgb24",
                        f"crop=w='ceil(iw*{right})-floor(iw*{left})':h='ceil(ih*{bottom})-floor(ih*{top})':"
                        f"x='floor(iw*{left})':y='floor(ih*{top})':exact=1"])
        scale = f"scale={geometry}"
    else:
        scale = display_scale(source, geometry)
    filters.extend([scale, "format=rgb24", "showinfo=checksum=0"])
    cmd.extend(["-i", str(source), "-map", f"0:{probe(source)['video_index']}", "-vf", ",".join(filters), "-fps_mode", "passthrough",
                "-c:v", "rawvideo", "-threads:v", "2", "-f", "rawvideo", "-"])
    clock = queue.Queue()
    diagnostics = deque(maxlen=8)
    stamp = re.compile(rb"\bn:\s*\d+\s+pts:\s*(-?\d+)\s+pts_time:\S+(?:\s+duration:\s*(\d+))?")

    def read_clock(stream):
        try:
            for line in stream:
                if b"Parsed_showinfo_" in line:
                    match = stamp.search(line)
                    if match:
                        clock.put((int(match[1]), int(match[2] or 0)))
                else:
                    diagnostics.append(line.decode(errors="replace").strip())
        finally:
            clock.put(None)

    size = width * height * 3
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        worker = threading.Thread(target=read_clock, args=(proc.stderr,), daemon=True)
        worker.start()
        held = None
        try:
            while pixels := proc.stdout.read(size):
                if len(pixels) != size:
                    raise ValueError("incomplete video frame from ffmpeg")
                try:
                    timing = clock.get(timeout=15)
                except queue.Empty as error:
                    raise ValueError("ffmpeg returned pixels without a presentation timestamp") from error
                if timing is None:
                    raise ValueError("ffmpeg returned pixels without a presentation timestamp")
                pts, duration = timing
                # Multiple pictures can have the same presentation time. Only the last
                # is on screen; keep the preceding visible picture if it spans the start.
                if held is not None and pts != held[0] and pts > start_us:
                    yield held
                held = (pts, duration, pixels)
            if proc.wait():
                worker.join()
                raise ValueError("ffmpeg could not decode the picture: " + " ".join(diagnostics))
            if held is not None:
                yield held
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.stdout.close()
            proc.wait()
            worker.join()


def mean_rgb_change(current, previous):
    """Mean absolute difference over displayed RGB samples, before spatial averaging."""
    return float(np.abs(np.subtract(current, previous, dtype=np.int16)).mean())


def save_frame(source, us, path):
    # Seek to a keyframe, retain the original presentation clock, and exclude later frames.
    # Rounding up when resampling makes the tick at zero use the last frame at or before it.
    # Correct non-square pixels by expanding one axis so both retain the original detail.
    scale = display_scale(source, "w='round(iw*max(1,sar))':h='round(ih/if(gt(sar,0),min(1,sar),1))'")
    filters = (f"settb=AVTB,trim=end_pts={us + 1},setpts=PTS-{us},fps=1:start_time=0:round=up:eof_action=pass,"
               f"{scale},setsar=1")
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        image = Path(tmp) / "frame.png"
        result = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-copyts", "-start_at_zero", "-noaccurate_seek",
                                 "-threads", "2", "-filter_threads", "2", "-ss", f"{us}us", "-i", str(source),
                                 "-map", f"0:{probe(source)['video_index']}", "-vf", filters, "-frames:v", "1", "-c:v", "png", "-threads", "2",
                                 "-update", "1", str(image)], stderr=subprocess.PIPE)
        if result.returncode:
            raise ValueError(result.stderr.decode(errors="replace").strip())
        if not image.is_file() or not image.stat().st_size:
            raise ValueError(f"no video frame on display at {us / 1e6:g} seconds")
        image.replace(path)
    return path


def frame_sequence(out, start, end, *, allow_empty=False):
    out = Path(out)
    meta = json.loads((out / "meta.json").read_text())
    limit = meta.get("until", meta["dur"])
    if not 0 <= start < end <= limit:
        raise ValueError(f"frame range must be within 0 to {limit:g} seconds")
    source = reel_source(out, meta)
    info = probe(source)
    picture_end = meta["picture_end_s"] if "picture_end_s" in meta else video_end(source, info)
    return read_sequence(out, source, info, start, end, picture_end, allow_empty=allow_empty)


def read_sequence(out, source, info, start, end, picture_end, *, allow_empty=False):
    if start >= picture_end:
        if allow_empty:
            return {"schema": "reel.frames.v1", "start_s": start, "end_s": end, "pages": []}
        raise ValueError("no picture on display in this interval")
    width, height = sheet_size(info)
    frames = native_frames(source, width, height, int(Fraction(str(start)) * 1_000_000),
                           sample_boundary(end, 1_000_000))
    pages = frame_sheets(out, width, height, frames)
    if not pages and not allow_empty:
        raise ValueError("no picture on display in this interval")
    return {"schema": "reel.frames.v1", "start_s": start, "end_s": end, "pages": pages}


def sheet_size(info):
    aspect = info["display_aspect"]
    width = max(1, min(384, round(240 * aspect)))
    height = max(1, min(240, round(384 / aspect)))
    return width, height


SHEET_COLUMNS, SHEET_ROWS = 3, 4


def cell_box(index, size):
    width, height = size
    cell_width = max(136, width)
    x = index % SHEET_COLUMNS * cell_width + (cell_width - width) // 2
    y = index // SHEET_COLUMNS * (height + 24)
    return x, y, x + width, y + height


def frames_from_sheets(pages, size):
    for page in pages:
        with Image.open(page["file"]) as sheet:
            for index, pts in enumerate(page["pts_us"], start=page.get("first_cell", 0)):
                yield pts, 0, sheet.crop(cell_box(index, size)).convert("RGB").tobytes()


def frame_sheets(out, width, height, frames):
    """Write timestamped sheets from frames already decoded on the presentation clock."""
    pages = []
    digest = hashlib.sha256()
    cell_width = max(136, width)
    with tempfile.TemporaryDirectory(dir=out) as temp:
        pending = Path(temp) / "pages"
        pending.mkdir()
        sheet = None
        cols, rows = SHEET_COLUMNS, SHEET_ROWS
        for i, (pts, _, pixels) in enumerate(frames):
            cell = i % (cols * rows)
            if cell == 0:
                sheet = Image.new("RGB", (cols * cell_width, rows * (height + 24)), (16, 17, 19))
                draw = ImageDraw.Draw(sheet)
                pages.append({"file": f"page_{len(pages) + 1:03d}.png", "pts_us": []})
            x, y = (cell % cols) * cell_width, (cell // cols) * (height + 24)
            sheet.paste(Image.frombytes("RGB", (width, height), pixels), (x + (cell_width - width) // 2, y))
            draw.text((x + 4, y + height + 3), f"{pts / 1e6:.6f} s", font=font(14), fill=(232, 233, 237))
            pages[-1]["pts_us"].append(pts)
            if cell + 1 == cols * rows:
                sheet.save(pending / pages[-1]["file"])
        if not pages:
            return []
        last_count = len(pages[-1]["pts_us"])
        if last_count < cols * rows:
            sheet.crop((0, 0, min(cols, last_count) * cell_width, math.ceil(last_count / cols) * (height + 24))).save(
                pending / pages[-1]["file"])
        for page in pages:
            digest.update(hashlib.sha256((pending / page["file"]).read_bytes()).digest())
        target = out / f"frames_{digest.hexdigest()[:24]}"
        target.mkdir(exist_ok=True)
        # Each replacement is atomic. Repeated or concurrent reads share identical sheets.
        for page in pages:
            (pending / page["file"]).replace(target / page["file"])
    for page in pages:
        page["file"] = str((target / page["file"]).resolve())
    return pages
