"""Serve the current viewing directly as standard MCP text, image and audio content."""
import asyncio
import base64
from bisect import bisect_left, bisect_right
from contextlib import ExitStack, closing, nullcontext
from fractions import Fraction
from functools import partial
import io
import json
import math
from pathlib import Path
from subprocess import SubprocessError
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import uuid4

from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource
from PIL import Image

if __package__:
    from .action_view import action_views
    from .audio_focus import Component, focus_recording
    from .attention import fixation_box, focus_motion, focus_still, inspect_past, inspect_turn
    from .common import RecordedSource
    from .replay import replay
    from .pitch import PitchTrace
    from .compare_sounds import SoundMatcher
    from .compare_heard import describe_recordings, recordings
    from .follow import draw_follow, follow_point, validate_anchor
    from .point_reader import PointReader
    from .frames import SHEET_COLUMNS, SHEET_ROWS, frames_from_sheets
    from .hearing import SoundReader, WINDOW_S
    from .joint import CONTEXT_SECONDS, published_scene, read_published_scene, scene_levels
    from .live_speech import SpeechReader
    from .observe import observe as observe_interval
    from .play import PlaybackPace, channel_spectrum, play
    from .region_sync import published_timing, region_views, validate_regions
    from .sound_glimpses import SoundChanges
    from .stereo import summarize_stereo
    from .unmix import LayerReader, describe_layer
    from .turn_audio import released_samples, sample_at
else:
    from action_view import action_views
    from audio_focus import Component, focus_recording
    from attention import fixation_box, focus_motion, focus_still, inspect_past, inspect_turn
    from common import RecordedSource
    from replay import replay
    from pitch import PitchTrace
    from compare_sounds import SoundMatcher
    from compare_heard import describe_recordings, recordings
    from follow import draw_follow, follow_point, validate_anchor
    from point_reader import PointReader
    from frames import SHEET_COLUMNS, SHEET_ROWS, frames_from_sheets
    from hearing import SoundReader, WINDOW_S
    from joint import CONTEXT_SECONDS, published_scene, read_published_scene, scene_levels
    from live_speech import SpeechReader
    from observe import observe as observe_interval
    from play import PlaybackPace, channel_spectrum, play
    from region_sync import published_timing, region_views, validate_regions
    from sound_glimpses import SoundChanges
    from stereo import summarize_stereo
    from unmix import LayerReader, describe_layer
    from turn_audio import released_samples, sample_at


MAX_INLINE_PCM_BYTES = 4 * 1024 * 1024
RECORDING_GUIDE = (
    "If your client cannot play the WAV, you can still report what the recorded channel measurements "
    "and any local sound or speech readings support. Attribute indirect observations to that evidence. "
    "during_pictures measures only the native exposures listed, in each channel of the released WAV. "
    "Quote only timed words from the speech reader. Treat possible_words as uncertain. "
    "An event mentioned in speech needs separate acoustic evidence. Digital silence in a measured gap "
    "rules out sound there. A nonzero level alone does not identify its source.")


def text_block(value):
    return types.TextContent(type="text", text=json.dumps(value, ensure_ascii=False))


