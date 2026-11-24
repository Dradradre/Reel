"""Relative picture motion and recorded sound on a shared, causal clock."""
from bisect import bisect_right
from collections import deque

import cv2
import numpy as np

if __package__:
    from .common import open_recording
else:
    from common import open_recording


def alignment(motion, levels):
    """Compare simultaneous changes with shifted controls on the same central samples."""
    # Ignore global picture changes, including most cuts. Each cell competes with
    # other cells and with time-shifted versions of itself on identical samples.
    motion = np.asarray(motion, float)
    local = np.maximum(0, motion - np.median(motion, axis=1, keepdims=True))
    rise = np.maximum(0, np.diff(np.asarray(levels, float), axis=0))
    shifts = (-12, -9, -6, 6, 9, 12)
    idx = np.arange(13, len(local)-12)
    if len(idx) < 32:
        return []
    def cosine(a, b):
        a = a - a.mean()
        b = b - b.mean()
        return float(a @ b / max(np.linalg.norm(a)*np.linalg.norm(b), 1e-9))
    result = []
    for channel, attacks in enumerate(rise.T, 1):
        signal = attacks[idx-1]
        if np.max(signal) < .0001 or np.count_nonzero(signal > signal.max()*.12) < 3:
            continue
        scores = []
        for cell in range(local.shape[1]):
            curve = local[:, cell]
            sync = cosine(curve[idx], signal)
            control = max(cosine(curve[idx+shift], signal) for shift in shifts)
            scores.append((sync, control, cell))
        scores.sort(reverse=True)
        sync, control, cell = scores[0]
        if sync >= .45 and sync >= control + .15 and sync >= scores[1][0]+.08:
            x, y = cell % 4, cell // 4
            result.append(dict(kind="motion_sound_timing", channels=[channel], box=[x/4, y/3, (x+1)/4, (y+1)/3],
                               correlation=round(sync, 3), shifted_control=round(control, 3),
                               competing_region=round(scores[1][0], 3)))
    return result


class TimingHistory:
    def __init__(self):
        self.hits = {}
        self.samples = deque(maxlen=125)
        self.prev_gray = None
        self.last_pts = None
        self.next_tick = None

    def pulses(self, attacks, end):
        rows = []
        changed = set()
        for event in attacks:
            for ch in event['channels']:
                times = self.hits.setdefault(ch, deque(maxlen=32))
                if not times or event['time_s'] > times[-1]:
                    times.append(event['time_s'])
                    changed.add(ch)
        for ch, times in self.hits.items():
            while times and times[0] < end-8:
                times.popleft()
            if ch not in changed or len(times) < 5:
                continue
            recent = list(times)[-8:]
            gaps = np.diff(recent)
            period = float(np.median(gaps))
            if .18 <= period <= 1.5 and np.all(np.abs(gaps-period) < period*.13):
                rows.append(dict(kind='regular_pulse', start_s=recent[0], end_s=recent[-1],
                                 channels=[ch], period_s=round(period, 3), bpm=round(60/period, 1),
                                 attacks=len(recent)))
        return rows

    def binding(self, frames, size, start, end, wav, first):
        if not frames or not wav or end <= start:
            self.samples.clear()
            self.prev_gray = self.last_pts = self.next_tick = None
            return []
        times, movement = [], []
        for pts, _, raw in frames:
            if pts == self.last_pts or pts/1e6 >= end:
                continue
            gray = cv2.cvtColor(np.frombuffer(raw, np.uint8).reshape(size[1], size[0], 3), cv2.COLOR_RGB2GRAY)
            thumb = cv2.resize(gray, (64, 48), interpolation=cv2.INTER_AREA).astype(float)
            delta = np.abs(thumb-self.prev_gray) if self.prev_gray is not None else np.zeros_like(thumb)
            times.append(pts/1e6)
            movement.append(delta.reshape(3, 16, 4, 16).mean(axis=(1, 3)).ravel())
            self.prev_gray, self.last_pts = thumb, pts
        # Complete 40 ms bins only. Carry a split bin across short playback turns.
        with open_recording(wav) as f:
            rate = f.samplerate
            if self.next_tick is None or getattr(self, 'audio_next', None) != (first, rate, f.channels):
                self.next_tick = int(np.ceil(first*25/rate))
                self.pending_energy = np.zeros(f.channels)
                self.pending_motion = np.zeros(12)
                self.samples.clear()
            last = min(first + len(f), round(end*rate))
            self.audio_next = (last, rate, f.channels)
            tick = self.next_tick
            offset = max(first, round(tick*rate/25))
            f.seek(min(len(f), offset-first))
            while offset < last:
                stop = round((tick+1)*rate/25)
                if stop <= offset:
                    tick += 1
                    continue
                finish = min(last, stop)
                block = f.read(finish-offset, dtype='float32', always_2d=True)
                self.pending_energy += np.square(block, dtype=np.float64).sum(axis=0)
                lo = bisect_right(times, offset/rate-1e-10)
                hi = bisect_right(times, finish/rate-1e-10)
                if hi > lo:
                    self.pending_motion = np.maximum(self.pending_motion, np.max(movement[lo:hi], axis=0))
                offset = finish
                if offset == stop:
                    self.samples.append((tick, self.pending_motion.copy(),
                                         np.sqrt(self.pending_energy/max(1,stop-round(tick*rate/25)))))
                    tick += 1
                    self.pending_energy.fill(0)
                    self.pending_motion.fill(0)
            self.next_tick = tick
        if len(self.samples) < 57:
            return []
        ticks, motions, levels = zip(*self.samples)
        return [dict(row, start_s=ticks[0]/25, end_s=(ticks[-1]+1)/25) for row in alignment(motions, levels)]
