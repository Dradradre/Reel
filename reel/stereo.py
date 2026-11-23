"""Measure level and relative arrival time in recorded channel pairs on the film clock."""
from functools import lru_cache
import math
import subprocess

import numpy as np

if __package__:
    from .common import open_recording, probe, sample_boundary
else:
    from common import open_recording, probe, sample_boundary


@lru_cache(maxsize=1)
def named_layouts():
    result = subprocess.run(["ffmpeg", "-hide_banner", "-layouts"], check=True, capture_output=True, text=True)
    layouts = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and "+" in fields[1]:
            layouts[fields[0]] = fields[1].split("+")
    return layouts


def channel_pairs(count, layout):
    # A two-channel recording may have no speaker mask. Its channel numbers still have meaning.
    if count == 2 and layout in ("", "unknown", "stereo"):
        return [{"channels": [1, 2], "speakers": ["FL", "FR"] if layout == "stereo" else None}]
    if count < 2:
        return []
    names = named_layouts().get(layout, [])
    if len(names) != count and "+" in layout:
        names = layout.split("(", 1)[-1].rstrip(")").split("+")
    if len(names) != count:
        return []
    pairs = []
    for i, name in enumerate(names):
        if name == "FLC":
            right = "FRC"
        elif name.endswith("L"):
            right = name[:-1] + "R"
        else:
            continue
        if right in names:
            pairs.append({"channels": [i + 1, names.index(right) + 1], "speakers": [name, right]})
    return pairs


def lag_peak(left, right, pad):
    # Keep the same samples of channel one at every candidate lag. Normalize the overlapping
    # samples of channel two separately at each lag. A positive lag means channel two came later.
    if len(left) <= 2 * pad + 1:
        return None
    ref = left[pad:-pad]
    ref = ref - ref.mean()
    energy = ref @ ref
    if energy <= 0:
        return None
    right = right - right.mean()
    n = len(ref)
    sums = np.r_[0., np.cumsum(right)]
    squares = np.r_[0., np.cumsum(right * right)]
    energies = squares[n:] - squares[:-n] - (sums[n:] - sums[:-n]) ** 2 / n
    denom = np.sqrt(energy * np.maximum(energies, 0))
    coefficients = np.divide(np.correlate(right, ref, mode="valid"), denom,
                             out=np.zeros_like(denom), where=denom > 0)
    magnitudes = np.abs(coefficients)
    peak = int(magnitudes.argmax())
    if peak in (0, len(magnitudes) - 1) or magnitudes[peak] < .6:
        return None
    peaks = np.flatnonzero((magnitudes >= np.r_[0., magnitudes[:-1]]) &
                           (magnitudes >= np.r_[magnitudes[1:], 0.]))
    if np.any((peaks != peak) & (magnitudes[peaks] >= magnitudes[peak] - .08)):
        return None
    return peak - pad, float(np.clip(coefficients[peak], -1., 1.))


def pair_cues(samples, rate):
    samples = np.asarray(samples, dtype=np.float64)
    rms = np.sqrt(np.mean(np.square(samples), axis=0))
    levels = [round(20 * math.log10(v), 1) if v else None for v in rms]
    row = {"rms_dbfs": levels, "second_minus_first_db": None, "second_minus_first_us": None}
    if not np.all(rms):
        row["timing"] = "one active channel" if np.any(rms) else "digital silence"
        return row
    row["second_minus_first_db"] = round(20 * math.log10(rms[1] / rms[0]), 1)
    pad = max(1, math.ceil(rate / 1000))
    if len(samples) < max(4 * pad + 1, math.ceil(rate * .012)):
        row["timing"] = "too short"
        return row
    pieces = np.array_split(samples, max(1, round(len(samples) / (rate * .05))))
    estimates = []
    active = 0
    for piece in pieces:
        if not np.all(np.ptp(piece, axis=0) > 0):
            continue
        active += 1
        peak = lag_peak(piece[:, 0], piece[:, 1], pad)
        if peak is not None:
            estimates.append(peak)
    if estimates:
        lags, correlations = np.array(estimates).T
        median = np.median(lags)
        agrees = (np.abs(lags - median) <= max(1., rate * .0001)) & (np.sign(correlations) == np.sign(np.median(correlations)))
        if agrees.sum() * 3 >= 2 * active:
            row.update(second_minus_first_us=round(float(np.median(lags[agrees])) * 1e6 / rate, 1),
                       correlation=round(float(np.median(correlations[agrees])), 3), timing="shared waveform",
                       agreeing_parts=int(agrees.sum()), active_parts=active)
            return row
    row["timing"] = "no consistent lag"
    return row