def image_block(path, preview=False):
    if preview:
        with Image.open(path) as image:
            image.thumbnail((1280, 960), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            data = buf.getvalue()
    else:
        data = path.read_bytes()
    return types.ImageContent(type="image", mimeType="image/png", data=base64.b64encode(data).decode("ascii"))


def audio_block(path):
    return types.AudioContent(type="audio", mimeType="audio/wav",
                              data=base64.b64encode(path.read_bytes()).decode("ascii"))


def end_of(path):
    return json.loads(path.read_text())["end_s"]


def first_view_report(report):
    """Keep the timed readings in front of the pictures on a text-and-image client."""
    result = {key: report[key] for key in ("status", "turn", "start_s", "end_s")}
    for key in ("sound_descriptions", "joint", "sound_in_context"):
        if key not in report:
            continue
        reading = dict(report[key])
        windows = []
        for window in reading.get("windows", []):
            groups = []
            for channel in window["channels"]:
                evidence = {k: v for k, v in channel.items() if k != "channels"}
                # Keep distinct measurements even when two channels have the same caption.
                group = next((chosen for chosen, values in groups if values == evidence), None)
                if group is None:
                    group = []
                    groups.append((group, evidence))
                group.extend(channel.get("channels", []))
            stamp = {k: v for k, v in window.items()
                     if k not in ("channels", "picture_pts_us", "model_picture", "wav_samples")}
            windows.append({**stamp, "channels": [{"channels": ch, **row} for ch, row in groups]})
        if "windows" in reading:
            reading["windows"] = windows
        result[key] = reading
    if "speech" in report:
        result["speech"] = report["speech"]
    if report.get("audio"):
        audio = {}
        stereo = report["audio"].get("stereo_cues")
        if stereo and stereo.get("pairs") and stereo.get("windows"):
            audio["stereo_summary"] = summarize_stereo(stereo)
        audio.update({k: v for k, v in report["audio"].items() if k not in ("band_changes", "stereo_cues")})
        if "band_changes" in report["audio"]:
            audio["band_change_times_s"] = list(dict.fromkeys(c["time_s"] for c in report["audio"]["band_changes"]))
        result["audio"] = audio
    result.update({k: v for k, v in report.items() if k not in result})
    result["detail_on_replay"] = "Use full_detail=true on this turn for per-channel measurements and encoder timing."
    return result


class Viewing:
    def __init__(self, playback, sound, words, lang, source=None, layers=None, tracker=None, matcher=None):
        self.matcher = matcher
        self.tracker = tracker
        self.layers = layers
        self.playback, self.sound, self.words, self.lang = playback, sound, words, lang
        self.source = source
        self.original = RecordedSource(source) if source is not None else None
        self.hearing_by_turn = {}
        self.paths = []
        self.ended = False
        self.failed = False
        self.last_joint = None
        self.lock = Lock()

    def published(self, turn):
        if not 1 <= turn <= len(self.paths):
            raise ValueError("that turn has not been viewed")
        path = self.paths[turn - 1]
        return path.parent, json.loads(path.read_text())

    def sound_session(self, *, replay):
        if self.sound is None:
            return SoundReader()
        if replay and isinstance(self.sound, SoundReader):
            return self.sound.for_replay()
        return nullcontext(self.sound)

    def match_original(self, wav, start, end, alternatives, *, per_second, first_sample):
        if self.matcher is None:
            manager = SoundMatcher()
        elif isinstance(self.matcher, SoundMatcher):
            manager = self.matcher.for_replay()
        else:
            manager = nullcontext(self.matcher)
        with manager as matcher:
            if per_second:
                return matcher.read(wav, start, end, alternatives, per_second=True, first_sample=first_sample)
            return matcher.read(wav, start, end, alternatives)

    def read_scene(self, key, directory, *, seconds=None, question=None, interval=None, native_audio=False):
        cached = self.last_joint is not None and self.last_joint[0] == key
        scene = self.last_joint[2] if cached else published_scene(directory, seconds, self.source, interval=interval)
        if native_audio and scene.get("wav") and scene["wav"].stat().st_size > MAX_INLINE_PCM_BYTES:
            raise ValueError("the recording exceeds the inline limit; shorten the range or omit native_audio")
        if cached:
            return self.last_joint[1:]
        if interval is not None and question is None:
            return {"status": scene.get("status", "not_requested")}, scene
        with self.sound_session(replay=interval is not None) as sound:
            joint = read_published_scene(sound, scene, question)
        if joint.get("status") != "unavailable" and joint.get("recording", {}).get("status") != "unavailable":
            self.last_joint = (key, joint, scene)
        return joint, scene

    def ask_scene(self, start_s, end_s, question=None, native_audio=False, alternatives=None, sync_regions=None):
        if sync_regions:
            validate_regions(sync_regions)
        if question is not None:
            question = question.strip()
            if not question:
                raise ValueError("supply a question or omit it for the recording")
        with self.lock:
            if not self.paths or not 0 <= start_s < end_s <= end_of(self.paths[-1]):
                raise ValueError("choose a scene within the time already viewed")
            directory = self.paths[bisect_left(self.paths, end_s, key=end_of)].parent
            joint, scene = self.read_scene(("interval", start_s, end_s, question), directory, question=question,
                                          interval=(start_s, end_s), native_audio=native_audio)
            match = None
            if scene.get("wav") and alternatives is not None:
                match = self.match_original(scene["wav"], start_s, end_s, alternatives, per_second=True,
                                            first_sample=scene["first_sample"])
            if sync_regions:
                timing = published_timing(scene, sync_regions)
        report = {"start_s": start_s, "end_s": end_s, "joint": joint, "images_in_order": [], "audio": None}
        if sync_regions:
            report["region_timing"] = timing
        if match is not None:
            report["original_sound_match"] = match
        wav = scene.get("wav")
        if wav:
            report["audio"] = {"first_sample_on_film": scene["first_sample"], "sample_count": scene["sample_count"],
                               "rate_hz": scene["rate_hz"], "channels": scene["channels"], "wav_attached": native_audio,
                               "levels": scene_levels(scene),
                               "pitch_contour": PitchTrace().read(wav, scene["first_sample"]),
                               "band_changes": SoundChanges().scan(wav, scene["first_sample"])}
        blocks = []
        if scene.get("pages") and start_s < scene["picture_end"]:
            frames = frames_from_sheets(scene["pages"], scene["size"])
            with TemporaryDirectory() as tmp:
                view = action_views(tmp, frames, scene["size"], start_s, end_s, scene["picture_end"], wav,
                                    scene.get("first_sample", 0), chronological=True)
                for page in view["pages"]:
                    report["images_in_order"].append({"action_over_time": [page["start_s"], page["end_s"]],
                                                       "rows": page["rows"]})
                    blocks.append(image_block(Path(tmp) / page["file"]))
        if sync_regions:
            with TemporaryDirectory() as tmp:
                for page in region_views(Path(tmp), scene, sync_regions):
                    path = Path(tmp) / page.pop("file")
                    report["images_in_order"].append({"region_motion_and_channel_peaks": page})
                    blocks.append(image_block(path))
        if native_audio and wav:
            blocks.append(audio_block(wav))
        return [text_block(report), *blocks]

    def read_recording(self, turn, directory, packet, joint):
        """Use only released recordings, including when an older turn is replayed out of order."""
        track = packet["audio"]
        cached = self.hearing_by_turn.setdefault(turn, {})
        if track.get("signal") == "digital_silence":
            empty = {"status": "digital_silence", "windows": []}
            return {"speech": empty, "sound_descriptions": empty}
        readings = {}
        if packet.get("speech", {}).get("status") != "available" and "speech" not in cached:
            readings["speech"] = self.words.inspect(directory, self.lang)
        # A contextual reading can include a sound that already stopped. Only reuse it
        # as this turn's impression if it heard exactly the newly presented interval.
        windows = joint.get("windows", [])
        covers = (windows and windows[0]["start_s"] == packet["start_s"]
                  and windows[-1]["end_s"] == packet["end_s"]
                  and all(c["status"] in ("read", "digital_silence") for w in windows for c in w["channels"]))
        recording = joint.get("recording", {})
        if recording.get("status") == "available" and not recording.get("question"):
            heard = recording.get("windows", [])
            covers = covers or (heard and heard[0]["start_s"] == packet["start_s"]
                                and heard[-1]["end_s"] == packet["end_s"])
        saved = packet.get("sound_descriptions", {})
        saved_windows = saved.get("windows", [])
        described = (saved.get("status") == "available" and saved_windows
                     and saved_windows[0]["start_s"] == packet["start_s"] and saved_windows[-1]["end_s"] == packet["end_s"])
        if not (covers or described or "sound_descriptions" in cached):
            with self.sound_session(replay=False) as sound:
                readings["sound_descriptions"] = sound.read((directory / track["wav"]).resolve(), packet["start_s"], packet["end_s"])
        cached.update({key: value for key, value in readings.items() if value["status"] == "available"})
        result = {**cached, **readings}
        if covers:
            result.pop("sound_descriptions", None)
        return result

    def release(self, turn, max_seconds=None, continuous_seconds=None, max_frames=None, *, recording_only=False):
        """Advance or replay the shared clock while the caller holds the viewing lock."""
        if max_seconds is not None and not 0.000001 <= max_seconds <= 2:
            raise ValueError("choose a viewing interval between one microsecond and two seconds")
        if continuous_seconds is not None:
            if max_seconds is not None:
                raise ValueError("choose either max_seconds or continuous_seconds for this turn")
            if not 0.000001 <= continuous_seconds <= 8:
                raise ValueError("choose a continuous interval between one microsecond and eight seconds")
        requested = max_seconds or 2.0
        if continuous_seconds is not None or max_frames is not None or recording_only:
            continuous = continuous_seconds is not None or (max_frames is not None and max_seconds is None)
            requested = PlaybackPace(continuous_seconds or requested, continuous, max_frames, recording_only)
        if turn == len(self.paths) + 1:
            if self.failed:
                raise ValueError("the film could not be decoded beyond the last published turn")
            if not self.ended:
                if not callable(self.playback) and any(v is not None for v in (max_seconds, continuous_seconds, max_frames)):
                    raise ValueError("this playback has fixed, previously recorded intervals")
                try:
                    if callable(self.playback):
                        with self.original.reading() if self.original else nullcontext():
                            path = self.playback(requested)
                    else:
                        path = next(self.playback, None)
                except Exception:
                    self.failed = True
                    raise ValueError("the next interval could not be decoded") from None
                if path is None:
                    self.ended = True
                else:
                    self.paths.append(path)
            if self.ended:
                return None
        return self.published(turn)

    def advance(self, turn, native_audio, sound_estimates=True, focus=None, max_seconds=None, continuous_seconds=None,
                joint_seconds=None, *, image_hearing=False, follow_from_s=None, question=None, full_detail=True,
                max_frames=None):
        if question is not None:
            question = question.strip()
            if not question:
                raise ValueError("supply a question about the scene or omit it")
        joint_requested = joint_seconds is not None or question is not None
        if follow_from_s is not None:
            validate_anchor(focus, follow_from_s)
        if joint_seconds is not None and not 0 < joint_seconds <= CONTEXT_SECONDS:
            raise ValueError(f"choose joint_seconds above zero and at most {CONTEXT_SECONDS}")
        if focus is not None and (len(focus) != 2 or not all(0 <= coord <= 1 for coord in focus)):
            raise ValueError("focus needs a point [x,y] within the picture, in the range 0 to 1")
        with self.lock:
            published = self.release(turn, max_seconds, continuous_seconds, max_frames)
            if published is None:
                return [text_block({"status": "end_of_view", "last_turn": len(self.paths)})]
            directory, packet = published
            if follow_from_s is not None:
                followed, exposures = follow_point(directory, packet, follow_from_s, focus, self.tracker)
            joint = packet.get("joint", {})
            if joint_requested:
                joint, _ = self.read_scene((turn, joint_seconds, question), directory, seconds=joint_seconds,
                                           question=question)
            hearing = {}
            if image_hearing and sound_estimates and packet.get("audio"):
                hearing = self.read_recording(turn, directory, packet, joint)
        times = {"turn": turn, "start_s": packet["start_s"], "end_s": packet["end_s"]}
        report = {**times, "status": "view", "frame_pages": len(packet["pages"]), "images_in_order": []}
        if "picture_rendering" in packet:
            report["picture_rendering"] = packet["picture_rendering"]
        if "pause" in packet:
            report["pause"] = packet["pause"]
        blocks = []
        sheet = packet.get("picture_sequence")
        if packet.get("max_frames") and packet["pages"]:
            sheet = packet["pages"][0]
        if sheet:
            sequence = ({"selected_picture_pts_us": sheet["pts_us"], "selection": sheet["selection"]}
                        if "selection" in sheet else {"successive_picture_pts_us": sheet["pts_us"]})
            if "exposures_per_picture" in sheet:
                sequence["exposures_per_picture"] = sheet["exposures_per_picture"]
            if packet["picture_end_s"] < packet["end_s"]:
                sequence["picture_end_s"] = packet["picture_end_s"]
            report["images_in_order"].append(sequence)
            blocks.append(image_block(directory / sheet["file"]))
        for page in packet.get("action_view", {}).get("pages", []):
            description = {"action_over_time": [page["start_s"], page["end_s"]]}
            if (packet.get("audio") or {}).get("during_pictures"):
                description["picture_pts_us"] = page["pts_us"]
            if page.get("additional_frames"):
                description["additional_frames"] = page["additional_frames"]
            report["images_in_order"].append(description)
            blocks.append(image_block(directory / page["file"]))
        if detail := packet.get("local_picture_change"):
            report["local_picture_change"] = dict(detail)
            if self.original is not None:
                report["local_picture_change"]["inspect"] = (
                    "Use look with this box at either at_s time to inspect the changed region in the original picture.")
        if follow_from_s is not None:
            report["follow"] = {key: value for key, value in followed.items() if key != "trajectory"}
            with TemporaryDirectory() as tmp:
                for png, description in draw_follow(Path(tmp), directory, packet, followed, exposures, self.original):
                    report["images_in_order"].append(description)
                    blocks.append(image_block(png))
        elif focus is not None:
            box = fixation_box(focus)
            status = "motion_unavailable" if packet["pages"] else "no_picture"
            report["focus"] = {"point": focus, "box": box, "status": status}
            if packet["pages"] and self.source is not None:
                with TemporaryDirectory() as tmp:
                    try:
                        with self.original.reading() as movie:
                            pages = focus_motion(tmp, movie, packet, box, directory)
                    except (OSError, ValueError, SubprocessError):
                        pages = []
                    for page in pages:
                        report["images_in_order"].append({"reader_focus": box, "at_s": page["at_s"],
                                                         "pts_us": page["pts_us"],
                                                         "additional_frames": page["additional_frames"], "rows": page["rows"]})
                        blocks.append(image_block(Path(tmp) / page["file"]))
                    if pages:
                        report["focus"]["status"] = "available"
                        if packet.get("audio"):
                            report["focus"]["sound"] = "recorded channels of the whole scene"
        for event in packet.get("sound_views", {}).get("events", []):
            if event.get("file"):
                report["images_in_order"].append({"pictures_at_band_change_s": event["time_s"],
                                                  "frames": event["frames"]})
                blocks.append(image_block(directory / event["file"]))
        if gaze := packet.get("gaze"):
            report["gaze"] = {"status": gaze["status"], "model": gaze.get("model")}
            recent = gaze.get("recent_details", {})
            if recent.get("file"):
                samples = []
                for observation in recent["samples"]:
                    stamp = {"time_s": observation["tick"] / gaze["rate_hz"], "pts_us": observation["pts_us"]}
                    if observation.get("point") is None:
                        stamp["centre_fallback"] = True
                    samples.append(stamp)
                report["images_in_order"].append({"predicted_gaze_details": samples})
                blocks.append(image_block(directory / recent["file"]))
            elif gaze.get("detail", {}).get("file"):
                report["images_in_order"].append({"predicted_gaze_detail_pts_us": gaze["pts_us"]})
                blocks.append(image_block(directory / gaze["detail"]["file"], preview=True))
        if still := packet.get("current_frame"):
            report["images_in_order"].append({"current_picture_pts_us": still["pts_us"]})
            blocks.append(image_block(directory / still["file"], preview=True))
            if focus is not None and follow_from_s is None:
                with TemporaryDirectory() as tmp:
                    png = Path(tmp) / "detail.png"
                    detail = focus_still(directory / still["file"], focus, report["focus"]["box"], png)
                    report["images_in_order"].append({"focused_picture_pts_us": still["pts_us"], **detail})
                    blocks.append(image_block(png))
                if report["focus"]["status"] == "motion_unavailable":
                    report["focus"]["status"] = "still_only"
        audio = packet.get("audio")
        report["audio"] = None
        if audio:
            fields = ("rate_hz", "channels", "first_sample_on_film", "sample_count", "signal", "recorded_spectrum",
                      "band_changes", "stereo_cues", "during_pictures", "pitch_contour")
            if sound_estimates:
                fields += ("source_estimates", "scene")
            report["audio"] = {key: audio[key] for key in fields if key in audio}
            if image_hearing:
                report["audio"]["wav_attached"] = native_audio
                if native_audio and sound_estimates:
                    report["audio"]["reading_basis"] = (
                        "Channel measurements and local readings in this report come from the released recording "
                        "and remain available if the client omits the WAV.")
            report["images_in_order"].append("recorded channel spectrum")
            blocks.append(image_block(directory / audio["spectrum"]))
            if native_audio:
                blocks.append(audio_block(directory / audio["wav"]))
        if sound_estimates:
            for key in ("sound_descriptions", "joint", "speech"):
                if key in packet:
                    report[key] = packet[key]
            saved_windows = packet.get("sound_descriptions", {}).get("windows", [])
            if image_hearing and saved_windows and saved_windows[0]["start_s"] < packet["start_s"]:
                report["sound_in_context"] = report.pop("sound_descriptions")
            report.update(hearing)
        if joint_requested:
            report["joint"] = joint
        if not full_detail:
            report = first_view_report(report)
        return [text_block(report), *blocks]

    def frames(self, turn, page):
        with self.lock:
            directory, packet = self.published(turn)
        if not 1 <= page <= len(packet["pages"]):
            raise ValueError("no such frame page in this turn")
        sheet = packet["pages"][page - 1]
        return [text_block({"turn": turn, "page": page, "picture_pts_us": sheet["pts_us"]}),
                image_block(directory / sheet["file"])]

    def observe(self, start_s, end_s, box, native_audio):
        if box is not None:
            validate_regions([box])
        with self.lock:
            if not self.paths or not 0 <= start_s < end_s <= end_of(self.paths[-1]):
                raise ValueError("choose an interval within the time already viewed")
            # A reply carries at most one contact sheet and two seconds of sound.
            # Find its boundary from published timestamps before reading the media.
            stop = min(end_s, start_s + 2)
            first = bisect_right(self.paths, start_s, key=end_of)
            last = bisect_left(self.paths, stop, lo=first, key=end_of)
            pts = sorted({t for path in self.paths[first:last + 1]
                          for page in json.loads(path.read_text())["pages"] for t in page["pts_us"]})
            start_us = Fraction(str(start_s)) * 1_000_000
            first_frame = max(0, bisect_right(pts, start_us) - 1)
            next_frame = first_frame + SHEET_COLUMNS * SHEET_ROWS
            if next_frame < len(pts):
                stop = min(stop, pts[next_frame] / 1_000_000)
                last = bisect_left(self.paths, stop, lo=first, hi=last + 1, key=end_of)
            directory = self.paths[last].parent
            try:
                with self.original.reading() if self.original else nullcontext():
                    result = observe_interval(directory, start_s, stop, focus=box, playback_source=self.source)
            except Exception:
                raise ValueError("could not read that part of the released film") from None
        action = result.get("action_view", {})
        sheets = result["pages"]
        if "frame_pages" in action:
            sheets = [p["file"] for p in action["frame_pages"]]
        report = {"start_s": start_s, "end_s": stop, "next_start_s": stop if stop < end_s else None,
                  "box": box, "moments": result["moments"], "images_in_order": [], "audio": None}
        blocks = []
        for i, sheet in enumerate(sheets, 1):
            report["images_in_order"].append({"frame_page": i})
            blocks.append(image_block(Path(sheet)))
        for page in action.get("pages", []):
            report["images_in_order"].append({"action_over_time": [page["start_s"], page["end_s"]], "rows": page["rows"]})
            blocks.append(image_block(Path(page["file"])))
        if track := result["audio"]:
            report["audio"] = {k: v for k, v in track.items() if k != "wav"}
            wav = Path(track["wav"])
            with TemporaryDirectory() as tmp:
                spectrum = Path(tmp) / "spectrum.png"
                channel_spectrum(wav, spectrum, track["channels"])
                blocks.append(image_block(spectrum))
                report["images_in_order"].append("recorded channel spectrum")
            if native_audio:
                blocks.append(audio_block(wav))
        return [text_block(report), *blocks]

    def look(self, turn, box, at_s=None):
        with self.lock:
            directory, _ = self.published(turn)
            detail = inspect_turn(directory, box) if at_s is None else inspect_past(directory, self.original, at_s, box)
        return [text_block({k: v for k, v in detail.items() if k != "file"}),
                image_block(Path(detail["file"]))]

    def listen(self, turn, question, start_s=None, end_s=None, native_audio=False, describe=True, component="mix",
               alternatives=None, per_second=False):
        with self.lock:
            directory, packet = self.published(turn)
            end = packet["end_s"] if end_s is None else end_s
            if not 0 < end <= packet["end_s"]:
                raise ValueError("choose a listening range within the time viewed by this turn")
            if end <= packet["start_s"]:
                index = bisect_left(self.paths, end, hi=turn, key=end_of)
                directory, packet = self.published(index + 1)
            start = packet["start_s"] if start_s is None else start_s
            if not 0 <= start < end:
                raise ValueError("the listening range must start before it ends")
            track = packet["audio"]
            if not track:
                return [text_block({"status": "no_audio"})]
            if question is not None and not describe:
                raise ValueError("enable the local description to ask a listening question")
            rate = track["rate_hz"]
            wav = directory / track["wav"]
            first_packet = packet
            if start < packet["start_s"]:
                index = bisect_right(self.paths, start, hi=turn, key=end_of)
                _, first_packet = self.published(index + 1)
            first = sample_at(first_packet, start)
            stop = min(end, start + WINDOW_S)
            if native_audio:
                # Bound encoded media in a single reply, including high-rate multichannel recordings.
                bytes_per_frame = math.ceil(wav.stat().st_size / track["sample_count"])
                if component != "mix":
                    bytes_per_frame = max(bytes_per_frame, 4 * track["channels"])
                sample_limit = max(1, MAX_INLINE_PCM_BYTES // bytes_per_frame)
                if sample_at(packet, stop) - first > sample_limit:
                    stop = math.nextafter((first + sample_limit) / rate, -math.inf)
            if (start, stop) != (packet["start_s"], packet["end_s"]):
                index = bisect_left(self.paths, stop, hi=turn, key=end_of)
                wav, start, stop, first, last = released_samples(self.paths[index].parent, start, stop, WINDOW_S)
            else:
                last = sample_at(packet, stop)
            original = wav
            wav, focus = focus_recording(original, component, original.parent)
            result = {"status": "recording"}
            if describe:
                with self.sound_session(replay=True) as sound:
                    comparison = {"mix_wav": original, "component": component} if focus else {}
                    result = sound.read(wav, start, stop, question=question, **comparison)
            result = {**result, "start_s": start, "end_s": stop, "next_start_s": stop if stop < end else None,
                      "rate_hz": rate, "channels": track["channels"], "first_sample_on_film": first,
                      "samples_per_channel": last - first, "pitch_contour": PitchTrace().read(wav, first)}
            if alternatives is not None or per_second:
                result["original_sound_match"] = self.match_original(original, start, stop, alternatives or [],
                                                                     per_second=per_second, first_sample=first)
            if focus:
                result["component"] = focus
        blocks = []
        if start_s is not None or end_s is not None or not describe or focus:
            with TemporaryDirectory() as tmp:
                spectrum = Path(tmp) / "spectrum.png"
                channel_spectrum(wav, spectrum, track["channels"])
                blocks.append(image_block(spectrum))
            result["images_in_order"] = [f"{component} component spectrum" if focus else "recorded channel spectrum"]
        if native_audio:
            blocks.append(audio_block(wav))
        return [text_block(result), *blocks]

    def listen_layer(self, turn, start_s, end_s, channel, layer, native_audio, describe, focus=None):
        with self.lock:
            directory, packet = self.published(turn)
            if not 0 <= start_s < end_s <= packet["end_s"]:
                raise ValueError("choose an interval within the time viewed by this turn")
            if not packet.get("audio"):
                return [text_block({"status": "no_audio"})]
            with (nullcontext(self.layers) if self.layers is not None else LayerReader()) as layers:
                result = layers.inspect(directory, start_s, end_s, channel, layer, focus)
            wavs = [Path(result[k]) for k in ("mix_wav", "layer_wav") if k in result]
            if native_audio and sum(w.stat().st_size for w in wavs) > MAX_INLINE_PCM_BYTES:
                raise ValueError("the native mix and estimate exceed the inline limit; shorten the range or disable native_audio")
            if describe:
                with self.sound_session(replay=True) as sound:
                    describe_layer(sound, result)
        blocks, paths, order = [], [], []
        with TemporaryDirectory() as tmp:
            for name in ("mix", "layer"):
                if wav := result.pop(name + "_wav", None):
                    paths.append(Path(wav))
                    order.append(name)
                    png = Path(tmp) / (name + ".png")
                    channel_spectrum(wav, png, 1)
                    blocks.append(image_block(png))
        readings = {k: result.pop(k) for k in ("mix_reading", "estimate_reading") if k in result}
        result = {**readings, **result, "images_in_order": order, "wavs_in_order": order if native_audio else []}
        if native_audio:
            blocks.extend(audio_block(path) for path in paths)
        return [text_block(result), *blocks]

    def compare_heard(self, turn, reference, target, question=None, native_audio=False, describe=True):
        if question is not None and not describe:
            raise ValueError("enable the local description to ask a listening question")
        with self.lock:
            directory, packet = self.published(turn)
            if not packet.get("audio"):
                return [text_block({"status": "no_audio"})]
            clips = recordings(directory, reference, target)
            paths = [Path(clip["wav"]) for clip in clips.values()]
            if native_audio and sum(path.stat().st_size for path in paths) > MAX_INLINE_PCM_BYTES:
                raise ValueError("both WAVs together exceed the inline limit; shorten the ranges or disable native_audio")
            report = {name: {k: v for k, v in clip.items() if k != "wav"} for name, clip in clips.items()}
            if describe:
                with self.sound_session(replay=True) as sound:
                    report["independent_readings"] = describe_recordings(sound, clips, question)
        blocks = []
        with TemporaryDirectory() as tmp:
            for (name, clip), path in zip(clips.items(), paths, strict=True):
                spectrum = Path(tmp) / f"{name}.png"
                channel_spectrum(path, spectrum, clip["channels"])
                blocks.append(image_block(spectrum))
        if native_audio:
            blocks.extend(audio_block(path) for path in paths)
        report.update(images_in_order=["reference spectrum", "target spectrum"],
                      wavs_in_order=["reference", "target"] if native_audio else [])
        return [text_block(report), *blocks]

    def speech(self, turn, lang=None):
        with self.lock:
            directory, packet = self.published(turn)
            if not packet["audio"]:
                return [text_block({"status": "no_audio"})]
            original = lang is None or lang == self.lang
            saved = self.hearing_by_turn.get(turn, {}).get("speech")
            if original and saved and saved["status"] == "available":
                return [text_block(saved)]
            with self.sound_session(replay=True):
                reading = self.words.inspect(directory, self.lang if original else lang)
            if original and reading["status"] == "available":
                self.hearing_by_turn.setdefault(turn, {})["speech"] = reading
            return [text_block(reading)]


MEDIA_PART_BYTES = 4 * 1024 * 1024


def read_part(path, start, length):
    with path.open("rb") as handle:
        handle.seek(start)
        return handle.read(length)


class TurnMedia:
    """Publish playable recordings of released intervals. Keep bytes on disk between requests."""

    def __init__(self, server, source):
        self.server = server
        self.original = source if isinstance(source, RecordedSource) else RecordedSource(source)
        self.session = uuid4().hex
        self.published = {}

    def view(self, reader, turn, continuous_seconds):
        with reader.lock:
            published = reader.release(turn, continuous_seconds=continuous_seconds, recording_only=True)
            if published is None:
                return [text_block({"status": "end_of_view", "last_turn": len(reader.paths)})]
            if turn in self.published:
                return self.published[turn]
            directory, packet = published
            audio = packet["audio"]
            wav = directory / audio["wav"] if audio else None
            pages = packet["pages"]
            picture_end = packet.get("picture_end_s")
            clip = packet.get("media")
            if clip:
                path = directory / clip["file"]
            elif pages:
                try:
                    with self.original.reading() as movie:
                        clip = replay(directory, movie, packet["start_s"], packet["end_s"], picture_end,
                                      pages[0]["pts_us"][0], wav)
                except (OSError, ValueError, SubprocessError):
                    raise ValueError("could not reproduce the published pictures from the original film") from None
                path = Path(clip["file"]) if clip else None
            else:
                path = wav
            report = {"status": "view", "turn": turn, "start_s": packet["start_s"], "end_s": packet["end_s"],
                      "picture_end_s": picture_end, "audio": None, "media": None}
            if audio:
                report["audio"] = {k: audio[k] for k in ("rate_hz", "channels", "first_sample_on_film", "sample_count")}
            links = []
            if path is not None:
                size = path.stat().st_size
                mime = "audio/wav" if path.suffix == ".wav" else "video/quicktime"
                report["media"] = {"mime_type": mime, "bytes": size, "parts": math.ceil(size / MEDIA_PART_BYTES)}
                for index, start in enumerate(range(0, size, MEDIA_PART_BYTES), 1):
                    length = min(size - start, MEDIA_PART_BYTES)
                    name = f"turn-{turn}{path.suffix}" if size <= MEDIA_PART_BYTES else f"turn-{turn}.part-{index}"
                    link = types.ResourceLink(type="resource_link", uri=f"reel://{self.session}/{name}", name=name,
                                              mimeType=mime if size <= MEDIA_PART_BYTES else "application/octet-stream",
                                              size=length)
                    self.server.add_resource(FunctionResource(uri=link.uri, name=name, mime_type=link.mimeType,
                                                              fn=partial(read_part, path, start, length)))
                    links.append(link)
            blocks = [text_block(report), *links]
            self.published[turn] = blocks
            return blocks


def serve(source, *, until=None, speech=False, lang=None, joint=False, gaze=False, describe_sounds=False,
          pause_on_sound_change=False, image_client=False, tracks="both"):
    server = FastMCP("reel", instructions="Start with view on turn 1, then request consecutive turns. "
                     "Use view_media instead if the host can read MCP resources and play a movie with its sound. "
                     "Choose continuous_seconds to follow an action or conversation through cuts. "
                     "Choose max_frames to see successive exposures with their sound when an action moves quickly. "
                     "Omit it to stop and react to changes. "
                     "Use observe to revisit a time range with its pictures and sound together. "
                     "Use ask_scene to ask the local audiovisual reader about an exact interval already viewed. "
                     "For an image-and-text client, view with joint_seconds reads the recent recording and "
                     "moving pictures together. Use listen for a question about sound alone. "
                     "Use listen_layer to pick out an overlapping sound in a recorded channel. "
                     "Use compare_heard to compare sounds from two times you have already heard. " + RECORDING_GUIDE)
    hints = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    with ExitStack() as stack:
        directory = Path(stack.enter_context(TemporaryDirectory(prefix="reel-view-")))
        sound = stack.enter_context(SoundReader())  # Starts on the first request that needs the waveform model.
        words = stack.enter_context(SpeechReader(sound))
        playback = None

        def next_turn(requested):
            nonlocal playback
            pace = requested if isinstance(requested, PlaybackPace) else PlaybackPace(requested, False)
            if playback is None:
                playback = stack.enter_context(closing(play(source, directory / "turns", until=until, step=pace.seconds,
                                                           continuous=pace.continuous, max_frames=pace.max_frames,
                                                           recording_only=pace.recording_only,
                                                           max_continuous_pcm_bytes=MAX_INLINE_PCM_BYTES, tracks=tracks,
                                                           speech=speech, gaze=gaze, lang=lang, joint=joint,
                                                           describe_sounds=describe_sounds, sound_reader=sound,
                                                           pause_on_sound_change=pause_on_sound_change)))
                return next(playback, None)
            try:
                return playback.send(pace)
            except StopIteration:
                return None

        layers = stack.enter_context(LayerReader())
        reader = Viewing(next_turn, sound, words, lang, source, layers, stack.enter_context(PointReader()), layers.matcher)

        media = TurnMedia(server, reader.original)

        @server.tool(annotations=hints, structured_output=False)
        async def view_media(turn: int, continuous_seconds: float | None = None):
            """Receive a playable movie with recorded sound, or WAV for sound alone. Shares view's clock.
            Start at turn 1. Advance one turn or replay one already viewed. continuous_seconds (up to 8)
            plays through changes. Read the returned resource links in order using resources/read. If there
            are multiple parts, concatenate their decoded bytes before playback. Times refer to the film.
            Use view for pictures and listening evidence if the host cannot give the model this media."""
            return await asyncio.to_thread(media.view, reader, turn, continuous_seconds)

        @server.tool(annotations=hints, structured_output=False)
        async def view(turn: int, native_audio: bool = not image_client, sound_estimates: bool = True,
                       focus: list[float] | None = None, max_seconds: float | None = None,
                       continuous_seconds: float | None = None, joint_seconds: float | None = None,
                       follow_from_s: float | None = None, question: str | None = None,
                       full_detail: bool = False, max_frames: int | None = None):
            """See and hear one turn, starting at 1. Request the same number to replay, or the next to advance.
            The pause lists the first measured changes in picture, sound level or frequency bands.
            Each sound cue gives its film time for a closer listen. The local readers describe the published
            recording and report supported words. native_audio controls attachment of the original WAV.
            Set native_audio=false to omit it, or start with --image-client. Set sound_estimates=false
            for a first impression without source guesses, transcripts or local model descriptions.
            A picture-and-text view shows relative channel level and arrival over time. Replay with
            full_detail=true for the individual measurements and encoder timing.
            Pass focus=[x,y] (0..1 from top left) to magnify a fixed region. Add follow_from_s with
            the time when you saw that point to follow it across published pictures, up to eight
            seconds through this turn. The ring marks estimated positions and can drift. An
            untracked point has no reliable position. You also receive the whole scene and sound.
            max_seconds (up to 2) sets how soon to pause on an advance, including the first.
            Choose continuous_seconds (up to 8) to watch through changes for that duration.
            To see fast motion on the first pass, set max_frames (1..12). The first image shows every
            displayed exposure in order, including a picture already on screen at the start. The recording
            stops on the same clock. With this option alone, a turn lasts at most two seconds.
            With joint_seconds (up to 8), a local reader hears the recent original recording alone and again
            with its moving pictures, through this turn's end and across earlier pauses. This explicit
            request also works with sound_estimates=false and when replaying a published turn.
            Ask a question about what you saw and heard together using question. By itself it covers
            this turn. With joint_seconds it includes that much recent history. The recording also gets a
            waveform-only answer to your question, even where the camera shows no picture.
            The film cutoff or inline PCM limit can end the turn sooner. Read end_s to continue.
            A replay keeps its published times. An advance with no duration uses the usual two-second limit."""
            return await asyncio.to_thread(reader.advance, turn, native_audio, sound_estimates, focus, max_seconds,
                                           continuous_seconds, joint_seconds, image_hearing=True,
                                           follow_from_s=follow_from_s, question=question,
                                           full_detail=full_detail or native_audio or not sound_estimates, max_frames=max_frames)

        @server.tool(annotations=hints, structured_output=False)
        async def frames(turn: int, page: int):
            """See every displayed frame in a published turn, in page order starting from 1."""
            return await asyncio.to_thread(reader.frames, turn, page)

        @server.tool(annotations=hints, structured_output=False)
        async def ask_scene(start_s: float, end_s: float, question: str | None = None, native_audio: bool = False,
                            alternatives: list[str] | None = None, sync_regions: list[list[float]] | None = None):
            """Ask what happened within an exact span of released film, up to eight seconds across pauses.
            With only start_s and end_s, get pictures, channel levels and times of frequency-band changes
            without starting a model. Give alternatives to compare sound descriptions with each second of original
            audio on the same clock, or [] for catalogue neighbours. A match can have an unrelated visible cause. Supply a question for a local reading of the waveform alone and with the
            pictures. A band change marks an acoustic observation. Its source may be unknown.
            For competing visible movements, pass sync_regions as normalized [left,top,right,bottom] boxes.
            The diagram shows changes in the centre square of each box above the channel peaks. The optional
            sync reader compares these regions with the same audio. Synchronized motion
            can be incidental and the sound may come from offscreen. native_audio=true attaches the same recording."""
            return await asyncio.to_thread(reader.ask_scene, start_s, end_s, question, native_audio, alternatives, sync_regions)

        @server.tool(annotations=hints, structured_output=False)
        async def observe(start_s: float, end_s: float, box: list[float] | None = None,
                          native_audio: bool = not image_client):
            """Revisit a seen interval in film seconds with successive pictures and their simultaneous sound.
            An optional [left,top,right,bottom] box in 0..1 magnifies the picture. Frame times and WAV sample
            spans share a clock. If next_start_s is present and non-null, continue there with the same end_s."""
            return await asyncio.to_thread(reader.observe, start_s, end_s, box, native_audio)

        @server.tool(annotations=hints, structured_output=False)
        async def look(turn: int, box: list[float], at_s: float | None = None):
            """Inspect original pixels in a box [left,top,right,bottom] from 0 to 1. Choose a film time before
            the end of this published turn, or omit at_s for its final picture."""
            return await asyncio.to_thread(reader.look, turn, box, at_s)

        @server.tool(annotations=hints, structured_output=False)
        async def listen(turn: int, question: str | None = None, start_s: float | None = None,
                         end_s: float | None = None, native_audio: bool = False, describe: bool = True,
                         component: Component = "mix", alternatives: list[str] | None = None, per_second: bool = False):
            """Rehear this turn or a film-time range already heard by this turn. Listening can cross pauses.
            A time range includes a channel spectrum. When sounds overlap, choose component="harmonic"
            to emphasize sustained frequency bands or "percussive" to emphasize short broadband changes.
            Use "mix" for the original. The local reader compares a selected component with the original.
            Supply alternatives to compare proposed sounds against the original recording without pictures.
            An empty list retrieves the closest catalogue sounds. Set per_second=true to compare each second
            independently, including the last partial second. The scores do not certify a source.
            native_audio includes the selected WAV.
            Set describe=false to use the recording and spectrum without the local waveform reader.
            The local reader can answer a question. Continue from next_start_s if set, keeping the same end_s."""
            return await asyncio.to_thread(reader.listen, turn, question, start_s, end_s, native_audio, describe, component,
                                           alternatives, per_second)

        @server.tool(annotations=hints, structured_output=False)
        async def compare_heard(turn: int, reference: list[float], target: list[float], question: str | None = None,
                                native_audio: bool = not image_client, describe: bool = True):
            """Compare two previously heard film-time ranges, each [start_s, end_s] up to eight seconds.
            Listen for a recurring sound, a new sound, or a change in the background. Both original
            channel spectra are included. native_audio adds the two WAVs in reference, target order.
            describe reads each recording independently, to keep its sounds tied to that interval. Only the audio enters the reader."""
            return await asyncio.to_thread(reader.compare_heard, turn, reference, target, question, native_audio, describe)

        @server.tool(annotations=hints, structured_output=False)
        async def listen_layer(turn: int, start_s: float, end_s: float, layer: int | None = None,
                               focus: str | None = None, channel: int = 1, native_audio: bool = not image_client, describe: bool = True):
            """Listen to a mix and one estimated sound within it over up to ten already heard seconds.
            Choose a recorded channel (1-based) and layer 1–8, ranked loudest first for this interval.
            Or omit layer and use focus to seek a sound in your own words with local CLAP. It can
            select the mix (0). A match does not establish presence. Layer 2 can expose a quieter sound. An estimate can contain artifacts or several
            sources. Both spectra, each layer's level, and optionally both WAVs and local readings are returned.
            Check words against heard_words. The optional separator is prepared with reel setup --layers."""
            return await asyncio.to_thread(reader.listen_layer, turn, start_s, end_s, channel, layer, native_audio, describe, focus)

        @server.tool(annotations=hints, structured_output=False)
        async def heard_words(turn: int, lang: str | None = None):
            """Check voice activity and timed words through this turn. If you know the language, pass its code
            (e.g. bn for Bengali) to rehear this recording. The hint applies only to this request.
            possible_words are uncertain gap readings."""
            return await asyncio.to_thread(reader.speech, turn, lang)

        server.run(transport="stdio")
