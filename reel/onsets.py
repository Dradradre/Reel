"""Find the times of acoustic attacks in released PCM, without naming a source."""
from collections import deque

import numpy as np
from scipy.ndimage import maximum_filter1d

if __package__:
    from .common import open_recording
else:
    from common import open_recording


class OnsetTrace:
    """Detect positive spectral flux. A peak needs two more released hops to be confirmed."""

    def __init__(self):
        self.end = None
        self.rate = None
        self.channels = None

    def _reset(self, rate, channels, first):
        self.rate, self.channels = rate, channels
        self.hop = max(1, round(.008 * rate))
        self.width = 4 * self.hop
        self.window = np.hanning(self.width)
        # Neighbouring log-frequency bands let a vibrating tone drift without a new attack.
        bins = self.width // 2 + 1
        edges = 2 ** (np.arange(0, np.log2(bins), 1 / 24))
        self.lower = np.unique(np.r_[0, np.rint(edges).astype(int)])
        self.lower = self.lower[self.lower < bins]
        self.tail = np.empty((0, channels))
        self.prior = deque(maxlen=2)
        self.floor = deque(maxlen=30)
        # A first measurable rise can peak before five flux frames exist. There
        # is no score until two real spectra are available for comparison.
        zero = np.zeros(channels)
        self.peaks = deque([(None, zero, 0.), (None, zero, 0.)], maxlen=5)
        self.last = np.full(channels, first - rate, dtype=np.int64)

    def scan(self, samples, rate, first):
        channels = samples.shape[1]
        if (first, rate, channels) != (self.end, self.rate, self.channels):
            self._reset(rate, channels, first)
        origin = first - len(self.tail)
        self.end = first + len(samples)
        data = np.concatenate((self.tail, samples))
        if len(data) < self.width:
            self.tail = data
            return []
        frames = np.lib.stride_tricks.sliding_window_view(data, self.width, axis=0)[::self.hop]
        mag = np.abs(np.fft.rfft(frames * self.window, axis=-1))
        bands = np.maximum.reduceat(mag, self.lower, axis=-1)
        levels = np.log1p((2000 / self.width) * bands)
        filtered = maximum_filter1d(levels, size=3, axis=-1, mode="nearest")
        self.tail = data[len(frames) * self.hop:].copy()
        events = []
        for i, level in enumerate(levels):
            # Report the centre of the growth interval. Only complete windows contribute.
            at = origin + i * self.hop + self.width // 2 - self.hop
            if len(self.prior) == 2:
                flux = np.maximum(level - self.prior[0], 0).mean(axis=-1)
                floor = np.mean(self.floor, axis=0) if self.floor else 0.
                threshold = .045 + 1.6 * floor
                self.floor.append(flux)
                self.peaks.append((at, flux, threshold))
                if len(self.peaks) == 5:
                    left2, left1, (stamp, peak, minimum), right1, right2 = self.peaks
                    active = ((peak > minimum) & (peak > left2[1]) & (peak > left1[1])
                              & (peak >= right1[1]) & (peak >= right2[1]) & (stamp - self.last >= 6 * self.hop))
                    if active.any():
                        self.last[active] = stamp
                        events.append({"time_s": stamp / rate, "channels": (np.flatnonzero(active) + 1).tolist()})
            # Copy this frame so long file reads do not retain an earlier block's spectra.
            self.prior.append(filtered[i].copy())
        return events


def attack_report(events, rate):
    return {"basis": "Estimated times of sharp rises in spectral energy, by channel. A sound can have "
                     "several attacks or none. Close or soft attacks can merge. Times use the film clock. "
                     "A peak near a pause can be confirmed in the next turn.",
            "confirmation_delay_s": 5 * max(1, round(.008 * rate)) / rate,
            "events": events}


def recorded_attacks(wav, first):
    trace = OnsetTrace()
    events = []
    with open_recording(wav) as recording:
        rate = recording.samplerate
        for samples in recording.blocks(blocksize=32768, dtype="float64", always_2d=True):
            events.extend(trace.scan(samples, rate, first))
            first += len(samples)
    return attack_report(events, rate)
