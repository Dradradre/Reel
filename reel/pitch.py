"""Estimate the repeating frequency of a sound without assigning a source to it."""
import math

import numpy as np

if __package__:
    from .common import open_recording
else:
    from common import open_recording


def periodic_hz(samples, rate):
    """First strong positive autocorrelation peak after the zero-lag lobe (McLeod and Wyvill)."""
    n = len(samples)
    last = min(n // 3, math.ceil(rate / 60) + 1)
    if last < 3 or not np.isfinite(samples).all():
        return None
    x = samples - np.mean(samples)
    energy = np.r_[0., np.cumsum(x * x)]
    if energy[-1] == 0:
        return None
    # Zero padding avoids matching the end of the recording against its beginning.
    size = 1 << (n + last - 1).bit_length()
    spectrum = np.fft.rfft(x, n=size)
    power = spectrum.real**2 + spectrum.imag**2
    # Near Nyquist the period spans too few samples for lag interpolation. An
    # isolated spectral line can still locate a whistle or electronic tone.
    strongest = int(np.argmax(power))
    if strongest * rate / size > 2000:
        tapered = np.abs(np.fft.rfft(x * np.hanning(n), n=size)) ** 2
        peak = int(np.argmax(tapered))
        radius = math.ceil(2 * size / n)
        if tapered[max(0, peak - radius):peak + radius + 1].sum() >= .985 * tapered.sum():
            if not 0 < peak < len(tapered) - 1:
                return None
            left, centre, right = np.log(np.maximum(tapered[peak - 1:peak + 2], np.finfo(float).tiny))
            offset = .5 * (left - right) / (left - 2 * centre + right)
            hz = (peak + offset) * rate / size
            return round(float(hz), 1) if 60 <= hz <= 20000 else None
    corr = np.fft.irfft(power, n=size)[:last + 1]
    lags = np.arange(last + 1)
    norm = energy[n - lags] + (energy[n] - energy[lags])
    similarity = np.divide(2 * corr, norm, out=np.zeros_like(corr), where=norm > 0)
    negative = np.flatnonzero(similarity <= 0)
    if not len(negative):
        return None
    peaks = np.flatnonzero((similarity[1:-1] > similarity[:-2]) & (similarity[1:-1] >= similarity[2:])) + 1
    peaks = peaks[peaks > negative[0]]
    if not len(peaks):
        return None
    left, middle, right = similarity[peaks - 1], similarity[peaks], similarity[peaks + 1]
    shift = .5 * (left - right) / (left - 2 * middle + right)
    heights = middle + .25 * (right - left) * shift
    best = heights.max()
    if best < .85:
        return None
    chosen = np.flatnonzero(heights >= max(.85, .93 * best))[0]
    period = peaks[chosen] + shift[chosen]
    hz = rate / period
    return round(float(hz), 1) if 60 <= hz <= 2000 else None


class PitchTrace:
    """Keep at most one unfinished 50 ms window between consecutive playback turns."""

    def __init__(self):
        self.tail = None
        self.next_sample = None
        self.format = None

    def read(self, wav, first_sample):
        with open_recording(wav) as recording:
            rate, channels = recording.samplerate, recording.channels
            width = max(1, round(rate * .05))
            if self.next_sample != first_sample or self.format != (rate, channels):
                self.tail = np.empty((0, channels), dtype=np.float64)
            self.format = rate, channels
            self.next_sample = first_sample + len(recording)
            start = first_sample - len(self.tail)
            # For an isolated excerpt, skip the partial window before the first film-grid boundary.
            if start % width:
                skip = min(len(recording), -start % width)
                recording.seek(skip)
                start += skip
            traces = [[] for _ in range(channels)]
            while recording.tell() < len(recording):
                block = recording.read(width - len(self.tail), dtype="float64", always_2d=True)
                if len(self.tail):
                    block = np.concatenate((self.tail, block))
                if len(block) < width:
                    self.tail = block
                    break
                self.tail = block[:0]
                for channel, trace in zip(block.T, traces):
                    trace.append(periodic_hz(channel, rate))
        groups = {}
        for index, trace in enumerate(traces, 1):
            if any(hz is not None for hz in trace):
                groups.setdefault(tuple(trace), []).append(index)
        return {"first_window_sample_on_film": start, "window_samples": width,
                "window_count": len(traces[0]), "rate_hz": rate,
                "channels": [{"channels": ids, "hz": list(values)} for values, ids in groups.items()],
                "basis": "Repeating frequency estimated in consecutive 50 ms windows, 60 to 2000 Hz, or up to 20 kHz for an isolated tone. "
                         "A gap or omitted channel has no clear estimate. A frequency can belong to any source in the mix."}
