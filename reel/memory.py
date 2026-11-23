"""Find earlier observations and preserve which pair a match refers to."""
import hashlib

import numpy as np


def earlier_matches(features, times, valid, gap, threshold, shots=None):
    """Best eligible earlier row per observation. Bound the pairwise score buffer."""
    count = len(times)
    indices = np.full(count, -1, dtype=np.intp)
    scores = np.zeros(count, dtype=np.float32)
    # A row only needs earlier observations. A ten-minute film need not retain
    # a square matrix, and a silent track needs no matrix multiplication at all.
    block_size = max(1, min(128, (4 * 1024**2) // (4 * max(1, count))))
    for start in range(0, count, block_size):
        stop = min(start + block_size, count)
        queries = np.flatnonzero(valid[start:stop]) + start
        if not len(queries):
            continue
        last = times.searchsorted(times[queries[-1]] - gap)
        past = np.flatnonzero(valid[:last])
        if not len(past):
            continue
        similarities = features[queries] @ features[past].T
        for i, row in zip(queries, similarities):
            limit = past.searchsorted(times.searchsorted(times[i] - gap))
            if not limit:
                continue
            eligible = row[:limit]
            if shots is not None:
                eligible = np.where(shots[past[:limit]] != shots[i], eligible, -np.inf)
            j = eligible.argmax()
            if eligible[j] > threshold:
                indices[i], scores[i] = past[j], eligible[j]
    return indices, scores


def prefer_identical_previews(thumbs, times, shots, indices, scores):
    """An identical earlier colour image takes precedence over an approximate layout match."""
    first_seen = {}
    identical = np.zeros(len(times), dtype=bool)
    for i, image in enumerate(thumbs):
        if np.all(image == image[0, 0]):
            continue  # A blank screen is a poor landmark.
        key = hashlib.sha256(np.ascontiguousarray(image)).digest()
        j = first_seen.setdefault(key, i)
        if shots[j] != shots[i] and times[j] < times[i] - 1 and np.array_equal(image, thumbs[j]):
            indices[i], scores[i], identical[i] = j, 1.0, True
    return identical


def match_note(memory, start, end, kind):
    """Return one observed pair, preferring a verified preview and then the strongest similarity."""
    selected = np.flatnonzero((memory["t"] >= start) & (memory["t"] < end) & np.isfinite(memory["src"]))
    if not len(selected):
        return ""
    label = kind
    if kind == "layout" and "same_pixels" in memory:
        identical = selected[memory["same_pixels"][selected]]
        if len(identical):
            selected, label = identical, "same preview"
    i = selected[np.argmax(memory["score"][selected])]
    return f"{label} at {memory['t'][i]:.3f}s / earlier {memory['src'][i]:.3f}s"
