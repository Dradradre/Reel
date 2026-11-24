"""Causal landmarks in recorded sound and picture, with reusable local track IDs."""

import math

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.signal import find_peaks

if __package__:
    from .event_timing import TimingHistory
    from .common import open_recording
else:
    from event_timing import TimingHistory
    from common import open_recording


class SpectralTracks:
    """Follow narrow spectral peaks through overlapping sounds without naming their causes."""

    def __init__(self):
        self.tracks = {}
        self.counter = 0
        self.tail = None
        self.next_sample = None
        self.format = None

    def read(self, wav, first):
        observations = {}
        with open_recording(wav) as recording:
            rate, channels = recording.samplerate, recording.channels
            hop = max(1, round(rate * 0.032))
            width = 2 * hop
            window = np.hanning(width)
            if (rate, channels) != self.format or first != self.next_sample:
                self.tracks.clear()
                self.tail = np.empty((0, channels), np.float32)
            self.format = (rate, channels)
            self.next_sample = first + len(recording)
            origin = first - len(self.tail)
            for block in recording.blocks(blocksize=16 * hop, dtype="float32", always_2d=True):
                samples = np.concatenate((self.tail, block))
                offset = 0
                while offset + width <= len(samples):
                    chunk = samples[offset : offset + width]
                    magnitude = np.abs(np.fft.rfft(chunk * window[:, None], axis=0))
                    power = magnitude.max(axis=1)
                    peaks, _ = find_peaks(
                        power,
                        height=max(float(power.max()) * 0.075, width * 1e-5),
                        prominence=max(float(power.max()) * 0.08, width * 1e-5),
                    )
                    # A broadband transient also has local maxima. Only follow peaks that
                    # stand above their own neighbourhood on several successive windows.
                    peaks = [
                        k
                        for k in peaks
                        if k * rate / width >= 45 and power[k] > 3 * np.median(power[max(0, k - 5) : k + 6])
                    ]
                    peaks = sorted(peaks, key=lambda k: -power[k])[:6]
                    at = (origin + offset) / rate
                    self.tracks = {key: v for key, v in self.tracks.items() if at - v["last"] < 0.24}
                    keys = list(self.tracks)
                    freqs = np.array(peaks) * rate / width
                    matches = {}
                    if keys and peaks:
                        old = [self.tracks[key]["hz"] for key in keys]
                        distance = np.abs(np.log2(freqs[None, :] / np.asarray(old)[:, None]))
                        for i, j in zip(*linear_sum_assignment(distance)):
                            if distance[i, j] <= 0.18:
                                matches[j] = keys[i]
                    for j, (k, hz) in enumerate(zip(peaks, freqs)):
                        key = matches.get(j)
                        if key is None:
                            self.counter += 1
                            key = self.counter
                            self.tracks[key] = dict(start=at, count=0)
                        state = self.tracks[key]
                        state.update(last=at, hz=hz, count=state["count"] + 1)
                        if state["count"] < 3:
                            continue
                        levels = 20 * np.log10(np.maximum(magnitude[k] / max(1, window.sum() / 2), 1e-8))
                        row = observations.setdefault(
                            key,
                            dict(first=at, last=at, low=hz, high=hz, peak=levels, count=0, spans=[], balance=[]),
                        )
                        stop = min(self.next_sample / rate, at + width / rate)
                        if row["spans"] and at <= row["spans"][-1][1] + 0.5 / rate:
                            row["spans"][-1][1] = stop
                        else:
                            row["spans"].append([at, stop])
                        if channels > 1:
                            order = np.argsort(levels)
                            leader = int(order[-1] + 1) if levels[order[-1]] - levels[order[-2]] >= 3 else None
                            if not row["balance"] or row["balance"][-1]["channel"] != leader:
                                row["balance"].append({"at_s": at, "channel": leader})
                        row.update(
                            last=at,
                            low=min(row["low"], hz),
                            high=max(row["high"], hz),
                            peak=np.maximum(row["peak"], levels),
                            count=row["count"] + 1,
                        )
                    offset += hop
                self.tail = samples[offset:].copy()
                origin += offset
        return [
            dict(
                track=f"band-{key}",
                kind="spectral_peak",
                start_s=row["first"],
                end_s=min(self.next_sample / rate, row["last"] + width / rate),
                hz=[round(row["low"], 1), round(row["high"], 1)],
                windows=row["count"],
                detected_spans=row["spans"],
                channel_lead_changes=row["balance"],
                channel_peak_dbfs=np.round(row["peak"], 1).tolist(),
            )
            for key, row in observations.items()
        ]


