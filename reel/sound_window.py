"""Draw a bounded recording on its native sample clock with an optional marked interval."""
from fractions import Fraction
from itertools import chain
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.fft import rfft

if __package__:
    from .turn_audio import preceding_turns, sample_at
    from .common import font, lut, open_recording, sample_boundary, save_observation_png
else:
    from turn_audio import preceding_turns, sample_at
    from common import font, lut, open_recording, sample_boundary, save_observation_png


def validate_mark(start, end, mark):
    if mark is not None and (start is None or end is None or not 0 < Fraction(str(end)) - Fraction(str(start)) <= 8):
        raise ValueError("give a replay range up to eight seconds long when marking a sound")
    if mark is not None and (len(mark) != 2 or not start <= mark[0] < mark[1] <= end):
        raise ValueError("mark two increasing film times inside the replay")


def marked_offsets(mark, start, end, first, count, rate, turn_directory):
    boundaries = {start: first, end: first + count}
    if turn_directory is not None:
        directory = Path(turn_directory)
        current = json.loads((directory / "view.json").read_text())
        history = preceding_turns(directory, current, mark[0])
        for packet in chain((current,), (view for _, view in history)):
            for t in mark:
                if t in (packet["start_s"], packet["end_s"]) and packet.get("audio"):
                    boundaries[t] = sample_at(packet, t)
    return [min(count, max(0, boundaries.get(t, sample_boundary(t, rate)) - first)) for t in mark]


def sound_window(out, wav, start, end, first, mark=None, turn_directory=None):
    """Plot the recording once, using only samples in it. Colours share a scale across channels."""
    validate_mark(start, end, mark)
    if not 0 < Fraction(str(end)) - Fraction(str(start)) <= 8:
        raise ValueError("choose a sound window up to eight seconds long")
    width, bands, left, top, stride = 768, 90, 72, 38, 124
    with open_recording(wav) as recording:
        rate, channels, count = recording.samplerate, recording.channels, len(recording)
        if not count:
            raise ValueError("the sound window contains no samples")
        # Magnitude of a sinusoid at a Fourier bin, relative to a full-scale sample.
        size = max(4, round(rate * .025))
        taper = np.hanning(size + 1)[:-1].astype(np.float32)
        spectral_edges = np.geomspace(min(40., rate / 4), rate / 2, bands + 1)
        bins = np.minimum(np.ceil(spectral_edges * size / rate).astype(int), size // 2)
        bins[0] = 0
        # A narrow display band may share its Fourier bin with adjacent rows.
        unique, inverse = np.unique(bins[:-1], return_inverse=True)
        palette = lut()
        band_image = np.zeros((channels, bands, width), dtype=np.uint8)
        peaks = np.zeros((width, channels))
        edges = (np.arange(width + 1, dtype=np.int64) * count + width - 1) // width
        centers = ((2 * np.arange(width, dtype=np.int64) + 1) * count) // (2 * width)
        for x in range(0, width, 32):
            stop = min(x + 32, width)
            offsets = centers[x:stop] - size // 2
            lo = min(edges[x], offsets[0])
            hi = max(edges[stop], offsets[-1] + size)
            begin = max(0, lo)
            recording.seek(begin)
            chunk = recording.read(min(hi, count) - begin, dtype="float32", always_2d=True)
            chunk = np.pad(chunk, ((begin - lo, max(0, hi - count)), (0, 0)))
            for j in range(x, stop):
                if edges[j] < edges[j + 1]:
                    peaks[j] = np.max(np.abs(chunk[edges[j] - lo:edges[j + 1] - lo]), axis=0)
            windows = np.lib.stride_tricks.sliding_window_view(chunk, size, axis=0)
            for ch in range(channels):
                spectrum = np.abs(rfft(windows[offsets - lo, ch] * taper, axis=-1)) * (2 / taper.sum())
                spectrum[:, 0] *= .5
                if size % 2 == 0:
                    spectrum[:, -1] *= .5
                maximum = np.maximum.reduceat(spectrum, unique, axis=1)[:, inverse]
                db = 20 * np.log10(np.maximum(maximum, 1e-4))
                band_image[ch, :, x:stop] = np.rint(np.clip((db + 80) / 80, 0, 1) * 255).T[::-1]

    image = Image.new("RGB", (left + width + 8, top + channels * stride + 18), (18, 21, 25))
    draw = ImageDraw.Draw(image)
    label = f"Recording   {start:g} – {end:g} s"
    report = {"start_s": start, "end_s": end, "rate_hz": rate, "first_sample_on_film": first,
              "samples_per_channel": count, "channels": channels, "fft_samples": size,
              "basis": "Frequency increases upwards on a logarithmic scale. The bottom row includes DC. "
                       "All channels use the same −80 to 0 dBFS scale. The peak trace uses every sample. "
                       "Spectral windows can straddle the marked boundary by half a window."}
    if mark is not None:
        a, b = marked_offsets(mark, start, end, first, count, rate, turn_directory)
        report.update(mark_s=list(mark), mark_samples=[a, b])
        label += f"      Marked   {mark[0]:g} – {mark[1]:g} s"
        x0, x1 = (left + round(width * v / count) for v in (a, b))
        draw.line((x0, top-5, x1, top-5), fill=(255, 206, 104), width=4)
    draw.text((left, 3), label, font=font(14), fill="white")
    for ch in range(channels):
        y = top + ch * stride
        image.paste(Image.fromarray(palette[band_image[ch]]), (left, y))
        draw.text((3, y), f"{rate / 2000:g} kHz", font=font(10), fill="white")
        if spectral_edges[0] < 1000 < spectral_edges[-1]:
            mid = y + bands - 1 - np.searchsorted(spectral_edges, 1000)
            if y + 14 < mid < y + bands - 24:
                draw.text((3, mid), "1 kHz", font=font(10), fill="white")
        draw.text((3, y+bands-13), "0 Hz", font=font(10), fill="white")
        bottom = y + bands + 25
        draw.text((3, bottom - 16), f"ch {ch + 1} peak", font=font(11), fill="white")
        heights = np.rint(np.clip((20 * np.log10(np.maximum(peaks[:, ch], 1e-4)) + 80) / 80, 0, 1) * 22).astype(int)
        for x in np.flatnonzero(heights):
            draw.line((left+x, bottom-heights[x], left+x, bottom), fill=(119, 204, 242))
        if mark is not None:
            for x in (x0, x1):
                for tick in range(y, y + bands, 9):
                    draw.point((x, tick), fill=(255, 206, 104))
                draw.line((x, bottom+1, x, bottom+4), fill=(255, 206, 104))
    draw.text((left, image.height-17), f"{start:g} s", font=font(11), fill="white")
    draw.text((left+width, image.height-17), f"{end:g} s", anchor="ra", font=font(11), fill="white")
    return {**report, "file": save_observation_png(out, image, "sound-window")}
