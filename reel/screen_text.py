"""Read text in displayed pixels and keep its exposure times on the film clock."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import math

from PIL import Image

if __package__:
    from .common import probe, sample_boundary, video_end
    from .frames import native_frames
else:
    from common import probe, sample_boundary, video_end
    from frames import native_frames


def vision_engine():
    from ocrmac import ocrmac
    return ocrmac.OCR


def recognize(image, engine):
    rows = engine(image, language_preference=["ko-KR", "en-US"], recognition_level="accurate").recognize()
    lines = []
    for text, confidence, (x, y, width, height) in rows:
        if text.strip():
            lines.append(dict(text=text.strip(), x=x, y=1 - y - height, w=width, h=height, c=confidence))
    return lines


def readable(line):
    return line["c"] >= .9 and line["h"] >= .032 and any(c.isalnum() for c in line["text"])


def read_crop(path):
    try:
        with Image.open(path) as image:
            lines = recognize(image, vision_engine())
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        return {"status": "unavailable", "reason": str(error)}
    return {"status": "available", "lines": [line for line in lines if readable(line)],
            "basis": "Apple Vision estimates on this crop. Coordinates are relative to its top left."}


def displayed_pictures(frames, end_us):
    """An exposure lasts until its successor. Only the last frame at a tied PTS is displayed."""
    previous = None
    for pts, _, pixels in frames:
        if pts >= end_us:
            break
        if previous is not None and pts > previous[0]:
            yield previous[0], pts, previous[1]
        previous = (pts, pixels)
    if previous is not None:
        yield previous[0], end_us, previous[1]


def unchanged_pictures(frames, end_us):
    start = last = pixels = None
    for pts, stop, current in displayed_pictures(frames, end_us):
        if pixels is not None and pixels != current:
            yield start, pts, pixels
            start = None
        if start is None:
            start = pts
            pixels = current
        last = stop
    if pixels is not None:
        yield start, last, pixels


def read_pictures(source, size, end_us):
    # Limit both queued RGB buffers and concurrent Vision requests on long films.
    engine = vision_engine()

    def read(pixels):
        try:
            return recognize(Image.frombytes("RGB", size, pixels), engine)
        except (OSError, RuntimeError, ValueError):
            return None

    with closing(native_frames(source, *size, stop_us=end_us)) as frames, ThreadPoolExecutor(2) as workers:
        pending = deque()
        for start, stop, pixels in unchanged_pictures(frames, end_us):
            pending.append((start, stop, workers.submit(read, pixels)))
            if len(pending) == 2:
                a, b, job = pending.popleft()
                yield a, b, job.result()
        for a, b, job in pending:
            yield a, b, job.result()


def overlap(a, b):
    width = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    height = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return max(0., width) * max(0., height)


def screen_intervals(source, duration, log, step=1., subject=None):
    info = probe(source)
    end = min(duration, video_end(source, info))
    aspect = info["display_aspect"]
    side = min(1280, max(info["w"], info["h"]))
    size = (max(1, round(side * min(1, aspect))), max(1, round(side * min(1, 1 / aspect))))
    aliases = [name.strip().casefold() for name in (subject or "").split(",") if name.strip()]
    items, tags, previous = [], [], []
    failed = 0
    for first, last, lines in read_pictures(source, size, sample_boundary(end, 1_000_000)):
        start, stop = max(0., first / 1_000_000), min(end, last / 1_000_000)
        if start >= stop:
            continue
        if lines is None:
            failed += 1
            previous = []
            continue
        active = []
        for line in lines:
            if aliases and line["h"] < .032 and len(line["text"]) <= 14 and any(
                    name in line["text"].casefold() for name in aliases):
                # Sample name-tag positions at the same cadence as the gaze lane.
                for i in range(max(0, math.ceil((start - .25) / step)), math.ceil((stop - .25) / step)):
                    tags.append(dict(t=.25 + i * step, x=line["x"] + line["w"] / 2, y=line["y"] + line["h"] / 2))
            if not readable(line):
                continue
            matched = max((pair for pair in previous if pair[0]["text"] == line["text"]),
                          key=lambda pair: overlap(pair[0], line), default=None)
            if matched is not None and overlap(matched[0], line) > 0:
                previous.remove(matched)
                item = matched[1]
                item["e"] = stop
                item["c"] = min(item["c"], line["c"])
            else:
                item = dict(line, t=start, e=stop)
                items.append(item)
            active.append((line, item))
        previous = active
    if failed:
        log(f"  on-screen text unavailable for {failed} picture intervals")
    log(f"  on-screen text done, {len(items)} intervals, {len(tags)} name-tag sightings")
    return items, sorted(tags, key=lambda tag: tag["t"])