class PictureTracks:
    """Follow local texture on every exposure, and test its appearance after a short occlusion."""

    def __init__(self):
        self.previous = None
        self.pts = None
        self.tracks = {}
        self.counter = 0

    @staticmethod
    def patch(gray, point):
        x, y = np.rint(point).astype(int)
        if not (6 <= x < gray.shape[1] - 6 and 6 <= y < gray.shape[0] - 6):
            return None
        return gray[y - 6 : y + 7, x - 6 : x + 7].copy()

    def read(self, frames, size, start, end):
        rows = {}
        for index, (pts, _, pixels) in enumerate(frames):
            if index + 1 < len(frames) and frames[index + 1][0] == pts:
                continue
            if pts == self.pts:
                for key, state in self.tracks.items():
                    if state["visible"]:
                        self.add(rows, key, state["point"], size, pts / 1e6, False, index)
                continue
            if pts / 1e6 >= end:
                continue
            image = np.frombuffer(pixels, np.uint8).reshape(size[1], size[0], 3)
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            at = pts / 1e6
            if self.previous is not None and (
                self.previous.shape != gray.shape or np.median(cv2.absdiff(self.previous, gray)[::4, ::4]) > 48
            ):
                self.previous = None
                self.tracks.clear()
            active = [k for k, v in self.tracks.items() if v["visible"]]
            traced = {}
            if active:
                old = np.float32([self.tracks[k]["point"] for k in active]).reshape(-1, 1, 2)
                nxt, ok, _ = cv2.calcOpticalFlowPyrLK(self.previous, gray, old, None, maxLevel=2)
                back, reverse, _ = cv2.calcOpticalFlowPyrLK(gray, self.previous, nxt, None, maxLevel=2)
                for key, xy, good, err in zip(
                    active, nxt[:, 0], (ok & reverse).ravel(), np.linalg.norm(back[:, 0] - old[:, 0], axis=1)
                ):
                    patch = self.patch(gray, xy)
                    if good and err <= 1.5 and patch is not None:
                        score = cv2.matchTemplate(patch, self.tracks[key]["template"], cv2.TM_CCOEFF_NORMED).item()
                        if score >= 0.65:
                            traced[key] = xy
            for key, state in list(self.tracks.items()):
                if at - state["seen"] > 1.25:
                    del self.tracks[key]
                    continue
                recovered = False
                xy = traced.get(key)
                if xy is None:
                    x, y = state["point"]
                    radius = int(max(20, min(gray.shape) * (0.12 + 0.25 * (at - state["seen"]))))
                    left, top = max(0, int(x) - radius), max(0, int(y) - radius)
                    crop = gray[top : int(y) + radius + 1, left : int(x) + radius + 1]
                    if min(crop.shape) >= 13:
                        scores = cv2.matchTemplate(crop, state["template"], cv2.TM_CCOEFF_NORMED)
                        _, best, _, (cx, cy) = cv2.minMaxLoc(scores)
                        scores[max(0, cy - 6) : cy + 7, max(0, cx - 6) : cx + 7] = -1
                        if best >= 0.86 and best - float(scores.max()) >= 0.06:
                            xy = np.array([left + cx + 6, top + cy + 6], dtype=np.float32)
                            recovered = True
                state["visible"] = xy is not None
                if xy is not None:
                    state.update(point=xy, seen=at)
                    self.add(rows, key, xy, size, at, recovered, index)
            if len([v for v in self.tracks.values() if v["visible"]]) < 6 and min(gray.shape) >= 15:
                mask = np.full(gray.shape, 255, np.uint8)
                mask[:6] = mask[-6:] = 0
                mask[:, :6] = mask[:, -6:] = 0
                for v in self.tracks.values():
                    cv2.circle(mask, tuple(np.rint(v["point"]).astype(int)), 14, 0, -1)
                corners = cv2.goodFeaturesToTrack(gray, 6, 0.04, 16, mask=mask)
                for xy in () if corners is None else corners[:, 0]:
                    if len(self.tracks) >= 12 or sum(v["visible"] for v in self.tracks.values()) >= 6:
                        break
                    template = self.patch(gray, xy)
                    if template is None or template.std() < 8:
                        continue
                    self.counter += 1
                    self.tracks[self.counter] = dict(point=xy, template=template, visible=True, seen=at)
                    self.add(rows, self.counter, xy, size, at, False, index)
            self.previous, self.pts = gray, pts
        result = []
        for key, v in rows.items():
            first, last = v["times"]
            spans = [
                [max(start, frames[i][0] / 1e6), min(end, frames[j + 1][0] / 1e6 if j + 1 < len(frames) else end)]
                for i, j in v["runs"]
            ]
            spans = [span for span in spans if span[1] > span[0]]
            # Raw exposure PTS can precede a pause when a picture remains on screen.
            bound = min(end, max(last + 1e-6, next((f[0] / 1e6 for f in frames if f[0] / 1e6 > last), end)))
            if bound > max(start, first):
                result.append(
                    dict(
                        track=f"point-{key}",
                        kind="picture_detail",
                        start_s=max(start, first),
                        end_s=bound,
                        visible_spans=spans,
                        first_point=v["first_point"],
                        last_point=v["last_point"],
                        last_pts_us=round(last * 1e6),
                        observed_frames=v["count"],
                        reacquisitions=v["recoveries"],
                        travel=round(v["travel"], 4),
                    )
                )
        return result

    @staticmethod
    def add(rows, key, xy, size, at, recovered, index):
        point = np.round(xy.astype(float) / size, 4).tolist()
        row = rows.setdefault(
            key,
            dict(times=[at, at], first_point=point, last_point=point, count=0, recoveries=0, travel=0.0, runs=[]),
        )
        if row["runs"] and row["runs"][-1][1] == index - 1:
            row["runs"][-1][1] = index
        else:
            row["runs"].append([index, index])
        row["travel"] += math.dist(row["last_point"], point)
        row.update(last_point=point, count=row["count"] + 1, recoveries=row["recoveries"] + int(recovered))
        row["times"][1] = at


