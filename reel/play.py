"""Deliver continuous, reader-paced audiovisual intervals from a film."""
from __future__ import annotations

from bisect import bisect_right
from collections import deque
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from fractions import Fraction
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile

import numpy as np
from PIL import Image

if __package__:
    from .attention import gaze_detail, local_change
    from .scene_audio import READY as CAPTIONS_READY, SceneReader
    from .sound_events import EventReader, ready as events_ready
    from .sounds import AST_NAME
    from .config import GAZE_PY, PY
    from .live_speech import SpeechReader
    from .pitch import PitchTrace
    from .local_reader import LocalReader
    from .action_view import action_views, missing_sequence, sound_during_pictures
    from .replay import replay
    from .sound_glimpses import SoundChanges, sound_glimpses
    from .stereo import StereoHistory
    from .hearing import SoundReader, measured_spectrum
    from .common import audio_timeline, hdr_display_available, hdr_picture, pcm_codec, playback_tracks, probe, reel_source, sample_boundary, video_end
    from .frames import SHEET_COLUMNS, SHEET_ROWS, frame_sheets, native_frames, save_frame, sheet_size
else:
    from attention import gaze_detail, local_change
    from scene_audio import READY as CAPTIONS_READY, SceneReader
    from sound_events import EventReader, ready as events_ready
    from sounds import AST_NAME
    from config import GAZE_PY, PY
    from live_speech import SpeechReader
    from pitch import PitchTrace
    from local_reader import LocalReader
    from action_view import action_views, missing_sequence, sound_during_pictures
    from replay import replay
    from sound_glimpses import SoundChanges, sound_glimpses
    from stereo import StereoHistory
    from hearing import SoundReader, measured_spectrum
    from common import audio_timeline, hdr_display_available, hdr_picture, pcm_codec, playback_tracks, probe, reel_source, sample_boundary, video_end
    from frames import SHEET_COLUMNS, SHEET_ROWS, frame_sheets, native_frames, save_frame, sheet_size


# FFmpeg's native little-endian PCM types. Keep the bytes for playback and use
# normalized floating-point samples only for the change measurement.
PCM_TYPES = {"u8": "u1", "s16le": "<i2", "s32le": "<i4", "s64le": "<i8",
             "f32le": "<f4", "f64le": "<f8"}


@dataclass(frozen=True)
class PlaybackPace:
    seconds: float
    continuous: bool
    max_frames: int | None = None
    recording_only: bool = False

    def __post_init__(self):
        if self.max_frames is not None:
            capacity = SHEET_COLUMNS * SHEET_ROWS
            if type(self.max_frames) is not int or not 1 <= self.max_frames <= capacity:
                raise ValueError(f"choose a frame limit between 1 and {capacity}")


@contextmanager
def sound_pipe(source, info, fmt, stop):
    filters = f"{audio_timeline(info)},atrim=end_sample={stop}"
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), "-map", f"0:{info['audio_index']}",
           "-af", filters, "-map_metadata", "-1", "-c:a", "pcm_" + fmt, "-f", "wav", "-rf64", "never", "-"]
    with tempfile.TemporaryFile() as errors, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors) as p:
        try:
            header = p.stdout.read(12)
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
                raise ValueError("audio decoder did not return a WAV stream")
            wave_format = None
            while header := p.stdout.read(8):
                kind, size = struct.unpack("<4sI", header)
                if kind == b"data":
                    break
                value = p.stdout.read(size + size % 2)
                if kind == b"fmt ":
                    wave_format = value[:size]
            if not wave_format or not header or kind != b"data":
                raise ValueError("audio decoder returned an incomplete WAV header")
            yield p, wave_format
            if p.poll() not in (0, None):
                errors.seek(0)
                raise ValueError(errors.read().decode(errors="replace"))
        finally:
            p.stdout.close()
            if p.poll() is None:
                p.terminate()
            p.wait()


def normalized_samples(pcm):
    dtype = pcm.dtype
    values = pcm.astype(np.float64, copy=False)
    if dtype.kind == "u":
        values -= 128
    if dtype.kind != "f":
        values /= 2 ** (dtype.itemsize * 8 - 1)
    return values


