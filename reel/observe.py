"""Read pictures and recorded sound together on the presentation clock."""
from __future__ import annotations

from contextlib import ExitStack, nullcontext
import json
from pathlib import Path

if __package__:
    from .action_view import action_frames, action_views
    from .turn_audio import released_samples
    from .replay import replay
    from .common import open_recording, playback_tracks, probe, recording_levels, reel_source, sample_boundary, video_end
    from .frames import frame_sheets, frames_from_sheets, read_sequence, sheet_size
    from .sound_glimpses import SoundChanges, sheet_picture, sound_glimpses
    from .stereo import StereoHistory
    from .scene_audio import inspect_scene
    from .hearing import SoundReader
    from .listen import _slice_audio
    from .region_sync import regional_timing, validate_regions
else:
    from action_view import action_frames, action_views
    from turn_audio import released_samples
    from replay import replay
    from common import open_recording, playback_tracks, probe, recording_levels, reel_source, sample_boundary, video_end
    from frames import frame_sheets, frames_from_sheets, read_sequence, sheet_size
    from sound_glimpses import SoundChanges, sheet_picture, sound_glimpses
    from stereo import StereoHistory
    from scene_audio import inspect_scene
    from hearing import SoundReader
    from listen import _slice_audio
    from region_sync import regional_timing, validate_regions


def observe(out, start, end, *, sync_regions=(), media=False, focus=None, playback_source=None,
            question=None, sound_reader=None, joint=False):
    validate_regions(sync_regions)
    if focus is not None:
        validate_regions([focus])
    out = Path(out)
    tracks = "both"
    if playback_source is None:
        meta = json.loads((out / "meta.json").read_text())
        limit = meta.get("until", meta["dur"])
        source = reel_source(out, meta)
    else:
        view = json.loads((out / "view.json").read_text())
        if view.get("schema") != "reel.play.v1":
            raise ValueError("no playback interval to inspect")
        tracks = view.get("tracks", "both")
        limit = view["end_s"]
        source = Path(playback_source)
        meta = {}
        if source.is_dir():
            meta = json.loads((source / "meta.json").read_text())
            source = reel_source(source, meta)
    if not 0 <= start < end <= limit:
        raise ValueError(f"observation must be within 0 to {limit:g} seconds")
    info = playback_tracks(probe(source), tracks)
    pages = []
    # Start with no picture. A late-starting track and an audio-only tail are both observable.
    changes = {start: None}
    if info["has_video"]:
        picture_end = meta["picture_end_s"] if "picture_end_s" in meta else video_end(source, info)
        if start < picture_end:
            pages = read_sequence(out, source, info, start, min(end, picture_end), picture_end, allow_empty=True)["pages"]
            for page, sheet in enumerate(pages, 1):
                for cell, pts in enumerate(sheet["pts_us"], 1):
                    changes[max(start, pts / 1_000_000)] = {"page": page, "cell": cell, "pts_us": pts}
            if picture_end < end:
                changes[picture_end] = None
    times = sorted(changes) + [end]
    report = {"schema": "reel.observe.v1", "start_s": start, "end_s": end,
              "pages": [page["file"] for page in pages], "audio": None, "moments": []}
    with ExitStack() as stack:
        audio = None
        if info["has_audio"]:
            rate = info["audio_rate"]
            first, stop = (sample_boundary(t, rate) for t in (start, end))
            if first < stop:
                if playback_source is None:
                    wav = _slice_audio(source, out, start, end)
                else:
                    wav, _, _, first, stop = released_samples(out, start, end, end - start)
                audio = stack.enter_context(open_recording(wav))
                if audio.samplerate != rate or len(audio) != stop - first:
                    raise ValueError("the extracted audio does not match the requested sample range")
                report["audio"] = {"wav": str(wav.resolve()), "rate_hz": rate, "channels": audio.channels,
                                   "first_sample_on_film": first, "samples_per_channel": len(audio)}
                report["audio"]["scene"] = inspect_scene(wav, first, end)
                if audio.channels > 1:
                    cues = StereoHistory().read(wav, start, end, info.get("audio_layout", ""))
                    if cues:
                        report["audio"]["stereo_cues"] = cues
        for left, right in zip(times, times[1:]):
            moment = {"start_s": left, "end_s": right, "picture": changes[left]}
            if audio is not None:
                a = 0 if left == start else sample_boundary(left, rate) - first
                b = len(audio) if right == end else sample_boundary(right, rate) - first
                moment.update(recording_levels(audio, a, b))
            report["moments"].append(moment)
    if report["audio"]:
        audio_info = report["audio"]
        audio_info["band_changes"] = SoundChanges().scan(audio_info["wav"], audio_info["first_sample_on_film"])
    if report["audio"] and info["has_video"]:
        report["sound_views"] = sound_glimpses(out, audio_info["band_changes"], sheet_picture(pages, sheet_size(info)),
                                               start, end, picture_end)
        for event in report["sound_views"]["events"]:
            if "file" in event:
                event["file"] = str((out / event["file"]).resolve())
    if info["has_video"] and pages:
        size = sheet_size(info)
        details = pages
        if focus is not None:
            size, frames = action_frames(source, info, start, min(end, picture_end), focus)
            details = frame_sheets(out, *size, frames)
        track = report["audio"]
        report["action_view"] = action_views(out, frames_from_sheets(details, size), size, start, end, picture_end,
                                             track["wav"] if track else None, track["first_sample_on_film"] if track else 0,
                                             focus, chronological=True)
        if focus is not None:
            report["action_view"]["frame_pages"] = details
        for page in report["action_view"]["pages"]:
            page["file"] = str((out / page["file"]).resolve())
    if joint and report["audio"]:
        manager = SoundReader() if sound_reader is None else nullcontext(sound_reader)
        with manager as reader:
            # Give the joint reader the full scene even when a separate motion view has a focus box.
            report["joint"] = reader.joint(report["audio"]["wav"], start, end, report["audio"]["first_sample_on_film"],
                                           pages, sheet_size(info) if pages else None, picture_end if pages else start, question)
    elif joint:
        report["joint"] = {"status": "no_audio"}
    elif question is not None and report["audio"]:
        manager = SoundReader() if sound_reader is None else nullcontext(sound_reader)
        with manager as reader:
            report["sound_descriptions"] = reader.read(report["audio"]["wav"], start, end, question=question)
    if media:
        report["media"] = replay(out, source, start, end, picture_end if info["has_video"] else 0,
                                 pages[0]["pts_us"][0] if pages else None,
                                 report["audio"]["wav"] if report["audio"] else None)
    if sync_regions:
        report["region_timing"] = regional_timing(source, report, sync_regions)
    return report
