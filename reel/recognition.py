"""Compare or locate an unnamed sound within audio already released on the film clock."""

import numpy as np
from scipy.signal import correlate

if __package__:
    from .common import recorded_channel, resample_channels
else:
    from common import recorded_channel, resample_channels

RATE = 16000
HOP = 160
WIDTH = 512


def channel_audio(wav, channel):
    samples, rate = recorded_channel(wav, channel)
    return resample_channels(samples, rate, RATE)


def spectral_shape(x):
    frames = np.lib.stride_tricks.sliding_window_view(x, WIDTH)[::HOP]
    power = np.abs(np.fft.rfft(frames * np.hanning(WIDTH))) ** 2
    edges = np.unique(np.geomspace(1, WIDTH // 2, 48).astype(int))
    bands = np.add.reduceat(power[:, 1:], edges - 1, axis=1)
    peak = bands.max(axis=1, keepdims=True)
    db = 10 * np.log10(np.maximum(bands, np.maximum(peak * 1e-5, 1e-16)))
    db -= db.mean(axis=1, keepdims=True)
    return db / np.maximum(np.linalg.norm(db, axis=1, keepdims=True), 1e-10)


def recognize(reference, target, start=0.0, limit=3):
    """Best non-overlapping alignments. Exact correlation is invariant to level and polarity."""
    if min(len(reference), len(target)) < RATE * 0.10:
        return {"status": "too_short", "matches": []}
    if not np.any(reference - reference.mean()) or not np.any(target - target.mean()):
        return {"status": "no_varying_signal", "matches": []}
    if len(reference) > len(target):
        return {"status": "reference_longer_than_search", "matches": []}
    ref = reference.astype(np.float64)
    ref -= ref.mean()
    sig = target.astype(np.float64)
    n = len(ref)
    sums = np.r_[0.0, np.cumsum(sig)]
    squares = np.r_[0.0, np.cumsum(sig * sig)]
    energy = squares[n:] - squares[:-n] - (sums[n:] - sums[:-n]) ** 2 / n
    denom = np.sqrt(np.maximum(energy, 0.0) * np.dot(ref, ref))
    raw = np.abs(
        np.divide(
            correlate(sig, ref, mode="valid", method="fft"), denom, where=denom > 1e-10, out=np.zeros_like(denom)
        )
    ).clip(0, 1)
    a, b = spectral_shape(reference), spectral_shape(target)
    # Dot products retain the temporal order of the entire exemplar. A spectral
    # resemblance alone does not establish a repeated waveform or a common source.
    shape = sum(correlate(b[:, j], a[:, j], mode="valid", method="fft") for j in range(a.shape[1])) / len(a)
    shape = shape[: (len(raw) - 1) // HOP + 1]
    starts = np.arange(len(shape)) * HOP
    peaks = np.minimum(starts + HOP // 2, len(raw) - 1)
    for i, s in enumerate(starts):
        lo, hi = max(0, s - HOP // 2), min(len(raw), s + HOP // 2 + 1)
        peaks[i] = lo + np.argmax(raw[lo:hi])
    # Always include the exact global maximum, even on a partial final hop.
    best = int(np.argmax(raw))
    peaks = np.r_[peaks, best]
    shape = np.r_[shape, shape[min(len(shape) - 1, round(best / HOP))]]
    scores = 0.55 * raw[peaks] + 0.45 * shape
    order = np.argsort(-scores, kind="stable")
    matches = []
    for i in order:
        offset = int(peaks[i])
        if any(abs(offset - other) < n for other, _ in matches):
            continue
        matches.append(
            (
                offset,
                {
                    "start_s": start + offset / RATE,
                    "end_s": start + (offset + n) / RATE,
                    "strong_waveform_match": bool(raw[offset] >= 0.70),
                    "waveform_correlation": round(float(raw[offset]), 5),
                    "ordered_spectrum_cosine": round(float(shape[i]), 5),
                },
            )
        )
        if len(matches) >= limit:
            break
    strong = sum(row["strong_waveform_match"] for _, row in matches)
    return {
        "localization": (
            "multiple_strong_intervals"
            if strong > 1
            else "single_strong_interval"
            if strong
            else "similarity_only"
        ),
        "status": "ranked",
        "matches": [row for _, row in matches],
        "basis": "Relative level and polarity are removed. Compare the original channels. "
        "A narrow tone can correlate at several positions. Similarity alone gives no source name.",
    }