def recorded_spectrum(pcm, rate):
    return {"basis": "Original-rate PCM over this turn, per channel. middle_80_percent_hz spans the "
                     "10th to 90th percentile of spectral power. band_power_fraction is the fraction "
                     "in the strongest roughly 94 Hz band. A source can remain unidentified.",
            "channels": [dict(channel=i, **measured_spectrum(normalized_samples(channel), rate))
                         for i, channel in enumerate(pcm.T, 1)]}


def channel_spectrum(wav, png, channels):
    graph = (f"showspectrumpic=s=640x{128 * channels}:mode=separate:color=magma:fscale=log:"
             "scale=log:drange=80:limit=0:legend=1")
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(wav), "-lavfi", graph,
                    "-frames:v", "1", str(png)], check=True, stdout=subprocess.DEVNULL)


def save_sound(dest, chunks, info, wave_format, dtype):
    rate, channels = info["audio_rate"], info["audio_channels"]
    # Reuse the decoder's native format chunk, including its speaker layout. Bytes
    # after the WAV header go straight from the source decoder to these intervals.
    size = sum(map(len, chunks))
    count = size // (channels * dtype.itemsize)
    fmt = b"fmt " + struct.pack("<I", len(wave_format)) + wave_format + bytes(len(wave_format) % 2)
    fact = struct.pack("<4sII", b"fact", 4, count) if dtype.kind == "f" else b""
    header = b"WAVE" + fmt + fact + b"data" + struct.pack("<I", size)
    with (dest / "sound.wav").open("wb") as wav:
        wav.write(b"RIFF" + struct.pack("<I", len(header) + size + size % 2) + header)
        wav.writelines(chunks)
        wav.write(bytes(size % 2))
    # The spectrogram uses the same WAV that an audio-capable reader can play.
    channel_spectrum(dest / "sound.wav", dest / "sound.png", channels)
    pcm = np.frombuffer(b"".join(chunks), dtype=dtype).reshape(count, channels)
    return {"wav": "sound.wav", "spectrum": "sound.png", "rate_hz": rate, "channels": channels,
            "sample_count": count, "recorded_spectrum": recorded_spectrum(pcm, rate),
            "listen_command": "listen [--component mix|harmonic|percussive|mid|side] [--gain-db DB] [QUESTION]",
            "match_command": 'match START END ["sound description" ...]'}


