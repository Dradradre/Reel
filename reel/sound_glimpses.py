"""Show the whole picture around a measured change in the recorded sound."""
from __future__ import annotations

from bisect import bisect_right
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

if __package__:
    from .frames import cell_box
    from .common import font, open_recording, sample_boundary, save_observation_png
else:
    from frames import cell_box
    from common import font, open_recording, sample_boundary, save_observation_png


def band_levels(samples, width, hop, rate):
    frequencies = np.fft.rfftfreq(width, 1 / rate)
    cutoffs = [hz for hz in (125, 250, 500, 1000, 2000, 4000, 8000) if hz < rate / 2]
    edges = np.unique(np.r_[0, np.searchsorted(frequencies, cutoffs), len(frequencies)])
    hz = [round(float(frequencies[i])) for i in edges[:-1]] + [rate / 2]
    taper = np.hanning(width)
    levels = []
    for channel in samples.T:
        frames = np.lib.stride_tricks.sliding_window_view(channel, width)[::hop]
        power = np.abs(np.fft.rfft(frames * taper, axis=-1)) ** 2
        bands = np.sqrt(np.add.reduceat(power, edges[:-1], axis=-1)) / width
        levels.append(20 * np.log10(np.maximum(bands, 1e-4)))
    return np.stack(levels, axis=1), hz


class SoundChanges:
    """Register a change when its samples arrive, independently of playback pauses."""
    def __init__(self):
        self.tail = None
        self.end = None
        self.rate = None
        self.previous = None
        self.history = deque(maxlen=50)
        self.last_above = None

    def scan_samples(self, samples, rate, first_sample):
        hop = max(1, round(rate * .02))
        width = 2 * hop
        if (self.end != first_sample or self.rate != rate or self.tail is None
                or self.tail.shape[1] != samples.shape[1]):
            self.tail = samples[:0]
            self.previous = None
            self.history.clear()
            self.last_above = np.full(samples.shape[1], -rate, dtype=np.int64)
        origin = first_sample - len(self.tail)
        data = np.concatenate((self.tail, samples))
        offset = (-origin) % hop
        self.end, self.rate = first_sample + len(samples), rate
        if len(data) < offset + width:
            self.tail = data[offset:].copy()
            return []
        levels, hz = band_levels(data[offset:], width, hop, rate)
        self.tail = data[offset + len(levels) * hop:].copy()
        events = []
        for index, level in enumerate(levels):
            at = origin + offset + index * hop + width - 1
            previous, self.previous = self.previous, level
            if previous is None:
                continue
            delta = level - previous
            strength = np.abs(delta).mean(axis=1)
            threshold = 3.
            if self.history:
                history = np.asarray(self.history)
                median = np.median(history, axis=0)
                threshold = np.maximum(3., median + 3 * np.median(np.abs(history - median), axis=0))
            self.history.append(strength)
            above = strength >= threshold
            # One cue per burst in each channel. A fresh cue needs a quiet gap in the change trace.
            new = above & (at - self.last_above >= 8 * hop)
            self.last_above[above] = at
            groups = {}
            for channel in np.flatnonzero(new):
                # Keep simultaneous different changes in separate channels. Identical stereo
                # changes (including opposite polarity) can share a description.
                key = delta[channel].tobytes()
                groups.setdefault(key, []).append(int(channel))
            for channels in groups.values():
                ch = channels[0]
                event = {"time_s": at / rate, "channels": [i + 1 for i in channels],
                         "band_change_db": round(float(strength[ch]), 1)}
                for sign, name in ((1, "rise"), (-1, "fall")):
                    band = int(np.argmax(sign * delta[ch]))
                    amount = sign * delta[ch, band]
                    if amount >= 3:
                        event[name] = {"hz": hz[band:band + 2], "db": round(float(amount), 1)}
                events.append(event)
        return events

    def scan(self, wav, first_sample):
        events = []
        with open_recording(wav) as recording:
            for block in recording.blocks(blocksize=3 * recording.samplerate, dtype="float64", always_2d=True):
                events.extend(self.scan_samples(block, recording.samplerate, first_sample))
                first_sample += len(block)
        return events


def sheet_picture(pages, size):
    """Look up a delivered frame by its PTS without reading the original movie again."""
    cells = [(int(pts), page["file"], i) for page in pages
             for i, pts in enumerate(page["pts_us"], start=page.get("first_cell", 0))]
    times = [cell[0] for cell in cells]
    cache = {}
    def get(us):
        index = bisect_right(times, us) - 1
        if index < 0:
            return None
        pts, path, i = cells[index]
        if path not in cache:
            cache.clear()
            with Image.open(path) as image:
                cache[path] = image.convert("RGB")
        return pts, cache[path].crop(cell_box(i, size))
    return get


def sound_glimpses(out, events, picture, start, end, picture_end):
    """Preserve an empty image interval, including a sound change outside the picture track."""
    out = Path(out)
    lo = sample_boundary(start, 1_000_000)
    hi = sample_boundary(end, 1_000_000) - 1
    highlights = {}
    for event in events:
        window = int((event["time_s"] - start) // 3)
        if window not in highlights or event.get("band_change_db", 0) > highlights[window].get("band_change_db", 0):
            highlights[window] = event
    events = [dict(event) for event in highlights.values()]
    for event in events:
        t = event["time_s"]
        centre = round(t * 1_000_000)
        probes = sorted({max(lo, min(hi, centre + shift)) for shift in (-160_000, 0, 160_000)})
        views = [(us, picture(us) if us < picture_end * 1e6 else None) for us in probes]
        event["frames"] = [{"at_s": us / 1e6, "pts_us": frame[0] if frame else None} for us, frame in views]
        active = [frame[1] for us, frame in views if frame is not None]
        if not active:
            continue
        w, h = active[0].size
        difference = len(active) == len(views) and len(active) >= 2
        cell = max(w, 140)
        strip = Image.new("RGB", (cell * (len(views) + int(difference)), h + 26), (16, 17, 19))
        labels = ImageDraw.Draw(strip)
        for i, (us, frame) in enumerate(views):
            if frame:
                strip.paste(frame[1], (i * cell + (cell - w) // 2, 0))
            labels.text((i * cell + 3, h + 4), f"{us / 1e6:.3f} s", font=font(13), fill="white")
        if difference:
            diff = Image.new("L", (w, h))
            for a, b in zip(active, active[1:]):
                diff = ImageChops.lighter(diff, ImageChops.difference(a, b).convert("L"))
            strip.paste(diff.point(lambda x: min(255, 3 * x)), (len(views) * cell + (cell - w) // 2, 0))
            labels.text((len(views) * cell + 3, h + 4), "picture change x3", font=font(13), fill="white")
        event["file"] = save_observation_png(out, strip, "sound_view")
    return {"events": events,
            "basis": "A marked change in recorded band levels selects the times. Frames cover the whole display. "
                     "The last panel, when present, shows amplified pixel changes. A source may be offscreen."}
