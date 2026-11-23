"""Show how the pixels along two fixed lines in a released picture change over time."""
import math

import numpy as np
from PIL import Image, ImageDraw

if __package__:
    from .common import font, save_observation_png
else:
    from common import font, save_observation_png


def validate_motion_point(point):
    if len(point) != 2 or any(not 0 <= value <= 1 for value in point):
        raise ValueError("motion_point needs [x,y] within the picture, from 0 to 1")


def _draw_slice(out, rows, end, still, point, size):
    width, height = size
    x, y = point
    # Keep spacing proportional to display time except when a subpixel exposure needs a column.
    span = 540
    start = rows[0][0]
    edges = [0]
    for i, (at, _, _) in enumerate(rows[1:], 1):
        ideal = round((at - start) * span / (end - start))
        edges.append(min(span - len(rows) + i, max(edges[-1] + 1, ideal)))
    columns = np.repeat(np.arange(len(rows)), np.diff([*edges, span]))
    left = 218
    top = 30
    lanes = (max(64, height), max(64, width))
    canvas = Image.new("RGB", (left + span + 8, top + sum(lanes) + 44), (17, 19, 23))
    draw = ImageDraw.Draw(canvas)
    draw.text((7, 5), "Line locations", font=font(14), fill="white")
    draw.text((left, 5), "Same lines through time", font=font(14), fill="white")
    preview = still.copy()
    preview.thumbnail((left - 14, sum(lanes)), Image.Resampling.LANCZOS)
    sw, sh = preview.size
    px = 7 + min(sw - 1, int((x + .5) * sw / width))
    py = top + min(sh - 1, int((y + .5) * sh / height))
    canvas.paste(preview, (7, top))
    cyan, amber = (111, 227, 245), (255, 190, 86)
    draw.line((px, top, px, top + sh - 1), fill=cyan)
    draw.line((7, py, 7 + sw - 1, py), fill=amber)
    for index, (label, color) in enumerate((("Vertical line: top to bottom", cyan),
                                            ("Horizontal line: left to right", amber)), 1):
        # Each column preserves spatial order. Only the spatial dimension is resized.
        band = Image.fromarray(np.stack([row[index] for row in rows], axis=1)[:, columns])
        lane = lanes[index - 1]
        offset = top if index == 1 else top + lanes[0] + 12
        canvas.paste(band.resize((span, lane), Image.Resampling.NEAREST), (left, offset))
        draw.text((7, canvas.height - 41 + 17 * (index - 1)), label, font=font(12), fill=color)
        draw.line((left - 2, offset, left - 2, offset + lane - 1), fill=color, width=2)
    axis = top + sum(lanes) + 12
    for fraction in (0, .25, .5, .75, 1):
        at = start + (end - start) * fraction
        # Map even on an unusual clock where short exposures needed extra columns.
        position = np.interp(at, [row[0] for row in rows] + [end], [*edges, span])
        xpos = left + min(span - 1, round(position))
        draw.line((xpos, axis, xpos, axis + 4), fill="white")
        label = f"{at:.3f} s"
        align = min(max(left, xpos - 24), canvas.width - draw.textlength(label, font=font(12)) - 3)
        draw.text((align, axis + 5), label, font=font(12), fill="white")
    return {"file": save_observation_png(out, canvas, "motion_slice"), "start_s": start, "end_s": end,
            "pixel": [x, y], "size": list(size), "exposures": len(rows)}


def motion_slices(out, frames, size, start, end, picture_end, point):
    """Use only displayed exposures. Retain a picture held at the start and split long runs into pages."""
    validate_motion_point(point)
    stop = min(float(end), float(picture_end))
    start = float(start)
    if not start < stop:
        return []
    w, h = size
    x, y = min(w - 1, math.floor(point[0] * w)), min(h - 1, math.floor(point[1] * h))
    pages, rows = [], []
    still = pending = None

    def append(at, pixels):
        nonlocal rows, still
        if len(rows) == 240:
            pages.append(_draw_slice(out, rows, at, still, (x, y), size))
            rows = []
        rgb = np.frombuffer(pixels, np.uint8).reshape(h, w, 3)
        if not rows:
            still = Image.fromarray(rgb)
        rows.append((at, rgb[:, x].copy(), rgb[y].copy()))

    for pts, _, pixels in frames:
        at = pts / 1_000_000
        if at >= stop:
            break
        at = max(start, at)
        if pending is not None and at > pending[0]:
            append(*pending)
        pending = at, pixels
    if pending is not None:
        append(*pending)
        pages.append(_draw_slice(out, rows, stop, still, (x, y), size))
    return pages