def play(source, out, *, until=None, step=2.0, describe_sounds=False, media=False, gaze=False, speech=False,
         lang=None, sound_reader=None, joint=False, pause_on_sound_change=False, continuous=False,
         max_continuous_pcm_bytes=None, tracks="both", max_frames=None, recording_only=False, verify_sounds=False):
    """Yield one published interval per pull. The reader sees no later intervals or source path."""
    source, out = Path(source), Path(out)
    if not math.isfinite(step) or step <= 0:
        raise ValueError("step must be a finite positive number of seconds")
    if until is not None and (not math.isfinite(until) or until <= 0):
        raise ValueError("until must be a finite positive number of seconds")
    meta = {}
    if source.is_dir():
        meta = json.loads((source / "meta.json").read_text())
        source = reel_source(source, meta)
    info = probe(source)
    if not info["has_video"] and not info["has_audio"]:
        raise ValueError("the source has no picture or sound")
    ends = [info.get(key + "_end_exact", info.get(key + "_end")) for key in ("video", "audio") if info["has_" + key]]
    media_end = max(Fraction(str(t)) for t in ends) if all(t is not None for t in ends) else info["dur"]
    limit = min(media_end, meta.get("until", meta.get("dur", media_end)), until if until is not None else math.inf)
    info = playback_tracks(info, tracks)
    # Use a new session directory so an earlier run cannot leave future packets in view.
    pace = PlaybackPace(step, continuous, max_frames, recording_only)
    out.mkdir(parents=True, exist_ok=False)
    limit = Fraction(str(limit))
    tick = Fraction(1, 10)
    start = Fraction(0)
    position = start
    serial = 0
    previous_gray = held = upcoming = None
    recent_levels = deque(maxlen=3)
    gaze_history = deque(maxlen=7)
    changes = SoundChanges()
    pitch = PitchTrace()
    stereo = StereoHistory()
    with ExitStack() as stack:
        tagger = stack.enter_context(LocalReader([PY, str(Path(__file__).with_name("sounds.py")), "--serve"],
                                                 AST_NAME)) if info["has_audio"] else None
        scene = stack.enter_context(SceneReader(verify_sounds=verify_sounds)) if info["has_audio"] and CAPTIONS_READY.is_file() else None
        event_reader = stack.enter_context(EventReader()) if info["has_audio"] and events_ready() else None
        reader = None
        if info["has_audio"] and (describe_sounds or joint):
            reader = sound_reader if sound_reader is not None else stack.enter_context(SoundReader())
        words = stack.enter_context(SpeechReader(reader or sound_reader)) if speech and info["has_audio"] else None
        eye = stack.enter_context(LocalReader([GAZE_PY, str(Path(__file__).with_name("gaze.py")), "--serve"],
                                              "UNISAL/DHF1K")) if gaze and info["has_video"] else None
        if info["has_video"]:
            w, h = sheet_size(info)
            picture_stop = Fraction(str(meta["picture_end_s"] if "picture_end_s" in meta else
                                        info.get("video_end_exact") or video_end(source, info)))
            frames = stack.enter_context(closing(native_frames(source, w, h, stop_us=sample_boundary(limit, 1_000_000))))
            upcoming = next(frames, None)
        if info["has_audio"]:
            rate, channels = info["audio_rate"], info["audio_channels"]
            fmt = pcm_codec(info).removeprefix("pcm_")
            dtype = np.dtype(PCM_TYPES[fmt])
            pipe, wave_format = stack.enter_context(sound_pipe(source, info, fmt, sample_boundary(limit, rate)))
            quiet = b"\x80" if fmt == "u8" else bytes(dtype.itemsize)
        while position < limit:
            serial += 1
            name = f"turn_{serial:05}"
            prior_picture = held
            shown = []
            sound = []
            cues = {}
            events = []
            span = Fraction(str(pace.seconds))
            deadline = min(limit, start + span)
            pause_reason = "interval"
            if (pace.continuous or pace.max_frames) and info["has_audio"] and max_continuous_pcm_bytes is not None:
                sample_count = max(1, max_continuous_pcm_bytes // (channels * dtype.itemsize))
                sample_limit = sample_boundary(start, rate) + sample_count
                deadline = min(deadline, Fraction(sample_limit, rate))
            while position < deadline:
                stop = min(deadline, position + tick)
                while upcoming is not None and upcoming[0] < sample_boundary(stop, 1_000_000):
                    if not shown and held is not None and upcoming[0] > start * 1_000_000:
                        shown.append(held)
                    if pace.max_frames and shown:
                        if upcoming[0] == shown[-1][0]:
                            shown.pop()  # Only the last picture at this PTS has display time.
                        elif len(shown) == pace.max_frames:
                            stop = deadline = Fraction(upcoming[0], 1_000_000)
                            pause_reason = "frame_limit"
                            break
                    # Keep the full presentation clock when a still lasts many frames.
                    if held is not None and held[2] == upcoming[2]:
                        held = (upcoming[0], upcoming[1], held[2])
                    else:
                        held = upcoming
                    shown.append(held)
                    small = Image.frombytes("RGB", (w, h), held[2]).convert("L").resize((64, 36), Image.Resampling.BOX)
                    gray = np.asarray(small, dtype=np.float32)
                    if previous_gray is not None and np.mean(np.abs(gray - previous_gray)) >= 18:
                        cues.setdefault("picture_change_s", held[0] / 1_000_000)
                    previous_gray = gray
                    upcoming = next(frames, None)
                if info["has_audio"]:
                    count = sample_boundary(stop, rate) - sample_boundary(position, rate)
                    size = count * channels * dtype.itemsize
                    raw = pipe.stdout.read(size)
                    if len(raw) < size:
                        if pipe.wait():
                            raise ValueError("could not decode the audio track")
                        raw += quiet * ((size - len(raw)) // dtype.itemsize)
                    sound.append(raw)
                    if count:
                        values = normalized_samples(np.frombuffer(raw, dtype=dtype).reshape(-1, channels))
                        fresh = changes.scan_samples(values, rate, sample_boundary(position, rate))
                        if fresh:
                            cues.setdefault("sound_band_change_s", fresh[0]["time_s"])
                            events.extend(fresh)
                        level = 10 * np.log10(np.maximum(np.mean(values * values, axis=0), 1e-12))
                        if recent_levels:
                            previous_level = np.median(recent_levels, axis=0)
                            if np.any((np.maximum(level, previous_level) > -60) & (np.abs(level - previous_level) >= 10)):
                                cues.setdefault("sound_level_change_s", (sample_boundary(stop, rate) - 1) / rate)
                        recent_levels.append(level)
                position = stop
                interrupt = ("picture_change_s" in cues or "sound_level_change_s" in cues
                             or (pause_on_sound_change and "sound_band_change_s" in cues))
                if not pace.continuous and interrupt and position - start >= min(span, Fraction(1, 2)):
                    break
            if not shown and held is not None and start < picture_stop:
                shown.append(held)
            with tempfile.TemporaryDirectory(dir=out.parent) as temp:
                dest = Path(temp) / name
                dest.mkdir()
                sheets = frame_sheets(dest, w, h, shown) if shown else []
                for page in sheets:
                    page["file"] = str(Path(page["file"]).relative_to(dest.resolve()))
                audio = save_sound(dest, sound, info, wave_format, dtype) if any(sound) else None
                if audio:
                    audio["first_sample_on_film"] = sample_boundary(start, rate)
                    # Test the actual PCM before resampling or applying a gain. This
                    # measurement is available even when optional sound models are absent.
                    zero = 128 if dtype.kind == "u" else 0
                    present = any(np.any(np.frombuffer(chunk, dtype=dtype) != zero) for chunk in sound)
                    sound.clear()
                    audio["signal"] = "nonzero" if present else "digital_silence"
                    audio["pitch_contour"] = pitch.read(dest / audio["wav"], sample_boundary(start, rate))
                current = None
                if shown and position <= picture_stop:
                    save_frame(source, held[0], dest / "now.png")
                    current = {"file": "now.png", "pts_us": held[0]}
                packet = {"schema": "reel.play.v1", "start_s": float(start), "end_s": float(position),
                          "pages": sheets, "audio": audio, "current_frame": current}
                if tracks != "both":
                    packet["tracks"] = tracks
                if pace.max_frames is not None:
                    packet["max_frames"] = pace.max_frames
                packet["pause"] = {"reason": "change" if position < deadline else pause_reason, **cues}
                packet["observe_command"] = "observe START END [--joint] [--focus LEFT TOP RIGHT BOTTOM] [--question TEXT]"
                if shown:
                    if hdr_picture(source):
                        packet["picture_rendering"] = {"source_transfer": info["color_transfer"],
                                                       "pictures": "sRGB tone-mapped" if hdr_display_available()
                                                       else "HDR signal levels, tone mapping unavailable"}
                    packet["picture_size"] = [w, h]
                    packet["picture_end_s"] = float(min(position, picture_stop))
                    packet["action_view"] = action_views(dest, shown, (w, h), start, position, picture_stop,
                                                         dest / audio["wav"] if audio else None,
                                                         audio["first_sample_on_film"] if audio else 0,
                                                         previous_pixels=prior_picture[2] if prior_picture else None)
                    if info["w"] * info["h"] > w * h:
                        detail = local_change(shown, (w, h), start)
                        if detail:
                            packet["local_picture_change"] = detail
                    if pace.max_frames is None:
                        sequence = missing_sequence(dest, shown, (w, h), sheets, packet["action_view"], current)
                        if sequence:
                            packet["picture_sequence"] = sequence
                if audio:
                    audio["band_changes"] = events
                if audio and info["has_video"]:
                    times = [frame[0] for frame in shown]
                    def picture(us):
                        index = bisect_right(times, us) - 1
                        if index < 0:
                            return None
                        pts, _, pixels = shown[index]
                        return pts, Image.frombytes("RGB", (w, h), pixels)
                    packet["sound_views"] = sound_glimpses(dest, events, picture, float(start), float(position),
                                                          float(picture_stop))
                    if shown:
                        selected = set()
                        for page in packet["action_view"]["pages"]:
                            selected.update(page["pts_us"])
                            selected.update(row["pts_us"] for row in page["additional_frames"] if row)
                        sheet = sheets[0] if pace.max_frames else packet.get("picture_sequence")
                        if sheet:
                            selected.update(sheet["pts_us"])
                        if current:
                            selected.add(current["pts_us"])
                        if detail := packet.get("local_picture_change"):
                            selected.update(detail["pts_us"])
                        for event in packet["sound_views"]["events"]:
                            selected.update(frame["pts_us"] for frame in event["frames"])
                        audio["during_pictures"] = sound_during_pictures(
                            dest / audio["wav"], audio["first_sample_on_film"], times, selected, min(position, picture_stop))
                # From here on readers use published sheets and WAVs. Keep only the carried
                # picture and decoder lookahead while waiting for the viewer's next action.
                shown.clear()
                if audio and not pace.recording_only:
                    # Feed silent intervals into the same history so old sources expire on the film clock.
                    if event_reader is not None:
                        audio["sound_events"] = event_reader.read(dest / audio["wav"], audio["first_sample_on_film"], float(position))
                    if scene is not None:
                        audio["scene"] = scene.read(dest / audio["wav"], audio["first_sample_on_film"], float(position))
                    else:
                        audio["scene"] = {"status": "not_prepared", "setup": "reel setup --captions"}
                    if present:
                        audio["source_estimates"] = tagger.request({"wav": str((dest / audio["wav"]).resolve()),
                                                                    "first_sample": audio["first_sample_on_film"],
                                                                    "end_s": float(position)})

                if audio and channels > 1:
                    spatial = stereo.read(dest / audio["wav"], start, float(position), info.get("audio_layout", ""))
                    if spatial:
                        audio["stereo_cues"] = spatial
                if held is not None:
                    packet["look_command"] = "look [--at SECONDS] LEFT TOP RIGHT BOTTOM"
                if audio is not None:
                    packet["speech"] = (words.read(dest, packet, lang) if words is not None and not pace.recording_only
                                        else {"status": "on_request", "command": "speech"})
                if eye is not None and sheets and not pace.recording_only:
                    packet["gaze"] = gaze_detail(eye, dest, packet, (w, h), float(min(position, picture_stop)), source, gaze_history)
                if media:
                    clip = replay(dest, source, start, position, picture_stop if info["has_video"] else 0,
                                  sheets[0]["pts_us"][0] if sheets else None, dest / audio["wav"] if audio else None)
                    if clip:
                        clip["file"] = Path(clip["file"]).name
                        packet["media"] = clip
                if joint and audio is not None and not pace.recording_only:
                    joint_pages = [{"file": str((dest / page["file"]).resolve()), "pts_us": page["pts_us"]} for page in sheets]
                    packet["joint"] = reader.joint(dest / audio["wav"], start, position, audio["first_sample_on_film"],
                                                  joint_pages, (w, h) if sheets else None,
                                                  picture_stop if sheets else start, long_context=pace.continuous)
                elif joint and audio is None:
                    packet["joint"] = {"status": "no_audio"}
                if audio is not None:
                    packet["joint_command"] = "joint SECONDS [QUESTION] (up to 8, including previous turns)"
                if describe_sounds and reader is not None and audio is not None and not pace.recording_only:
                    packet["sound_descriptions"] = reader.read(dest / audio["wav"], float(start), float(position), remember=True)
                (dest / "view.json").write_text(json.dumps(packet, ensure_ascii=False))
                dest.rename(out / name)
            requested = yield out / name / "view.json"
            pace = requested if isinstance(requested, PlaybackPace) else PlaybackPace(
                step if requested is None else requested, continuous, max_frames, recording_only)
            if not math.isfinite(pace.seconds) or pace.seconds <= 0:
                raise ValueError("next interval must be a finite positive number of seconds")
            start = position
