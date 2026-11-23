"""Present the same released pictures and PCM to a local audiovisual reader."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from contextlib import ExitStack
from fractions import Fraction
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

if __package__:
    from .common import open_recording, probe, recording_levels, reel_source, resample_channels, sample_boundary
    from .frames import cell_box, sheet_size
    from .turn_audio import preceding_audio, preceding_turns, released_samples, sample_at
else:
    from common import open_recording, probe, recording_levels, reel_source, resample_channels, sample_boundary
    from frames import cell_box, sheet_size
    from turn_audio import preceding_audio, preceding_turns, released_samples, sample_at

MAX_SECONDS = 4
MAX_PICTURES = 48
CONTEXT_SECONDS = 8
CONTEXT_PICTURES = 240
RATE = 16000
ANALYSIS_SAMPLES = 400  # A 25 ms audio feature window at the encoder's native rate.
BASIS = ("Two separate reads of the same channel and time range. The first receives the waveform alone. "
         "The second receives the pictures and waveform together. A proposed link to a visible action is "
         "an interpretation. Compare it with the audio-only read, timing and actual pixels. Use the speech track for quoted words.")


def encoder_waveform(pcm, rate):
    """Keep native channels and use the same samples for waveform and audiovisual reads."""
    return resample_channels(pcm, rate, RATE)


def picture_windows(pages, start, end, *, long_context=False):
    entries = [(pts / 1_000_000, page["file"], i, pts) for page in pages
               for i, pts in enumerate(page["pts_us"], start=page.get("first_cell", 0))]
    if not entries:
        return
    times = [entry[0] for entry in entries]
    max_seconds, max_pictures = (CONTEXT_SECONDS, CONTEXT_PICTURES) if long_context else (MAX_SECONDS, MAX_PICTURES)
    left = max(start, times[0])
    while left < end:
        first = bisect_right(times, left) - 1
        right = min(left + max_seconds, end)
        if first + max_pictures < len(entries):
            right = min(right, times[first + max_pictures])
        last = bisect_left(times, right)
        yield left, right, entries[first:last]
        left = right


def visual_input(entries, size, start, end, *, long_context=False):
    frames = []
    files = {}
    with ExitStack() as stack:
        for _, path, cell, _ in entries:
            if path not in files:
                files[path] = stack.enter_context(Image.open(path))
            frames.append(files[path].crop(cell_box(cell, size)).convert("RGB"))
    times = np.array([e[0] for e in entries])
    indices = np.arange(len(frames))
    clocks = [indices]
    if long_context:
        changes = [0] + [i for i in range(1, len(frames))
                         if ImageChops.difference(frames[i - 1], frames[i]).getbbox() is not None]
        if len(changes) < len(frames):
            clocks.insert(0, np.asarray(changes))
    for pictures in clocks:
        # The model can hold identical pixels. Each distinct exposure must retain its onset.
        count = max(len(pictures), math.ceil((end - start) * 2))
        fps = count / (end - start)
        ticks = start + (np.arange(count) + .5) / fps
        which = np.searchsorted(times[pictures], ticks, side="right") - 1
        _, onsets = np.unique(which, return_index=True)
        if len(onsets) == len(pictures):
            error = np.max(np.abs(start + onsets / fps - np.maximum(start, times[pictures])))
            if error < ANALYSIS_SAMPLES / RATE:
                order = pictures[which].tolist()
                return "video", np.stack([np.asarray(frames[i]) for i in order]), fps, order
    # Qwen encodes two successive pictures per temporal patch. Pair adjacent exposures
    # on a regular cadence. At irregular boundaries give an exposure a patch of its own.
    # A partly held first frame and a brief flash retain their real onset on the audio clock.
    distinct = clocks[0]
    onsets = np.maximum(start, times[distinct]) - start
    gaps = np.diff(onsets)
    cadence = np.median(gaps) if len(gaps) >= 2 else None
    order, grid_times = [], []
    i = 0
    while i < len(distinct):
        frame = int(distinct[i])
        grid_times.append(float(onsets[i]))
        regular_pair = (cadence is not None and i + 1 < len(distinct)
                        and abs(gaps[i] - cadence) <= 2e-6 and cadence <= .5)
        second = int(distinct[i + 1]) if regular_pair else frame
        order.extend((frame, second))
        i += 2 if regular_pair else 1
    return "timed_video", np.stack([np.asarray(frames[i]) for i in order]), grid_times, order


def legacy_picture_size(parts, source):
    """Recover frame-sheet dimensions from playback saved before picture_size was recorded."""
    for path, packet in reversed(parts):
        if still := packet.get("current_frame"):
            with Image.open(path / still["file"]) as image:
                return sheet_size({"display_aspect": image.width / image.height})
    if source is not None:
        source = Path(source)
        if source.is_dir():
            source = reel_source(source, json.loads((source / "meta.json").read_text()))
        return sheet_size(probe(source))
    raise ValueError("missing picture dimensions for the released frames")


def published_scene(directory, seconds=None, source=None, *, interval=None):
    """Collect the released cells and PCM for a single time span, across playback pauses."""
    if seconds is not None and not 0 < seconds <= CONTEXT_SECONDS:
        raise ValueError(f"choose joint_seconds above zero and at most {CONTEXT_SECONDS}")
    directory = Path(directory)
    view = json.loads((directory / "view.json").read_text())
    end = view["end_s"]
    if interval is not None:
        earliest, end = interval
        if not 0 <= earliest < end <= view["end_s"] or not view["start_s"] < end:
            raise ValueError("choose a scene ending within this released turn")
        if Fraction(str(end)) - Fraction(str(earliest)) > CONTEXT_SECONDS:
            raise ValueError(f"choose a scene at most {CONTEXT_SECONDS} seconds long")
    elif seconds is None:
        earliest = max(view["start_s"], end - CONTEXT_SECONDS)
    else:
        earliest = max(0., float(Fraction(str(end)) - Fraction(str(seconds))))
    if view["audio"]:
        previous = [(wav.parent, packet) for wav, packet in preceding_audio(directory, view, earliest)]
    elif interval is None:
        return {"status": "no_audio"}
    else:
        previous = list(preceding_turns(directory, view, earliest))
    parts = [*reversed(previous), (directory, view)]
    start = max(parts[0][1]["start_s"], earliest)
    if interval is not None and earliest < start:
        raise ValueError("the requested scene is outside the contiguous released recording")
    pages = []
    size = None
    picture_end = start
    last_pts = None
    stop_us = sample_boundary(end, 1_000_000)
    for path, packet in parts:
        for n, page in enumerate(packet["pages"]):
            pts = page["pts_us"]
            # A pause repeats the exposure already on screen. Keep the original cell indices
            # when that still and the next new frame share a contact sheet.
            skip = int(n == 0 and pts[0] == last_pts)
            stop = bisect_left(pts, stop_us)
            if skip < stop:
                pages.append({"file": str(path / page["file"]), "pts_us": pts[skip:stop], "first_cell": skip})
            last_pts = pts[-1]
        if packet["pages"]:
            picture_end = min(end, packet.get("picture_end_s", packet["end_s"]))
            size = packet.get("picture_size") or size
    if pages:
        # Keep the exposure held at the left edge, with its actual cell in the published sheet.
        at = Fraction(str(start)) * 1_000_000
        first_page = max(0, bisect_right(pages, at, key=lambda page: page["pts_us"][0]) - 1)
        pages = pages[first_page:]
        page = pages[0]
        skip = max(0, bisect_right(page["pts_us"], at) - 1)
        page["first_cell"] += skip
        page["pts_us"] = page["pts_us"][skip:]
    if pages and picture_end > start and size is None:
        size = legacy_picture_size(parts, source)
    scene = {"start": start, "end": end, "pages": pages, "size": size, "picture_end": picture_end}
    if not view["audio"]:
        scene["status"] = "no_audio"
    elif sample_at(parts[0][1], start) >= sample_at(view, end):
        scene["status"] = "no_samples"
    else:
        wav, _, _, first, last = released_samples(directory, start, end, CONTEXT_SECONDS)
        scene.update(wav=wav, first_sample=first, sample_count=last - first, rate_hz=view["audio"]["rate_hz"],
                     channels=view["audio"]["channels"])
    return scene


def read_published_scene(reader, scene, question=None):
    if "wav" not in scene:
        if "start" not in scene:
            return {"status": scene["status"]}
        return {"status": scene["status"], "requested_start_s": scene["start"], "requested_end_s": scene["end"]}
    options = {"question": question} if question is not None else {}
    return reader.joint(scene["wav"], scene["start"], scene["end"], scene["first_sample"], scene["pages"],
                        scene["size"], scene["picture_end"], long_context=True, **options)


def recent_joint(reader, directory, seconds=None, source=None, *, question=None):
    return read_published_scene(reader, published_scene(directory, seconds, source), question)


def scene_levels(scene):
    """Measure every sample, including sound before or after a visible picture."""
    start, end = (Fraction(str(scene[k])) for k in ("start", "end"))
    rows = []
    with open_recording(scene["wav"]) as recording:
        first = 0
        for i in range(math.ceil(2 * (end - start))):
            left, right = start + Fraction(i, 2), min(end, start + Fraction(i + 1, 2))
            stop = len(recording)
            if right < end:
                stop = min(stop, sample_boundary(right, recording.samplerate) - scene["first_sample"])
            rows.append({"start_s": float(left), "end_s": float(right), **recording_levels(recording, first, stop)})
            first = stop
    return rows


def read_joint(reader, wav, start, end, first_sample, pages, size, picture_end, question=None, long_context=False):
    result = {"status": "available", "basis": BASIS, "requested_start_s": start, "requested_end_s": end,
              "encoder_rate_hz": RATE, "windows": []}
    if question is not None:
        result["question"] = question
    with open_recording(wav) as recording:
        rate = recording.samplerate
        for left, right, entries in picture_windows(pages, start, min(end, picture_end), long_context=long_context):
            a, b = (sample_boundary(t, rate) - first_sample for t in (left, right))
            # Playback serializes rational film times as floats. At an exact native
            # boundary the serialized time can round just above its sample index.
            if left == start and abs(left * rate - first_sample) <= math.ulp(left * rate):
                a = 0
            if right == end and abs(right * rate - (first_sample + len(recording))) <= math.ulp(right * rate):
                b = len(recording)
            if not 0 <= a <= b <= len(recording):
                raise ValueError("audiovisual interval falls outside the released recording")
            recording.seek(a)
            pcm = recording.read(b - a, dtype="float64", always_2d=True)
            if len(pcm) != b - a:
                raise ValueError("incomplete audiovisual recording")
            window = {"start_s": left, "end_s": right, "picture_pts_us": [e[3] for e in entries],
                      "wav_samples": [a, b], "channels": []}
            result["windows"].append(window)
            signals = []
            for i, channel in enumerate(pcm.T):
                duplicate = next((j for j, prev in enumerate(signals) if np.array_equal(channel, prev)), None)
                if duplicate is not None:
                    window["channels"][duplicate]["channels"].append(i + 1)
                else:
                    signals.append(channel)
                    window["channels"].append({"channels": [i + 1]})
            visual = None
            for channel, row in zip(signals, window["channels"], strict=True):
                if not len(channel) or not np.any(channel):
                    row["status"] = "digital_silence" if len(channel) else "no_samples"
                    continue
                signal = encoder_waveform(channel, rate)
                if len(signal) < ANALYSIS_SAMPLES or not np.any(signal):
                    row["status"] = "insufficient_encoder_input"
                    continue
                if visual is None:
                    layout, visual, clock, order = visual_input(entries, size, left, right, long_context=long_context)
                    window["model_picture"] = {"format": layout, "frame_indices": order}
                    if layout == "timed_video":
                        window["model_picture"]["grid_onsets_s_from_start"] = clock
                    else:
                        window["model_picture"]["fps"] = clock
                row["audio_only"] = reader(signal)
                row["with_picture"] = reader.with_picture(signal, layout, visual, clock, left, right, question)
                row["status"] = "read"
    if not result["windows"]:
        result["status"] = "no_simultaneous_picture"
    return result