class EventStream:
    def __init__(self):
        self.sound = SpectralTracks()
        self.picture = PictureTracks()
        self.timing = TimingHistory()

    def read(self, directory, packet, frames, size):
        start, end = packet["start_s"], packet["end_s"]
        events = []
        if frames:
            events.extend(self.picture.read(frames, size, start, packet.get("picture_end_s", end)))
        audio = packet.get("audio")
        if audio and audio.get("sample_count"):
            events.extend(self.sound.read(directory / audio["wav"], audio["first_sample_on_film"]))
            # The attack detector can confirm a peak from the previous turn. Keep its
            # film time, and publish it only on the turn where confirmation occurred.
            for event in audio.get("attacks", {}).get("events", []):
                when = event["time_s"]
                left, right = max(0.0, when - 0.08), min(end, when + 0.16)
                if left < right:
                    events.append(
                        dict(
                            kind="acoustic_attack",
                            start_s=left,
                            end_s=right,
                            at_s=when,
                            channels=event["channels"],
                        )
                    )
        events.extend(self.timing.pulses((audio or {}).get("attacks", {}).get("events", []), end))
        events.extend(
            self.timing.binding(
                frames,
                size,
                start,
                packet.get("picture_end_s", end),
                directory / audio["wav"] if audio else None,
                audio["first_sample_on_film"] if audio else 0,
            )
        )
        return events

    @staticmethod
    def publish(events, packet, turn):
        audio = packet.get("audio") or {}
        source = audio.get("sound_events") or {}
        for candidate in source.get("candidates", []):
            events.append(dict(candidate, kind="sound_candidate", model=source.get("model")))
        for window in packet.get("speech", {}).get("windows", []):
            for channel in window["channels"]:
                for word in channel["words"]:
                    if word["t"] < word["e"]:
                        events.append(
                            dict(
                                kind="supported_word",
                                start_s=word["t"],
                                end_s=word["e"],
                                channels=channel["channels"],
                                word=word["w"],
                            )
                        )
        events.extend(dict(row) for row in packet.get("voices", {}).get("windows", []))
        events.sort(key=lambda r: (r["start_s"], r["kind"], r.get("track", "")))
        for i, event in enumerate(events, 1):
            event["id"] = f"{turn}.{i}"
        return events