class StereoHistory:
    """Carry a partial quarter-second bin across contiguous play turns without using future samples."""
    def __init__(self):
        self.tail = None
        self.stop = None
        self.format = None

    def read(self, wav, start, end, layout=None):
        with open_recording(wav) as f:
            if f.channels < 2:
                self.tail = self.stop = self.format = None
                return None
            if layout is None:
                layout = probe(wav).get("audio_layout", "")
            pairs = channel_pairs(f.channels, layout)
            if not pairs:
                self.tail = self.stop = self.format = None
                return None
            rate = f.samplerate
            first = sample_boundary(start, rate)
            fmt = rate, f.channels, layout
            carry = self.tail if self.stop == first and self.format == fmt else None
            pos = first
            windows = []
            stop = min(first + f.frames, sample_boundary(end, rate))
            while pos < stop:
                next_tick = pos * 4 // rate + 1
                boundary = (next_tick * rate + 3) // 4
                piece = f.read(min(boundary, stop) - pos, dtype="float64", always_2d=True)
                if not len(piece):
                    break
                pos += len(piece)
                if carry is not None:
                    piece = np.concatenate((carry, piece))
                begin = pos - len(piece)
                windows.append({"start_s": begin / rate, "end_s": min(end, pos / rate),
                                "pairs": [pair_cues(piece[:, np.array(p["channels"]) - 1], rate) for p in pairs]})
                carry = piece if pos < boundary else None
            self.tail, self.stop, self.format = carry, pos, fmt
        return {"pairs": pairs, "windows": windows, "lag_search_us": 1000,
                "basis": "Positive microseconds mean the first recorded channel leads. Positive dB mean the second "
                         "is louder. Null delay means timing was unresolved. A mix can pan an offscreen sound."}


def summarize_stereo(stereo):
    """Describe the channel measurements on the film clock, without placing a sound source."""
    def extend_passage(rows, start, end, state, value, unit):
        previous = rows[-1] if rows else {}
        if (previous.get("end_s") == start
                and {k: v for k, v in previous.items() if k not in ("start_s", "end_s", unit)} == state):
            row = rows[-1]
            row["end_s"] = end
            if value is not None:
                row[unit] = [min(row[unit][0], value), max(row[unit][1], value)]
        else:
            rows.append({"start_s": start, "end_s": end, **state})
            if value is not None:
                rows[-1][unit] = [value, value]

    pairs = []
    for index, pair in enumerate(stereo["pairs"]):
        first, second = pair["channels"]
        level, arrival = [], []
        for window in stereo["windows"]:
            start, end = window["start_s"], window["end_s"]
            sample = window["pairs"][index]
            difference = sample["second_minus_first_db"]
            if difference is None:
                active = [ch for ch, rms in zip((first, second), sample["rms_dbfs"]) if rms is not None]
                if len(active) == 1:
                    levels = {"only_active_channel": active[0]}
                else:
                    levels = {"level": "unavailable" if active else "digital silence"}
            elif difference == 0:
                levels = {"level": "equal at recorded precision"}
            else:
                levels = {"louder_channel": second if difference > 0 else first}
            extend_passage(level, start, end, levels, abs(difference) if difference else None, "by_db_range")
            lag = sample["second_minus_first_us"]
            if lag is None:
                timing = {"timing": sample["timing"]}
            else:
                timing = {"leading_channel": first if lag > 0 else second} if lag else {"timing": "simultaneous"}
                if sample.get("correlation") is not None:
                    timing["waveform_polarity"] = "opposite" if sample["correlation"] < 0 else "same"
            extend_passage(arrival, start, end, timing, abs(lag) if lag else None, "by_us_range")
        pairs.append({**pair, "level": level, "arrival": arrival})
    return {"pairs": pairs, "basis": "Level and arrival are independent measurements of the recorded channels. "
            "Ranges give the smallest and largest measured difference in each passage. "
            "A mix can pan an offscreen sound."}
