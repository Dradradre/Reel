"""Select a contrasting passage from an already released channel, at its native sample rate."""
import numpy as np
from scipy.ndimage import binary_closing, median_filter

if __package__:
    from .sound_events import active_channels
else:
    from sound_events import active_channels


def contrast_interval(samples, rate):
    """Return sample bounds of one locally prominent event, or None for a steady passage."""
    hop = max(1, round(.02 * rate))
    width = 2 * hop
    if len(samples) < max(4, 75 * hop) or not np.any(samples):
        return None
    frames = np.lib.stride_tricks.sliding_window_view(samples, width)[::hop]
    bins = width // 2 + 1
    # Log-spaced bands retain both narrow resonances and broadband attacks. The floor
    # limits apparent changes in bands having very little recorded energy.
    edges = np.unique(np.r_[0, np.geomspace(1, bins, 15).astype(int)])
    edges = edges[edges < bins]
    window = np.hanning(width)
    power = np.empty((len(frames), len(edges)))
    # Keep the temporary Fourier matrix bounded on high-rate multichannel recordings.
    for offset in range(0, len(frames), 32):
        magnitude = np.abs(np.fft.rfft(frames[offset:offset + 32] * window))
        power[offset:offset + len(magnitude)] = np.add.reduceat(magnitude ** 2, edges, axis=1)
    peak = power.max()
    if peak <= 0:
        return None
    level = 10 * np.log10(np.maximum(power, peak * 1e-5))
    rise = level - np.percentile(level, 25, axis=0)
    bands = min(3, len(edges))
    strength = np.partition(rise, -bands, axis=1)[:, -bands:].mean(axis=1)
    strength = median_filter(strength, size=3, mode="nearest")
    middle = np.median(strength)
    excursion = strength.max() - middle
    if excursion < 7:
        return None
    gate = middle + max(3., .25 * excursion)
    active = strength >= gate
    # Pad so closing can bridge small gaps without erasing a run at either edge.
    active = binary_closing(np.pad(active, 4, mode="edge"), structure=np.ones(5))[4:-4]
    edges = np.flatnonzero(np.diff(np.r_[False, active, False]))
    if not len(edges):
        return None
    lo, hi = max(edges.reshape(-1, 2), key=lambda span: np.maximum(strength[span[0]:span[1]] - gate, 0).sum())
    left = max(0, (lo - 4) * hop)
    right = min(len(samples), (hi + 4) * hop + width)
    if right - left < .32 * rate:
        size = round(.32 * rate)
        left = max(0, min((left + right - size) // 2, len(samples) - size))
        right = left + size
    if right - left > len(samples) * .75:
        return None
    return int(left), int(right)


class FocusHistory:
    """Find at most one new contrasting passage per distinct channel in the released window."""
    def __init__(self):
        self.previous = {}

    def passages(self, context, rate, origin, fresh_start):
        for channels, waveform in active_channels(context):
            bounds = contrast_interval(waveform, rate)
            if bounds is None:
                continue
            lo, hi = bounds
            start, end = origin + lo, origin + hi
            if end <= fresh_start or not np.any(waveform[max(lo, fresh_start - origin):hi]):
                continue
            new = []
            for channel in channels:
                previous = self.previous.get(channel)
                if previous is not None:
                    overlap = min(end, previous[1]) - max(start, previous[0])
                    if overlap >= .5 * (end - start):
                        continue
                new.append(channel)
                self.previous[channel] = (start, end)
            if new:
                yield new, waveform, lo, hi


def contrast_measurements(wav, first_sample, end_s):
    """Measure the contrasting part of the original recording even when no source is recognized."""
    if __package__:
        from .common import open_recording, sample_boundary
        from .hearing import measured_spectrum
    else:
        from common import open_recording, sample_boundary
        from hearing import measured_spectrum
    windows = []
    with open_recording(wav) as recording:
        rate = recording.samplerate
        stop = min(len(recording), max(0, sample_boundary(end_s, rate) - first_sample))
        while recording.tell() < stop:
            offset = recording.tell()
            pcm = recording.read(min(4 * rate, stop - offset), dtype="float32", always_2d=True)
            for channels, waveform in active_channels(pcm):
                bounds = contrast_interval(waveform, rate)
                if bounds is None:
                    continue
                left, right = bounds
                outside = np.r_[waveform[:left], waveform[right:]]
                power = np.mean(np.square(outside, dtype=np.float64))
                windows.append({"channels": channels, "start_s": (first_sample + offset + left) / rate,
                                "end_s": min(end_s, (first_sample + offset + right) / rate),
                                "surrounding_rms_dbfs": round(float(10 * np.log10(power)), 1) if power else None,
                                **measured_spectrum(waveform[left:right], rate)})
    return {"windows": windows,
            "basis": "Original samples from a passage that stands out in its surrounding four seconds or less. "
                     "Times bound the excerpt. Levels and spectral spread describe a sound even if its source is unknown."}
