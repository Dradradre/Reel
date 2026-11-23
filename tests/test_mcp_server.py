import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from importlib.util import find_spec
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import numpy as np
import soundfile as sf

if find_spec("mcp"):
    from reel.mcp_server import Viewing


@unittest.skipUnless(find_spec("mcp"), "MCP extra is not installed")
class TransportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        sf.write(self.root / "sound.wav", np.arange(8000, dtype=np.int16), 8000)
        self.wav = (self.root / "sound.wav").read_bytes()
        Image.new("RGB", (18, 12), "teal").save(self.root / "page.png")
        self.turn = {"schema": "reel.play.v1", "start_s": 0, "end_s": 1,
                     "pages": [{"file": "page.png", "pts_us": [0, 500_000]}],
                     "audio": {"wav": "sound.wav", "spectrum": "page.png", "rate_hz": 8000,
                               "sample_count": 8000, "channels": 1, "signal": "nonzero"},
                     "current_frame": {"file": "page.png", "pts_us": 500_000}}
        self.path = self.root / "view.json"
        self.path.write_text(json.dumps(self.turn))

    def test_optional_text_reads_only_the_published_crop(self):
        reader = Viewing(iter([self.path]), None, None, None)
        reader.advance(1, False)
        with patch("reel.mcp_server.read_crop", return_value={"status": "available", "lines": []}) as recognize:
            plain, picture = reader.look(1, [0, 0, .5, 1])
            self.assertNotIn("screen_text", json.loads(plain.text))
            with self.assertRaises(ValueError):
                reader.look(1, [0, 0, .5, 1], at_s=1, read_text=True)
            recognize.assert_not_called()
            text, same_picture = reader.look(1, [0, 0, .5, 1], at_s=.75, read_text=True)
            self.assertEqual(json.loads(text.text)["screen_text"], {"status": "available", "lines": []})
            self.assertEqual(same_picture, picture)
            self.assertEqual(json.loads(text.text)["pts_us"], 500000)
            with Image.open(recognize.call_args.args[0]) as crop:
                self.assertEqual(crop.size, (9, 12))

    def test_selected_tracks_survive_native_replay_and_reinspection_on_the_film_clock(self):
        from reel.play import play

        # Sound lasts 2 s. Picture comes in late, changes at 0.75 s, and ends before the sound.
        source = self.root / "mixed.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-s", "16x16", "-framerate", "4", "-i", "-",
                        "-f", "lavfi", "-i", "aevalsrc=if(lt(t\\,1.2)\\,0\\,0.25):s=8000:d=2",
                        "-vf", "setpts=PTS+1", "-fps_mode", "passthrough", "-c:v", "ffv1", "-c:a", "pcm_s16le", str(source)],
                       input=b"".join(bytes([v]) * 256 for v in [0, 0, 240, 240]), check=True)
        published = {}
        with (patch("reel.play.CAPTIONS_READY", self.root / "absent"),
              patch("reel.play.LocalReader") as tagger, patch("reel.observe.inspect_scene", return_value={})):
            tagger.return_value.__enter__.return_value.request.return_value = {}
            for tracks in ("both", "picture", "sound"):
                with closing(play(source, self.root / tracks, tracks=tracks, until=1.75,
                                  step=1, continuous=True, media=True)) as playback:
                    paths = list(playback)
                packets = [json.loads(path.read_text()) for path in paths]
                published[tracks] = list(zip(paths, packets))
                self.assertEqual([(v["start_s"], v["end_s"]) for v in packets], [(0., 1.), (1., 1.75)])
                view = Viewing(iter(paths), None, None, None, source=source)
                for turn, packet in enumerate(packets, 1):
                    blocks = view.advance(turn, native_audio=True)
                    self.assertEqual(any(b.type == "audio" for b in blocks), tracks != "picture")
                    self.assertEqual(bool(packet["pages"]), tracks != "sound")
                    media = paths[turn - 1].parent / packet["media"]["file"]
                    streams = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                                                       "stream=codec_type", "-of", "csv=p=0", str(media)], text=True)
                    self.assertEqual(set(streams.split()), {"both": {"audio", "video"}, "sound": {"audio"},
                                                           "picture": {"video"}}[tracks])
                # Revisit an interval across two turns. The presenter still holds the full source.
                receipt = view.observe(.125, 1.6, [0, 0, .5, .5], native_audio=True)
                report = json.loads(receipt[0].text)
                self.assertEqual(report["end_s"], 1.6)
                self.assertEqual(report["moments"][0]["picture"], None)
                self.assertEqual(report["moments"][-1]["picture"], None)
                self.assertEqual(any(m["picture"] is not None for m in report["moments"]), tracks != "sound")
                self.assertEqual(any(b.type == "audio" for b in receipt), tracks != "picture")
                if tracks == "sound":
                    self.assertEqual(report["images_in_order"], ["recorded channel spectrum"])
                    with self.assertRaisesRegex(ValueError, "no picture"):
                        view.look(2, [0, 0, 1, 1], at_s=.8)
                if tracks == "picture":
                    self.assertIsNone(report["audio"])
                    self.assertEqual(json.loads(view.listen(2, None, .3, 1.4, native_audio=True)[0].text),
                                     {"status": "no_audio"})
                with self.assertRaisesRegex(ValueError, "already viewed"):
                    view.observe(0, 1.751, None, True)
            for (whole, av), (seen, v), (heard, a) in zip(*published.values(), strict=True):
                self.assertEqual(av["pages"], v["pages"])
                for page in v["pages"]:
                    self.assertEqual((seen.parent / page["file"]).read_bytes(), (whole.parent / page["file"]).read_bytes())
                self.assertEqual((heard.parent / a["audio"]["wav"]).read_bytes(),
                                 (whole.parent / av["audio"]["wav"]).read_bytes())
            for tracks, expected in (("picture", .8), ("sound", 1.3)):
                with closing(play(source, self.root / ("paced-" + tracks), tracks=tracks, step=2)) as playback:
                    first = json.loads(next(playback).read_text())
                self.assertAlmostEqual(first["end_s"], expected, places=1)
                hidden = "sound" if tracks == "picture" else "picture"
                self.assertFalse(any(key.startswith(hidden) for key in first["pause"]))

    def test_movie_only_turn_starts_no_recognition_helpers_then_normal_view_can_use_them(self):
        from reel.play import PlaybackPace, play

        source = self.root / "recording.wav"
        sf.write(source, np.full(2400, 500, dtype=np.int16), 8000)
        with (patch("reel.play.LocalReader") as tagger, patch("reel.play.CAPTIONS_READY", source),
              patch("reel.play.SceneReader") as scene, patch("reel.play.SpeechReader") as words,
              patch("reel.play.SoundReader") as sound):
            readers = [tagger.return_value.__enter__.return_value.request, scene.return_value.__enter__.return_value.read,
                       words.return_value.__enter__.return_value.read, sound.return_value.__enter__.return_value.joint,
                       sound.return_value.__enter__.return_value.read]
            for read in readers:
                read.return_value = {"status": "available"}
            with closing(play(source, self.root / "viewing", step=.1, continuous=True, recording_only=True,
                              speech=True, joint=True, describe_sounds=True)) as playback:
                first = json.loads(next(playback).read_text())
                for read in readers:
                    read.assert_not_called()
                second = json.loads(playback.send(PlaybackPace(.1, True)).read_text())
                self.assertEqual(first["end_s"], second["start_s"])
                self.assertEqual(first["audio"]["sample_count"], second["audio"]["sample_count"])
                for read in readers:
                    read.assert_called_once()

    def test_media_parts_are_bounded_and_reusable_without_the_source(self):
        from mcp.server.fastmcp import FastMCP
        from reel.mcp_server import TurnMedia

        self.turn["pages"] = []
        self.turn["audio"]["first_sample_on_film"] = 0
        self.path.write_text(json.dumps(self.turn))
        source = self.root / "unpublished-name.wav"
        source.write_bytes(b"input")
        server = FastMCP("test")
        media = TurnMedia(server, source)
        reader = Viewing(iter([self.path]), None, None, None)
        part_size = 4096
        with patch("reel.mcp_server.MEDIA_PART_BYTES", part_size):
            with self.assertRaises(ValueError):
                media.view(reader, 2, None)
            report, *parts = media.view(reader, 1, None)
        self.assertEqual(json.loads(report.text)["media"],
                         {"mime_type": "audio/wav", "bytes": len(self.wav), "parts": len(parts)})
        self.assertEqual([p.size for p in parts], [part_size] * 3 + [len(self.wav) - 3 * part_size])
        self.assertTrue(all(p.mimeType == "application/octet-stream" for p in parts))
        source.unlink()
        self.assertEqual(media.view(reader, 1, None), [report, *parts])
        async def read():
            result = bytearray()
            for link in parts:
                part, = await server.read_resource(link.uri)
                result.extend(part.content)
            return bytes(result)
        self.assertEqual(asyncio.run(read()), self.wav)

    def test_first_view_keeps_reported_disagreement_and_full_detail_replays_the_same_turn(self):
        channels = [{"channels": [i], "status": "read", "audio_only": sound, "with_picture": "figure moves"}
                    for i, sound in enumerate(["brief rattle", "brief rattle", "steady hiss"], 1)]
        self.turn["joint"] = {"windows": [{"start_s": 0, "end_s": 1, "picture_pts_us": [0, 500_000],
                                           "wav_samples": [0, 8000], "channels": channels}]}
        self.turn["audio"].update(band_changes=[{"time_s": .36, "channels": [1], "band_change_db": 7}],
                                  stereo_cues={"windows": []})
        self.turn["speech"] = {"status": "available", "windows": []}
        self.path.write_text(json.dumps(self.turn))
        reader = Viewing(iter([self.path]), None, None, None)
        short = reader.advance(1, False, full_detail=False)
        full = reader.advance(1, False, full_detail=True)
        self.assertEqual(short[1:], full[1:])
        first, detail = (json.loads(blocks[0].text) for blocks in (short, full))
        self.assertEqual(first["audio"]["band_change_times_s"], [.36])
        self.assertEqual(detail["audio"]["band_changes"], self.turn["audio"]["band_changes"])
        self.assertEqual(first["speech"], detail["speech"])
        self.assertEqual(first["joint"]["windows"][0]["channels"],
                         [{**channels[0], "channels": [1, 2]}, channels[2]])
        self.assertEqual(detail["joint"], self.turn["joint"])
        self.assertEqual(reader.advance(1, False, full_detail=False), short)
        self.assertEqual(len(reader.paths), 1)

    def test_first_view_keeps_motion_between_channels_and_unresolved_spatial_cues(self):
        pairs = [{"channels": [1, 2], "speakers": ["FL", "FR"]},
                 {"channels": [5, 6], "speakers": ["SL", "SR"]}]
        leading = {"rms_dbfs": [-20, -20], "second_minus_first_db": 0, "second_minus_first_us": 500,
                   "correlation": -.98, "timing": "shared waveform"}
        trailing = dict(leading, second_minus_first_us=-500)
        unknown = dict(rms_dbfs=[-14, -20], second_minus_first_db=-6, second_minus_first_us=None,
                       timing="no consistent lag")
        one = dict(rms_dbfs=[None, -18], second_minus_first_db=None, second_minus_first_us=None,
                   timing="one active channel")
        windows = [{"start_s": .25, "end_s": .5, "pairs": [leading, one]},
                   {"start_s": .5, "end_s": .75, "pairs": [dict(leading, second_minus_first_us=440), one]},
                   {"start_s": .75, "end_s": .87, "pairs": [trailing, unknown]}]
        self.turn["audio"].update(channels=6, stereo_cues={"windows": windows, "pairs": pairs})
        self.path.write_text(json.dumps(self.turn))
        reader = Viewing(iter([self.path]), None, None, None)
        cues = json.loads(reader.advance(1, False, full_detail=False)[0].text)["audio"]["stereo_summary"]
        front, surround = cues["pairs"]
        self.assertEqual(front["speakers"], ["FL", "FR"])
        self.assertEqual(front["level"], [{"start_s": .25, "end_s": .87, "level": "equal at recorded precision"}])
        self.assertEqual(front["arrival"], [
            {"start_s": .25, "end_s": .75, "leading_channel": 1, "waveform_polarity": "opposite",
             "by_us_range": [440, 500]},
            {"start_s": .75, "end_s": .87, "leading_channel": 2, "waveform_polarity": "opposite",
             "by_us_range": [500, 500]}])
        self.assertEqual(surround["channels"], [5, 6])
        self.assertEqual(surround["level"], [
            {"start_s": .25, "end_s": .75, "only_active_channel": 6},
            {"start_s": .75, "end_s": .87, "louder_channel": 5, "by_db_range": [6, 6]}])
        self.assertEqual(surround["arrival"][-1]["timing"], "no consistent lag")
        detail = json.loads(reader.advance(1, False)[0].text)["audio"]["stereo_cues"]
        self.assertEqual(detail["windows"], windows)

    def test_two_concurrent_retries_release_one_turn_and_preserve_original_media(self):
        playback = iter([self.path, self.root / "unreleased.json"])
        reader = Viewing(playback, None, None, None)
        for call in (lambda: reader.frames(1, 1), lambda: reader.advance(2, True)):
            with self.assertRaises(ValueError):
                call()
        with ThreadPoolExecutor(2) as workers:
            a, b = workers.map(lambda _: reader.advance(1, True), [0, 1])
        self.assertEqual(a, b)
        self.assertEqual(reader.paths, [self.path])
        self.assertEqual([block.type for block in a], ["text", "image", "image", "audio"])
        self.assertNotIn(str(self.root), a[0].text)
        self.assertEqual(base64.b64decode(a[-1].data), self.wav)
        self.assertEqual(reader.advance(1, False), a[:-1])
        sheet_info, sheet = reader.frames(1, 1)
        self.assertEqual(json.loads(sheet_info.text)["picture_pts_us"], [0, 500_000])
        self.assertEqual(base64.b64decode(sheet.data), (self.root / "page.png").read_bytes())
        with self.assertRaises(ValueError):
            reader.frames(1, 2)

    def test_first_view_includes_the_published_sound_and_gaze_pictures(self):
        frames = [{"at_s": .25, "pts_us": 0}]
        event = {"time_s": .25, "channels": [1], "band_change_db": 5.5}
        self.turn["sound_views"] = {"events": [{**event, "file": "page.png", "frames": frames}]}
        self.turn["audio"]["band_changes"] = [event]
        self.turn["audio"]["scene"] = {"status": "available", "description": "voices"}
        self.turn["audio"]["source_estimates"] = {"status": "available"}
        self.turn.update(speech={"status": "available"}, joint={"status": "available"})
        self.turn["sound_descriptions"] = {"status": "available", "windows": [{"start_s": 0, "end_s": 1}]}
        self.turn["gaze"] = {"model": "UNISAL/DHF1K", "status": "available", "rate_hz": 6,
                             "recent_details": {"file": "page.png", "samples": [{"tick": 3, "pts_us": 500000, "point": None}]}}
        self.path.write_text(json.dumps(self.turn))
        reader = Viewing(iter([self.path]), None, None, None)
        blocks = reader.advance(1, False)
        info = json.loads(blocks[0].text)
        self.assertEqual(info["audio"]["band_changes"], [event])
        self.assertEqual(info["sound_descriptions"], self.turn["sound_descriptions"])
        self.assertEqual(info["images_in_order"][:2], [{"pictures_at_band_change_s": .25, "frames": frames},
                         {"predicted_gaze_details": [{"time_s": .5, "pts_us": 500000, "centre_fallback": True}]}])
        self.assertNotIn("page.png", blocks[0].text)
        self.assertEqual([b.type for b in blocks[1:]], ["image"] * len(info["images_in_order"]))
        self.assertEqual(reader.advance(1, True)[:-1], blocks)
        plain = reader.advance(1, True, sound_estimates=False)
        physical = json.loads(plain[0].text)
        self.assertEqual(info["images_in_order"], physical["images_in_order"])
        self.assertEqual(physical["audio"], {k: v for k, v in info["audio"].items() if k not in ("scene", "source_estimates")})
        self.assertTrue({"speech", "joint", "sound_descriptions"}.isdisjoint(physical))
        self.assertEqual(plain[1:], reader.advance(1, True)[1:])
        self.assertEqual(reader.advance(1, False), blocks)

    def test_an_audio_only_first_view_reports_a_change_at_constant_level(self):
        from reel.play import play

        rate = 16000
        t = np.arange(rate) / rate
        wave = .25 * np.sin(2 * np.pi * np.where(t < .5, 250, 2000) * t)
        source = self.root / "sound-only.wav"
        sf.write(source, np.column_stack([wave, -wave]), rate, subtype="FLOAT")
        with closing(play(source, self.root / "played", pause_on_sound_change=True)) as playback:
            reader = Viewing(playback, None, None, None)
            blocks = reader.advance(1, False)
        report = json.loads(blocks[0].text)
        self.assertEqual(report["images_in_order"], ["recorded channel spectrum"])
        change, = report["audio"]["band_changes"]
        self.assertLess(abs(change["time_s"] - .5), .08)
        self.assertEqual(change["channels"], [1, 2])
        self.assertEqual(report["pause"], {"reason": "change", "sound_band_change_s": change["time_s"]})
        self.assertLess(change["time_s"], report["end_s"])
        self.assertLessEqual(report["end_s"], .6)
        with closing(play(source, self.root / "usual")) as playback:
            usual = json.loads(Viewing(playback, None, None, None).advance(1, False)[0].text)
        self.assertEqual(usual["end_s"], 1.)
        self.assertEqual(usual["pause"]["reason"], "interval")
        self.assertEqual(usual["audio"]["band_changes"], [change])

    def test_missing_track_repeated_end_and_revisit(self):
        self.turn.update(audio=None, current_frame=None, pages=[])
        self.path.write_text(json.dumps(self.turn))
        reader = Viewing(iter([self.path]), None, None, None)
        initial = reader.advance(1, True)
        self.assertIsNone(json.loads(initial[0].text)["audio"])
        self.assertEqual(len(initial), 1)
        self.assertEqual(json.loads(reader.speech(1)[0].text)["status"], "no_audio")
        self.assertEqual(json.loads(reader.listen(1, None)[0].text)["status"], "no_audio")
        end = reader.advance(2, True)
        self.assertEqual(json.loads(end[0].text), {"status": "end_of_view", "last_turn": 1})
        self.assertEqual(reader.advance(2, False), end)
        self.assertEqual(reader.advance(1, True), initial)

    def test_followup_uses_only_the_requested_published_turn(self):
        sound, words = Mock(), Mock()
        sound.read.return_value = {"status": "available"}
        words.inspect.return_value = {"status": "available"}
        reader = Viewing(iter([self.path]), sound, words, "en")
        reader.advance(1, False)
        reader.listen(1, "What changed?")
        sound.read.assert_called_once_with(self.root / "sound.wav", 0, 1, question="What changed?")
        original = reader.speech(1)
        words.inspect.assert_called_once_with(self.root, "en")
        words.inspect.return_value = {"status": "available", "language_hint": "bn"}
        self.assertNotEqual(reader.speech(1, "bn"), original)
        self.assertEqual(reader.speech(1), original)
        self.assertEqual(words.inspect.call_count, 2)

    def test_joint_context_uses_published_frames_and_pcm_across_pauses(self):
        from reel.joint import read_joint
        from reel.play import play

        rate = 8000
        wave = self.root / "original.wav"
        samples = np.arange(2 * rate, dtype="int16")
        sf.write(wave, samples, rate)
        film = self.root / "clip.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=80x44:rate=17:duration=2",
                        "-i", str(wave), "-c:v", "ffv1", "-c:a", "pcm_s16le", str(film)], check=True)

        class Capture:
            calls = 0

            def joint(self, wav, start, end, first, pages, size, picture_end, question=None, *, long_context):
                self.calls += 1
                data, sr = sf.read(wav, dtype="int16")
                np.testing.assert_array_equal(data, samples[first:first + len(data)])
                self.last = (first, len(data), sr)
                return read_joint(Mock(return_value="sound", with_picture=Mock(return_value="action")),
                                  wav, start, end, first, pages, size, picture_end, question=question, long_context=long_context)

        ear = Capture()
        results = []
        for step in (.53, 2):
            with closing(play(film, self.root / str(step), until=1.85, step=step, continuous=True)) as playback:
                viewer = Viewing(playback, ear, None, None)
                with self.assertRaises(ValueError):
                    viewer.advance(1, False, joint_seconds=8.01)
                self.assertEqual(viewer.paths, [])
                turn = 1
                while json.loads(viewer.advance(turn, False)[0].text)["end_s"] < 1.85:
                    turn += 1
                if step == 2:
                    film.unlink()  # Replaying the context needs only published media.
                report = json.loads(viewer.advance(turn, False, False, joint_seconds=1.4)[0].text)
                self.assertNotIn(str(self.root), json.dumps(report))
                self.assertAlmostEqual(report["joint"]["requested_start_s"], .45)
                self.assertEqual(ear.last, (3600, 11200, rate))
                results.append(report["joint"])
                calls = ear.calls
                replay = json.loads(viewer.advance(turn, True, joint_seconds=1.4)[0].text)
                self.assertEqual(replay["joint"], report["joint"])
                self.assertEqual(ear.calls, calls)
                for question in ("What moved when the sound began?", "What else is audible?"):
                    asked = json.loads(viewer.advance(turn, False, False, question=question)[0].text)["joint"]
                    self.assertEqual(asked["question"], question)
                    self.assertEqual(asked["requested_end_s"], report["end_s"])
                    self.assertEqual(asked["requested_start_s"], report["start_s"])
                with self.assertRaises(ValueError):
                    viewer.advance(turn + 1, False, question="  ")
                # Replaying an earlier turn still ends at that turn, even after later turns were delivered.
                earlier = json.loads(viewer.advance(1, False, False, joint_seconds=8)[0].text)
                self.assertEqual(earlier["joint"]["requested_end_s"], min(step, 1.85))
                # End inside an old turn. Read exactly this scene even after more of the film was seen.
                first, last = 1177, 6477
                left, right = first / rate, last / rate
                prompt = "What happened when the sound changed?"
                scene = viewer.ask_scene(left, right, prompt, native_audio=True)
                reply = json.loads(scene[0].text)
                self.assertEqual((reply["start_s"], reply["end_s"], reply["joint"]["question"]), (left, right, prompt))
                levels = reply["audio"].pop("levels")
                contour = reply["audio"].pop("pitch_contour")
                self.assertEqual((contour["first_window_sample_on_film"], contour["window_count"]), (1200, 13))
                self.assertEqual(reply["audio"], {"first_sample_on_film": first, "sample_count": last - first,
                                                  "channels": 1, "rate_hz": rate, "wav_attached": True,
                                                  "band_changes": []})
                self.assertEqual([v["wav_samples"] for v in levels], [[0, 4000], [4000, last - first]])
                self.assertEqual(levels[0]["end_s"], levels[1]["start_s"])
                self.assertAlmostEqual(levels[-1]["peak_dbfs"][0], 20 * np.log10((last - 1) / 32768), places=2)
                self.assertEqual(ear.last, (first, last - first, rate))
                window, = reply["joint"]["windows"]
                self.assertLessEqual(window["picture_pts_us"][0], left * 1e6)
                self.assertGreater(window["picture_pts_us"][1], left * 1e6)
                self.assertLess(window["picture_pts_us"][-1], right * 1e6)
                self.assertEqual(window["wav_samples"], [0, last - first])
                self.assertEqual([b.type for b in scene], ["text", "image", "audio"])
                self.assertEqual(reply["images_in_order"][0]["action_over_time"], [left, right])
                original, _ = sf.read(io.BytesIO(base64.b64decode(scene[-1].data)), dtype="int16")
                np.testing.assert_array_equal(original, samples[first:last])
                calls = ear.calls
                retry = viewer.ask_scene(left, right, prompt)
                self.assertFalse(json.loads(retry[0].text)["audio"]["wav_attached"])
                self.assertEqual(retry[1:], scene[1:-1])
                for a, b, q in ((0, 1.851, prompt), (right, left, prompt), (left, right, "  ")):
                    with self.assertRaises(ValueError):
                        viewer.ask_scene(a, b, q)
                self.assertEqual(ear.calls, calls)
                self.assertEqual(len(viewer.paths), turn)
        self.assertEqual(*results)
        window, = results[0]["windows"]
        self.assertEqual(window["picture_pts_us"], sorted(set(window["picture_pts_us"])))
        self.assertLess(window["picture_pts_us"][0], 450000)
        self.assertLess(window["picture_pts_us"][-1], 1850000)

    def test_exact_scene_releases_a_cold_helper_and_preserves_a_warm_listener(self):
        from reel.hearing import SoundReader

        self.turn.update(pages=[], current_frame=None)
        self.turn["audio"]["first_sample_on_film"] = 0
        self.path.write_text(json.dumps(self.turn))
        sf.write(self.root / "sound.wav", np.ones(8000, dtype=np.int16), 8000)
        with SoundReader() as ear:
            ear.command = [sys.executable, "-c", "import sys, os, json\nfor line in sys.stdin: "
                           "print(json.dumps(dict(status='available', pid=os.getpid())), flush=True)"]
            viewer = Viewing(iter([self.path]), ear, None, None)
            viewer.release(1)
            with (patch("reel.mcp_server.MAX_INLINE_PCM_BYTES", 100), patch.object(ear, "joint") as read,
                  self.assertRaisesRegex(ValueError, "inline limit")):
                viewer.ask_scene(0, 1, "What changed?", native_audio=True)
            read.assert_not_called()
            def ask(q):
                return json.loads(viewer.ask_scene(0, 1, q)[0].text)["joint"]["recording"]["pid"]
            cold = ask("What changed?")
            self.assertIsNone(ear.process)
            with self.assertRaises(ProcessLookupError):
                os.kill(cold, 0)
            self.assertEqual(ask("What changed?"), cold)
            warm = ear.request({})["pid"]
            self.assertEqual(ask("What repeats?"), warm)
            self.assertIsNone(ear.process.poll())

    def test_replay_measures_changes_without_starting_a_listener(self):
        from reel.hearing import SoundReader

        rate = 8000
        pcm = np.zeros((rate, 2), dtype=np.int16)
        pcm[2800:3600, 1] = np.rint(8000 * np.sin(2 * np.pi * 900 * np.arange(800) / rate))
        self.turn.update(pages=[], current_frame=None)
        self.turn["audio"].update(first_sample_on_film=0, channels=2)
        self.path.write_text(json.dumps(self.turn))
        sf.write(self.root / "sound.wav", pcm, rate)
        with SoundReader() as ear, patch.object(ear, "request", side_effect=AssertionError("model started")):
            viewer = Viewing(iter([self.path]), ear, None, None)
            viewer.release(1)
            report, recording = viewer.ask_scene(.1, .7, native_audio=True)
            info = json.loads(report.text)
            self.assertEqual(info["joint"], {"status": "not_requested"})
            changes = info["audio"]["band_changes"]
            self.assertTrue(any(.35 <= change["time_s"] < .4 and "rise" in change for change in changes))
            self.assertTrue(all(change["channels"] == [2] for change in changes))
            received, _ = sf.read(io.BytesIO(base64.b64decode(recording.data)), dtype="int16")
            np.testing.assert_array_equal(received, pcm[800:5600])
            early = json.loads(viewer.ask_scene(.1, .35)[0].text)
            self.assertEqual(early["audio"]["band_changes"], [])
            with self.assertRaises(ValueError):
                viewer.ask_scene(.1, 1.1)
            self.assertIsNone(ear.process)

    def test_scene_regions_keep_the_released_motion_view_when_the_sync_helper_is_missing(self):
        from reel.frames import frame_sheets

        rate, size = 8000, (48, 32)
        still = np.zeros((32, 48, 3), dtype=np.uint8)
        changed = still.copy()
        changed[:, 24:] = 220
        frames = [(0, 0, still.tobytes()), (450000, 0, changed.tobytes()), (800000, 0, still.tobytes())]
        self.turn.update(pages=frame_sheets(self.root, *size, frames), picture_size=size, current_frame=None)
        self.turn["audio"].update(first_sample_on_film=0, sample_count=rate)
        self.path.write_text(json.dumps(self.turn))
        sf.write(self.root / "sound.wav", np.arange(rate, dtype=np.int16), rate)
        viewer = Viewing(iter([self.path]), None, None, None)
        viewer.release(1)
        regions = [[0, 0, .5, 1], [.5, 0, 1, 1]]
        with patch("reel.region_sync.SYNC_PY", str(self.root / "missing")):
            plain = viewer.ask_scene(.15, .8, native_audio=True)
            blocks = viewer.ask_scene(.15, .8, native_audio=True, sync_regions=regions)
        self.assertEqual(blocks[1], plain[1])
        self.assertEqual(blocks[-1], plain[-1])
        info = json.loads(blocks[0].text)
        self.assertEqual(info["region_timing"]["status"], "unavailable")
        panel = info["images_in_order"][-1]["region_motion_and_channel_peaks"]
        self.assertEqual(panel["picture_pts_us"], [0, 450000])
        self.assertEqual(panel["region_numbers"], [1, 2])
        self.assertEqual(panel["input_squares_px"], [[0, 4, 24, 28], [24, 4, 48, 28]])
        with Image.open(io.BytesIO(base64.b64decode(blocks[-2].data))) as im:
            self.assertTrue(im.width > im.height)
        with patch("reel.mcp_server.published_timing") as read:
            for end, boxes in [(1.1, regions), (.8, [[0, .1, 2, 1]])]:
                with self.assertRaises(ValueError):
                    viewer.ask_scene(.15, end, sync_regions=boxes)
            read.assert_not_called()

    def test_scene_compares_the_same_released_wav_without_starting_a_waveform_reader(self):
        rate = 8000
        pcm = np.column_stack((np.arange(rate), -np.arange(rate))).astype(np.int16)
        self.turn.update(pages=[], current_frame=None)
        self.turn["audio"].update(first_sample_on_film=0, channels=2)
        self.path.write_text(json.dumps(self.turn))
        sf.write(self.root / "sound.wav", pcm, rate)
        matches = Mock()
        def read(wav, start, end, descriptions, *, first_sample, per_second):
            self.assertTrue(per_second)
            self.assertEqual(descriptions, ["a rattle"])
            self.assertEqual(first_sample, 2000)
            data, _ = sf.read(wav, dtype="int16")
            np.testing.assert_array_equal(data, pcm[2000:6000])
            return {"status": "available", "descriptions": descriptions}
        matches.read.side_effect = read
        viewer = Viewing(iter([self.path]), None, None, None, matcher=matches)
        viewer.release(1)
        with patch("reel.mcp_server.SoundReader", side_effect=AssertionError("waveform reader started")):
            with self.assertRaises(ValueError):
                viewer.ask_scene(.25, 1.1, alternatives=["a rattle"])
            matches.read.assert_not_called()
            for blocks in (viewer.ask_scene(.25, .75, alternatives=["a rattle"]),
                           viewer.listen(1, None, .25, .75, describe=False, alternatives=["a rattle"], per_second=True)):
                report = json.loads(blocks[0].text)
                self.assertEqual(report["original_sound_match"]["status"], "available")
            self.assertEqual(matches.read.call_count, 2)

    def test_scene_alternatives_and_ranged_words_use_the_same_released_recording(self):
        from reel.live_speech import SpeechReader
        from reel.play import play

        rate = 8000
        samples = np.arange(16000, dtype=np.int16)
        source = self.root / "track.wav"
        sf.write(source, samples, rate)
        paths = list(play(source, self.root / "released", step=1, continuous=True, recording_only=True))
        words, matches = SpeechReader(), Mock()
        matches.read.return_value = {"status": "available"}
        reader = Viewing(iter(paths), None, words, "en", matcher=matches)
        readings = []
        def recognize(job):
            data, sr = sf.read(job["wav"], dtype="int16")
            self.assertEqual(sr, rate)
            self.assertEqual(job["history_files"], [])
            readings.append((data, job["lang"]))
            return {"status": "available", "windows": []}

        with patch.object(words, "request", side_effect=recognize):
            reader.release(1)
            with self.assertRaises(ValueError):
                reader.ask_scene(.75, 1.25, alternatives=[])
            reader.release(2)
            base = json.loads(reader.ask_scene(.75, 1.25)[0].text)
            self.assertNotIn("speech", base)
            scene = json.loads(reader.ask_scene(.75, 1.25, alternatives=[])[0].text)
            report = json.loads(reader.speech(2, "ko", .75, 1.25)[0].text)
            self.assertEqual(scene["speech"], report)
            self.assertEqual([lang for _, lang in readings], ["en", "ko"])
            for data, _ in readings:
                np.testing.assert_array_equal(data, samples[6000:10000])
            with self.assertRaises(ValueError):
                reader.speech(1, None, .75, 1.25)
            self.assertEqual(reader.hearing_by_turn, {})

    def test_listen_and_compare_release_only_helpers_they_started(self):
        from reel.hearing import SoundReader

        self.turn.update(pages=[], current_frame=None)
        self.turn["audio"].update(first_sample_on_film=0)
        self.path.write_text(json.dumps(self.turn))
        sf.write(self.root / "sound.wav", np.ones(8000, dtype=np.int16), 8000)
        with SoundReader() as ear:
            ear.command = [sys.executable, "-c", "import sys, os, json\nfor line in sys.stdin: "
                           "print(json.dumps(dict(status='available', pid=os.getpid())), flush=True)"]
            viewer = Viewing(iter([self.path]), ear, None, None)
            viewer.release(1)
            cold = json.loads(viewer.listen(1, None)[0].text)["pid"]
            with self.assertRaises(ProcessLookupError):
                os.kill(cold, 0)
            report = json.loads(viewer.compare_heard(1, [0, .5], [.5, 1])[0].text)
            reads = report["independent_readings"]
            self.assertEqual(reads["reference"]["pid"], reads["target"]["pid"])
            with self.assertRaises(ProcessLookupError):
                os.kill(reads["target"]["pid"], 0)
            warm = ear.request({})["pid"]
            self.assertEqual(json.loads(viewer.listen(1, None)[0].text)["pid"], warm)
            self.assertIsNone(ear.process.poll())

    def test_image_client_reads_published_audio_only_when_requested_and_reuses_evidence(self):
        sound, words = Mock(), Mock()
        sound.read.return_value = {"status": "available", "windows": [{"end_s": 1}]}
        words.inspect.side_effect = [{"status": "unavailable"}, {"status": "available", "windows": []}]
        self.turn["audio"]["first_sample_on_film"] = 0
        self.path.write_text(json.dumps(self.turn))
        reader = Viewing(iter([self.path]), sound, words, "en")
        hidden = reader.advance(1, False, False, image_hearing=True)
        self.assertFalse(json.loads(hidden[0].text)["audio"]["wav_attached"])
        self.assertNotIn("sound_descriptions", json.loads(hidden[0].text))
        words.inspect.assert_not_called()
        sound.read.assert_not_called()
        first = json.loads(reader.advance(1, False, image_hearing=True)[0].text)
        self.assertEqual(first["speech"]["status"], "unavailable")
        self.assertEqual(first["sound_descriptions"], sound.read.return_value)
        restored = reader.advance(1, True, image_hearing=True)
        self.assertTrue(json.loads(restored[0].text)["audio"]["wav_attached"])
        self.assertEqual(base64.b64decode(restored[-1].data), self.wav)
        self.assertEqual(json.loads(reader.speech(1)[0].text), {"status": "available", "windows": []})
        self.assertEqual(words.inspect.call_count, 2)
        sound.read.assert_called_once_with((self.root / "sound.wav").resolve(), 0, 1)
        words.inspect.side_effect = None
        words.inspect.return_value = {"status": "available", "language_hint": "bn", "windows": []}
        self.assertEqual(json.loads(reader.speech(1, "bn")[0].text)["language_hint"], "bn")
        words.inspect.assert_called_with(self.root, "bn")
        self.assertNotIn("language_hint", json.loads(reader.speech(1)[0].text))
        self.assertEqual(words.inspect.call_count, 3)

    def test_first_view_keeps_a_prior_sound_out_of_the_current_impression(self):
        sound, words = Mock(), Mock()
        sound.read.return_value = {"status": "available", "windows": [{"start_s": 1, "end_s": 2}]}
        earlier = {"status": "available", "windows": [{"start_s": 0, "end_s": 2, "current_start_s": 1}]}
        current = {**self.turn, "start_s": 1, "end_s": 2, "sound_descriptions": earlier,
                   "speech": {"status": "available"}, "joint": {"status": "available", "windows": [
                       {"start_s": 0, "end_s": 2, "channels": [{"status": "read"}]}]}}
        path = self.root / "second.json"
        path.write_text(json.dumps(current))
        viewer = Viewing(iter([self.path, path]), sound, words, None)
        viewer.advance(1, False)
        for _ in range(2):
            info = json.loads(viewer.advance(2, False, image_hearing=True)[0].text)
            self.assertEqual(info["sound_descriptions"], sound.read.return_value)
            self.assertEqual(info["sound_in_context"], earlier)
            self.assertEqual(info["joint"], current["joint"])
        sound.read.assert_called_once_with((self.root / "sound.wav").resolve(), 1, 2)

    def test_image_client_silence_and_already_completed_reads(self):
        sound, words = Mock(), Mock()
        self.turn.update(speech={"status": "available", "windows": []},
                         joint={"status": "available", "windows": [{"start_s": 0, "end_s": 1,
                                                                    "channels": [{"status": "read"}]}]})
        self.path.write_text(json.dumps(self.turn))
        view = Viewing(iter([self.path]), sound, words, None)
        info = json.loads(view.advance(1, False, image_hearing=True)[0].text)
        self.assertNotIn("sound_descriptions", info)
        self.assertEqual(info["joint"], self.turn["joint"])
        self.turn["audio"]["signal"] = "digital_silence"
        del self.turn["speech"], self.turn["joint"]
        self.path.write_text(json.dumps(self.turn))
        info = json.loads(view.advance(1, False, image_hearing=True)[0].text)
        for key in ("sound_descriptions", "speech"):
            self.assertEqual(info[key], {"status": "digital_silence", "windows": []})
        sound.read.assert_not_called()
        words.inspect.assert_not_called()

    def test_image_client_uses_complete_recording_for_sound_after_the_picture_ends(self):
        sound, words = Mock(), Mock()
        recording = {"status": "available", "windows": [{"start_s": 0, "end_s": 1,
                                                          "channels": [{"channels": [1], "impression": "continuous sound"}]}]}
        joint = {"status": "no_simultaneous_picture", "windows": [], "recording": recording}
        self.turn.update(speech={"status": "available"}, joint=joint, pages=[])
        self.path.write_text(json.dumps(self.turn))
        view = Viewing(iter([self.path]), sound, words, None)
        info = json.loads(view.advance(1, False, image_hearing=True, full_detail=False)[0].text)
        self.assertEqual(info["joint"]["recording"], recording)
        self.assertNotIn("sound_descriptions", info)
        sound.read.assert_not_called()
        words.inspect.assert_not_called()

    def test_image_client_stdio_defaults_and_per_call_choice_when_helpers_are_missing(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        src = self.root / "clip.wav"
        rate = 8000
        samples = np.arange(rate, dtype=np.int16)
        sf.write(src, samples, rate)

        async def check(preset):
            params = StdioServerParameters(command=sys.executable, args=[
                "-m", "reel.cli", "mcp", str(src), "--until", ".5", *(["--image-client"] if preset else [])],
                cwd=Path(__file__).resolve().parents[1], env={
                    "REEL_HOME": str(self.root / "empty"), "REEL_ASR_PYTHON": "/usr/bin/false",
                    "REEL_HEAR_PYTHON": "/usr/bin/false"})
            async with stdio_client(params) as (r, w), ClientSession(r, w) as client:
                await client.initialize()
                args = {"turn": 1} if preset else {"turn": 1, "native_audio": False}
                result = await client.call_tool("view", args)
                self.assertFalse(result.isError)
                self.assertEqual([b.type for b in result.content], ["text", "image"])
                report = json.loads(result.content[0].text)
                self.assertEqual(report["audio"]["signal"], "nonzero")
                self.assertFalse(report["audio"]["wav_attached"])
                self.assertIn("detail_on_replay", report)
                replay = await client.call_tool("view", {**args, "full_detail": True})
                self.assertNotIn("detail_on_replay", json.loads(replay.content[0].text))
                self.assertEqual(result.content[1:], replay.content[1:])
                for key in ("speech", "sound_descriptions"):
                    self.assertEqual(report[key]["status"], "unavailable")
                with_wav = await client.call_tool("view", {"turn": 1, **({"native_audio": True} if preset else {})})
                self.assertFalse(with_wav.isError)
                attached = json.loads(with_wav.content[0].text)
                self.assertEqual([b.type for b in with_wav.content], ["text", "image", "audio"])
                self.assertTrue(attached["audio"]["wav_attached"])
                for key in ("speech", "sound_descriptions"):
                    self.assertEqual(attached[key], report[key])
                result = await client.call_tool("view", {"turn": 1, "native_audio": True, "sound_estimates": False})
                self.assertEqual(with_wav.content[1:], result.content[1:])
                report = json.loads(result.content[0].text)
                self.assertNotIn("speech", report)
                self.assertNotIn("sound_descriptions", report)
                pcm, sr = sf.read(io.BytesIO(base64.b64decode(result.content[-1].data)), dtype="int16")
                self.assertEqual(sr, rate)
                np.testing.assert_array_equal(pcm, samples[:rate//2])

        for preset in (False, True):
            asyncio.run(check(preset))

    def test_decode_failure_cannot_become_a_false_end_or_disclose_the_source(self):
        def broken():
            yield self.path
            raise OSError("private future label")
        reader = Viewing(broken(), None, None, None)
        first = reader.advance(1, False)
        for _ in range(2):
            with self.assertRaises(ValueError) as raised:
                reader.advance(2, False)
            self.assertNotIn("private", str(raised.exception))
        self.assertEqual(reader.advance(1, False), first)

    def test_one_off_listening_releases_its_model(self):
        reader = Viewing(iter([self.path]), None, None, None)
        reader.advance(1, False)
        with patch("reel.mcp_server.SoundReader") as factory:
            worker = factory.return_value
            worker.__enter__.return_value.read.return_value = {"status": "available"}
            reader.listen(1, None)
            worker.__exit__.assert_called_once_with(None, None, None)

    def test_sound_comparison_receives_only_released_original_samples(self):
        rate = 8000
        ramp = np.arange(rate, dtype=np.int16)
        stereo = np.column_stack((ramp, -ramp))
        for i in range(2):
            directory = self.root / f"turn_{i + 1:05}"
            directory.mkdir()
            wav = directory / "sound.wav"
            sf.write(wav, stereo[i * 4000:(i + 1) * 4000], rate)
            packet = {**self.turn, "pages": [], "current_frame": None, "start_s": i / 2, "end_s": (i + 1) / 2,
                      "audio": {**self.turn["audio"], "channels": 2, "first_sample_on_film": i * 4000,
                                "sample_count": 4000}}
            (directory / "page.png").write_bytes((self.root / "page.png").read_bytes())
            (directory / "view.json").write_text(json.dumps(packet))
        reader = Viewing(iter(sorted(self.root.glob("turn_*/view.json"))), None, None, None)
        reader.advance(1, False)
        reader.advance(2, False)
        attempts = []

        def compare(wav, start, end, descriptions):
            samples, sr = sf.read(wav, dtype="int16")
            expected = stereo[round(start * rate):round(end * rate)]
            self.assertEqual(sr, rate)
            np.testing.assert_array_equal(samples, expected)
            attempts.append((start, end, descriptions))
            return {"status": "unavailable", "reason": "model offline"}

        choices = ["steady hum", "short pulses"]
        with patch("reel.mcp_server.SoundMatcher") as factory:
            worker = factory.return_value
            worker.__enter__.return_value.read.side_effect = compare
            with self.assertRaises(ValueError):
                reader.listen(1, None, .25, .75, alternatives=choices)
            plain = reader.listen(2, None, .25, .75, describe=False)
            self.assertNotIn("original_sound_match", json.loads(plain[0].text))
            factory.assert_not_called()
            reply = reader.listen(2, None, .25, .75, True, False, "percussive", choices)
            self.assertEqual([block.type for block in reply], ["text", "image", "audio"])
            self.assertEqual(json.loads(reply[0].text)["original_sound_match"]["status"], "unavailable")
            reader.listen(2, None, end_s=.25, describe=False, alternatives=[])
            self.assertEqual(attempts, [(.25, .75, choices), (0, .25, [])])
            self.assertEqual(worker.__exit__.call_count, 2)

    def test_replay_reports_actual_sample_count_at_a_rounded_track_end(self):
        from reel.action_view import action_views

        rate, count = 44100, 8000
        self.turn["end_s"] = count / rate
        self.turn.update(pages=[], current_frame=None)
        self.turn["audio"].update(rate_hz=rate, first_sample_on_film=0, sample_count=count)
        sf.write(self.root / "sound.wav", np.arange(count, dtype=np.int16), rate)
        self.path.write_text(json.dumps(self.turn))
        reader = Viewing(iter([self.path]), None, None, None, source=self.root / "sound.wav")
        reader.advance(1, False)
        for start, first in ((0, 0), (.1, 4410)):
            meta, _, recording = reader.listen(1, None, start, self.turn["end_s"], True, False)
            info = json.loads(meta.text)
            data, _ = sf.read(io.BytesIO(base64.b64decode(recording.data)), dtype="int16")
            self.assertEqual(info["first_sample_on_film"], first)
            self.assertEqual(info["samples_per_channel"], count - first)
            np.testing.assert_array_equal(data, np.arange(first, count, dtype=np.int16))
            observed = reader.observe(start, self.turn["end_s"], None, True)
            report = json.loads(observed[0].text)
            self.assertEqual(report["moments"][-1]["wav_samples"], [0, count - first])
            self.assertEqual(observed[-1], recording)
        pages = action_views(self.root, [], (8, 8), 0, self.turn["end_s"], 0, self.root / "sound.wav", 0)
        self.assertEqual(pages["pages"][0]["end_s"], self.turn["end_s"])

    def test_observation_reassembles_two_turns_and_continues_with_the_next_frame(self):
        from reel.play import play

        film = self.root / "source.mkv"
        wav = self.root / "ramp.wav"
        ramp = np.arange(8000, dtype=np.int16)
        samples = np.column_stack((ramp, -ramp))
        sf.write(wav, samples, 8000)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=80x40:r=40:d=1",
                        "-i", str(wav), "-vf", "drawbox=x=40:w=40:h=40:color=red:t=fill", "-c:v", "ffv1",
                        "-c:a", "pcm_s16le", str(film)], check=True)
        with closing(play(film, self.root / "watched", step=.4, until=.8)) as playback:
            reader = Viewing(playback, None, None, None, source=film)
            for point in ([-.1, .5], [.5, 1.01], []):
                with self.assertRaises(ValueError):
                    reader.advance(1, False, focus=point)
            self.assertEqual(reader.paths, [])
            original = reader.advance(1, True)
            for x, expected_channel in ((0, 2), (1, 0)):
                focused = reader.advance(1, True, focus=[x, .5])
                meta = json.loads(focused[0].text)
                index = next(i for i, desc in enumerate(meta["images_in_order"]) if "reader_focus" in desc)
                native = next(i for i, desc in enumerate(meta["images_in_order"]) if "focused_picture_pts_us" in desc)
                self.assertEqual([b for i, b in enumerate(focused[1:]) if i not in (index, native)], original[1:])
                self.assertEqual(meta["focus"]["status"], "available")
                self.assertEqual(meta["focus"]["sound"], "recorded channels of the whole scene")
                self.assertEqual(meta["focus"]["point"], [x, .5])
                self.assertTrue(all(0 <= pts < 400000 for pts in meta["images_in_order"][index]["pts_us"]))
                with Image.open(io.BytesIO(base64.b64decode(focused[index + 1].data))) as strip:
                    self.assertGreater(strip.convert("RGB").getpixel((173, 60))[expected_channel], 220)
                    colours = np.asarray(strip)
                    self.assertTrue(np.any(np.all(colours == [119, 204, 242], axis=2)))
                loc = meta["images_in_order"][native]
                with Image.open(io.BytesIO(base64.b64decode(focused[native + 1].data))) as pair:
                    with Image.open(reader.paths[0].parent / "now.png") as still:
                        np.testing.assert_array_equal(np.asarray(pair.crop(loc["detail_in_image"])),
                                                      np.asarray(still.crop(loc["pixel_box"])))
                past, crop = reader.look(1, [0, 0, .5, 1], at_s=.039)
                self.assertEqual(json.loads(past.text)["pts_us"], 25000)
                with Image.open(io.BytesIO(base64.b64decode(crop.data))) as picture:
                    self.assertEqual(picture.size, (40, 40))
            with self.assertRaises(ValueError):
                reader.observe(.304, .79, None, True)
            reader.advance(2, False)
            with self.assertRaises(ValueError):
                reader.look(1, [0, 0, 1, 1], at_s=.4)
            with self.assertRaises(ValueError):
                reader.listen(1, None, .3, .41, True, False)
            with patch("reel.mcp_server.SoundReader") as helper:
                replay = reader.listen(2, None, .300031, .769933, True, False)
                helper.assert_not_called()
            self.assertEqual([b.type for b in replay], ["text", "image", "audio"])
            metadata = json.loads(replay[0].text)
            self.assertIsNone(metadata["next_start_s"])
            left, right = 2401, 6160
            self.assertEqual((metadata["first_sample_on_film"], metadata["samples_per_channel"]),
                             (left, right - left))
            data, rate = sf.read(io.BytesIO(base64.b64decode(replay[-1].data)), dtype="int16")
            self.assertEqual(rate, 8000)
            np.testing.assert_array_equal(data, samples[left:right])
            earlier = reader.listen(2, None, end_s=.25, native_audio=True, describe=False)
            self.assertEqual(json.loads(earlier[0].text)["start_s"], 0)
            with patch("reel.mcp_server.WINDOW_S", .2):
                part = reader.listen(2, None, .25, .8, False, False)
                self.assertEqual(json.loads(part[0].text)["next_start_s"], .45)
            with patch("reel.mcp_server.MAX_INLINE_PCM_BYTES", 8100):
                chunks = []
                cursor = .300031
                while cursor is not None:
                    info, _, recording = reader.listen(2, None, cursor, .769933, True, False)
                    details = json.loads(info.text)
                    self.assertEqual(details["first_sample_on_film"], left + sum(map(len, chunks)))
                    pcm, _ = sf.read(io.BytesIO(base64.b64decode(recording.data)), dtype="int16")
                    self.assertEqual(len(pcm), details["samples_per_channel"])
                    chunks.append(pcm)
                    cursor = details["next_start_s"]
                self.assertGreater(len(chunks), 1)
                np.testing.assert_array_equal(np.concatenate(chunks), samples[left:right])
            first = reader.observe(.304, .79, [.5, 0, 1, 1], True)
            second = reader.observe(.6, .79, [.5, 0, 1, 1], True)
            self.assertEqual(len(reader.paths), 2)
        meta, tail = [json.loads(part[0].text) for part in (first, second)]
        self.assertEqual((meta["end_s"], meta["next_start_s"], tail["next_start_s"]), (.6, .6, None))
        self.assertEqual([r["picture"]["pts_us"] for m in (meta, tail) for r in m["moments"]],
                         list(range(300000, 790000, 25000)))
        for result, info in [(first, meta), (second, tail)]:
            self.assertNotIn(str(self.root), result[0].text)
            self.assertEqual([b.type for b in result], ["text", "image", "image", "image", "audio"])
            with Image.open(io.BytesIO(base64.b64decode(result[1].data))) as sheet:
                red, _, blue = sheet.getpixel((32, 32))
                self.assertGreater(red, 240)
                self.assertLess(blue, 10)
            pcm, rate = sf.read(io.BytesIO(base64.b64decode(result[-1].data)), dtype="int16")
            origin = info["audio"]["first_sample_on_film"]
            np.testing.assert_array_equal(pcm, samples[origin:round(info["end_s"] * rate)])
            self.assertEqual(info["moments"][0]["wav_samples"][0], 0)
            self.assertEqual(info["moments"][-1]["wav_samples"][1], len(pcm))

    def test_observe_recording_only_and_silent_picture_only(self):
        from reel.play import play

        wav = self.root / "audio.wav"
        sf.write(wav, np.zeros(8000, np.int16), 8000)
        film = self.root / "video.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=48x32:r=2:d=1",
                        "-c:v", "ffv1", str(film)], check=True)
        for source in (wav, film):
            with self.subTest(source=source.name), closing(play(source, self.root / source.stem)) as playback:
                reader = Viewing(playback, None, None, None, source=source)
                first = reader.advance(1, False, focus=[.1, .1])
                target = json.loads(first[0].text)["focus"]
                self.assertEqual(target["status"], "no_picture" if source == wav else "available")
                result = reader.observe(.1, .21, None, False)
                report = json.loads(result[0].text)
                if source == wav:
                    self.assertIsNone(report["moments"][0]["picture"])
                    self.assertEqual(report["moments"][0]["wav_samples"], [0, 880])
                    self.assertEqual(report["images_in_order"], ["recorded channel spectrum"])
                else:
                    self.assertIsNone(report["audio"])
                    self.assertEqual(report["moments"][0]["picture"]["pts_us"], 0)
                    self.assertEqual(len(report["images_in_order"]), 2)
                    scene = reader.ask_scene(.1, .21, "What moved?", native_audio=True)
                    self.assertEqual(json.loads(scene[0].text)["joint"]["status"], "no_audio")
                    self.assertEqual([b.type for b in scene], ["text", "image"])
                self.assertEqual([b.type for b in result[1:]], ["image"] * len(report["images_in_order"]))

    def test_real_stdio_client_receives_bounded_image_and_audio_blocks(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        wav = self.root / "private-recording.wav"
        samples = np.zeros((8000, 2), dtype=np.int16)
        samples[4000:] = [1213, -1213]
        sf.write(wav, samples, 8000)
        film = self.root / "private-film.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=48x32:r=4:d=1",
                        "-i", str(wav), "-vf", "drawbox=c=red:t=fill:enable='gte(t,0.5)'",
                        "-c:v", "ffv1", "-c:a", "pcm_s16le", str(film)], check=True)

        async def receive():
            command = StdioServerParameters(command=sys.executable, args=["-m", "reel.cli", "mcp",
                                            str(film), "--until", ".5", "--gaze"], cwd=Path(__file__).resolve().parents[1],
                                            env={"REEL_HOME": str(self.root / "empty")})
            async with stdio_client(command) as (r, w), ClientSession(r, w) as client:
                await client.initialize()
                ahead = await client.call_tool("view", {"turn": 2})
                self.assertTrue(ahead.isError)
                view = await client.call_tool("view", {"turn": 1})
                self.assertFalse(view.isError)
                info = json.loads(view.content[0].text)
                self.assertEqual(info["gaze"]["status"], "unavailable")
                self.assertNotIn(film.name, view.content[0].text)
                self.assertEqual((info["start_s"], info["end_s"]), (0, .5))
                self.assertEqual([b.type for b in view.content], ["text", "image", "image", "image", "audio"])
                with Image.open(io.BytesIO(base64.b64decode(view.content[2].data))) as still:
                    red, _, blue = still.convert("RGB").getpixel((24, 16))
                    self.assertLess(red, 10)
                    self.assertGreater(blue, 200)
                pcm, rate = sf.read(io.BytesIO(base64.b64decode(view.content[-1].data)), dtype="int16")
                self.assertEqual(rate, 8000)
                np.testing.assert_array_equal(pcm, samples[:4000])
                sheet = await client.call_tool("frames", {"turn": 1, "page": 1})
                self.assertEqual(json.loads(sheet.content[0].text)["picture_pts_us"], [0, 250000])
                request = {"start_s": .13, "end_s": .5, "native_audio": False}
                bounded = await client.call_tool("observe", request)
                self.assertFalse(bounded.isError)
                detail = json.loads(bounded.content[0].text)
                action, = (v for v in detail["images_in_order"] if "action_over_time" in v)
                observations = [pic for row in action["rows"] for pic in row["pictures"]]
                self.assertEqual(observations, sorted(observations, key=lambda pic: pic["at_s"]))
                self.assertEqual({pic["pts_us"] for pic in observations}, {0, 250000})
                self.assertEqual([b.type for b in bounded.content], ["text", "image", "image", "image"])
                moments = json.loads(bounded.content[0].text)["moments"]
                self.assertEqual([r["wav_samples"] for r in moments], [[0, 960], [960, 2960]])
                future = await client.call_tool("observe", {**request, "end_s": .50001})
                self.assertTrue(future.isError)
                end = await client.call_tool("view", {"turn": 2})
                self.assertEqual(json.loads(end.content[0].text)["status"], "end_of_view")

        asyncio.run(receive())

    def test_live_reader_can_set_first_and_later_pauses_and_replay_them(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        source = self.root / "clock.wav"
        data = np.arange(12000, dtype=np.int16)
        sf.write(source, data, 8000)

        async def receive():
            command = StdioServerParameters(command=sys.executable, args=["-m", "reel.cli", "mcp", str(source),
                                                                          "--until", "1.31"],
                                            env={"REEL_HOME": str(self.root / "empty")})
            async with stdio_client(command) as (r, w), ClientSession(r, w) as client:
                await client.initialize()
                for invalid in (-1, 0, 3):
                    self.assertTrue((await client.call_tool("view", {"turn": 1, "max_seconds": invalid})).isError)
                raw = []
                # The third turn uses the normal limit again. It may stop early if sound changes.
                for turn, length, bounds in ((1, .125, (0., .125)), (2, .23, (.125, .355)), (3, None, None)):
                    args = {"turn": turn, "sound_estimates": False}
                    if length is not None:
                        args["max_seconds"] = length
                    result = await client.call_tool("view", args)
                    self.assertFalse(result.isError)
                    meta = json.loads(result.content[0].text)
                    if bounds:
                        self.assertEqual((meta["start_s"], meta["end_s"]), bounds)
                    else:
                        self.assertEqual(meta["start_s"], .355)
                        self.assertGreater(meta["end_s"] - meta["start_s"], .23)
                    pcm, _ = sf.read(io.BytesIO(base64.b64decode(result.content[-1].data)), dtype="int16")
                    raw.append(pcm)
                    replay = await client.call_tool("view", {**args, "max_seconds": .00001})
                    self.assertEqual(result.content, replay.content)
                np.testing.assert_array_equal(np.concatenate(raw), data[:sum(map(len, raw))])

        asyncio.run(receive())

    def test_continuous_view_preserves_changes_and_returns_to_adaptive_playback(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        rate = 8000
        samples = np.zeros(rate * 5, dtype=np.int16)
        samples[rate // 2:rate] = 8000
        samples[3 * rate:] = 12000
        source = self.root / "mix.wav"
        sf.write(source, samples, rate)

        async def receive():
            params = StdioServerParameters(command=sys.executable, args=["-m", "reel.cli", "mcp",
                str(source), "--until", "4.125"], env={"REEL_HOME": str(self.root / "empty")})
            async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
                await client.initialize()
                for invalid in ({"continuous_seconds": 0}, {"continuous_seconds": 9},
                                {"continuous_seconds": 1, "max_seconds": 1}):
                    self.assertTrue((await client.call_tool("view", {"turn": 1, **invalid})).isError)
                recordings = []
                for turn, pace, stop in ((1, {"continuous_seconds": 2.5}, 2.5),
                                         (2, {}, 3.1), (3, {"continuous_seconds": 8}, 4.125)):
                    result = await client.call_tool("view", {"turn": turn, "sound_estimates": False, **pace})
                    self.assertFalse(result.isError)
                    report = json.loads(result.content[0].text)
                    self.assertEqual(report["end_s"], stop)
                    self.assertEqual(report["pause"]["reason"], "change" if turn == 2 else "interval")
                    if turn == 1:
                        self.assertIn("sound_level_change_s", report["pause"])
                        self.assertTrue(report["audio"]["band_changes"])
                    pcm, _ = sf.read(io.BytesIO(base64.b64decode(result.content[-1].data)), dtype="int16")
                    recordings.append(pcm)
                    replay = await client.call_tool("view", {"turn": turn, "sound_estimates": False,
                                                            "continuous_seconds": .1})
                    self.assertEqual(result.content, replay.content)
                np.testing.assert_array_equal(np.concatenate(recordings), samples[:33000])
                end = await client.call_tool("view", {"turn": 4, "continuous_seconds": 8})
                self.assertEqual(json.loads(end.content[0].text)["status"], "end_of_view")

        asyncio.run(receive())

    def test_frame_limited_first_view_follows_irregular_exposures_with_native_audio(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from reel.frames import cell_box

        ticks = [20, 55, 60, 185, 490, 494, 502]
        levels = [30, 210, 50, 170, 70, 130, 90]
        rate = 8000
        pcm = np.arange(6000, dtype=np.int16)
        stereo = np.stack((pcm, -pcm), axis=1)
        sf.write(self.root / "source.wav", stereo, rate)
        source = self.root / "clock.mkv"
        timestamps = "+".join(f"eq(N,{i})*{t}" for i, t in enumerate(ticks))
        subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "32x24",
                        "-framerate", "1000", "-i", "-", "-i", str(self.root / "source.wav"), "-vf",
                        f"setpts='{timestamps}'", "-fps_mode", "passthrough", "-enc_time_base:v", "1:1000",
                        "-c:v", "ffv1", "-c:a", "copy", str(source)],
                       input=b"".join(bytes([v]) * 32 * 24 * 3 for v in levels), check=True)

        async def receive():
            params = StdioServerParameters(command=sys.executable, args=["-m", "reel.cli", "mcp", str(source),
                "--until", ".70325"], env={"REEL_HOME": str(self.root / "empty")})
            turns = [
                ({"continuous_seconds": .04}, .04, None),
                ({"max_frames": 1}, .055, [20]),                # held when the reader slowed down
                ({"max_frames": 2}, .185, [55, 60]),           # 5 ms exposure
                ({"max_frames": 1, "continuous_seconds": .04}, .225, [185]),
                ({"max_frames": 2}, .494, [185, 490]),         # carry and one fresh exposure
                ({"max_frames": 12}, .70325, [494, 502]),      # sound continues after the picture ends
            ]
            async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
                await client.initialize()
                for n in (0, 13):
                    self.assertTrue((await client.call_tool("view", {"turn": 1, "max_frames": n})).isError)
                self.assertFalse((await client.list_resources()).resources)
                self.assertTrue((await client.call_tool("view_media", {"turn": 2})).isError)
                first_media = await client.call_tool("view_media", {"turn": 1, "continuous_seconds": .04})
                self.assertFalse(first_media.isError, first_media.content[0].text)
                samples = []
                for i, (pace, end, times) in enumerate(turns, 1):
                    args = {"turn": i, "sound_estimates": False, **pace}
                    result = await client.call_tool("view", args)
                    self.assertFalse(result.isError, result.content[0].text)
                    report = json.loads(result.content[0].text)
                    self.assertEqual(report["end_s"], end)
                    first = report["images_in_order"][0]
                    if times is None:
                        self.assertNotIn("successive_picture_pts_us", first)
                    else:
                        self.assertEqual(first["successive_picture_pts_us"], [t * 1000 for t in times])
                        with Image.open(io.BytesIO(base64.b64decode(result.content[1].data))) as img:
                            for j, t in enumerate(times):
                                l, top, r, b = cell_box(j, (320, 240))
                                self.assertEqual(img.getpixel(((l + r) // 2, (top + b) // 2)),
                                                 (levels[ticks.index(t)],) * 3)
                    if i in (2, 3, 5):
                        self.assertEqual(report["pause"]["reason"], "frame_limit")
                    if i == len(turns):
                        self.assertEqual(first["picture_end_s"], .503)
                    wav, hz = sf.read(io.BytesIO(base64.b64decode(result.content[-1].data)), dtype="int16")
                    self.assertEqual(hz, rate)
                    for row in report["audio"]["during_pictures"]:
                        pts = row["pts_us"]
                        first_sample = report["audio"]["first_sample_on_film"]
                        a, b = row["wav_samples"]
                        self.assertEqual(a, max(0, pts * rate // 1_000_000 - first_sample))
                        next_tick = next((t for t in ticks if t * 1000 > pts), 503)
                        self.assertEqual(b, min(len(wav), next_tick * 8 - first_sample))
                        peak = np.max(np.abs(wav[a:b].astype(float)), axis=0) / 32768
                        self.assertEqual(row["peak_dbfs"], list(np.round(20 * np.log10(peak), 2)))
                    native = await client.call_tool("view_media", {"turn": i})
                    self.assertFalse(native.isError, native.content[0].text)
                    clock = json.loads(native.content[0].text)
                    self.assertEqual((clock["start_s"], clock["end_s"]), (report["start_s"], end))
                    link, = native.content[1:]
                    self.assertEqual(link.mimeType, "video/quicktime")
                    self.assertNotIn(source.name, str(native.content))
                    if i == 1:
                        self.assertEqual(native.content, first_media.content)
                    resource, = (await client.read_resource(link.uri)).contents
                    mov = self.root / "received.mov"
                    mov.write_bytes(base64.b64decode(resource.blob))
                    recorded = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(mov), "-map", "0:a",
                                                        "-f", "s16le", "-"])
                    self.assertEqual(recorded, wav.astype("<i2").tobytes())
                    frame_pts = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v",
                        "-show_entries", "packet=pts", "-of", "csv=p=0", str(mov)], text=True).split()
                    start_us = round(report["start_s"] * 1e6)
                    self.assertEqual(list(map(int, frame_pts)), [max(0, t*1000 - start_us) for t in (times or [20])])
                    samples.append(wav)
                    replay = await client.call_tool("view", {"turn": i, "sound_estimates": False})
                    self.assertEqual(result.content, replay.content)
                np.testing.assert_array_equal(np.concatenate(samples), stereo[:5626])
                ended = await client.call_tool("view", {"turn": 7, "max_frames": 3})
                self.assertEqual(json.loads(ended.content[0].text)["status"], "end_of_view")

                source.unlink()
                repeat = await client.call_tool("view_media", {"turn": 1})
                self.assertEqual(repeat.content, first_media.content)
                self.assertEqual(json.loads((await client.call_tool("view_media", {"turn": 7})).content[0].text)
                                 ["status"], "end_of_view")

        asyncio.run(receive())

    def test_blind_presenter_keeps_audio_evidence_enabled_for_every_turn(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        source = self.root / "silence.wav"
        sf.write(source, np.zeros(72000, np.int16), 8000)
        script = Path(__file__).resolve().parents[1] / "examples" / "watch_first_impression.py"

        async def receive(image_client):
            params = StdioServerParameters(command=sys.executable, args=[str(script), "--present", "12",
                sys.executable, "-m", "reel.cli", "mcp", str(source), "--joint", *(["--image-client"] if image_client else [])],
                env={"REEL_HOME": str(self.root / "empty")})
            async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
                initialized = await client.initialize()
                self.assertIn("indirect observations", initialized.instructions)
                tool, = (await client.list_tools()).tools
                self.assertEqual(set(tool.inputSchema["properties"]), {"turn"})
                for turn, end in [(1, 8.), (2, 9.)]:
                    result = await client.call_tool("view", {"turn": turn})
                    self.assertFalse(result.isError, result.content[0].text)
                    report = json.loads(result.content[0].text)
                    self.assertEqual(report["end_s"], end)
                    self.assertEqual(report["speech"]["status"], "digital_silence")
                    self.assertEqual(report["audio"]["signal"], "digital_silence")
                    self.assertEqual(report["audio"]["wav_attached"], not image_client)
                    self.assertEqual("reading_basis" in report["audio"], not image_client)
                    self.assertEqual(any(b.type == "audio" for b in result.content), not image_client)
                    self.assertIn("joint", report)
                    self.assertTrue(any(b.type == "image" for b in result.content))

        for image_client in (True, False):
            asyncio.run(receive(image_client))
