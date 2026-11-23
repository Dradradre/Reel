import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from reel.attention import fixation_box, focus_still, inspect_turn
from reel.play import play


class AttentionTests(unittest.TestCase):
    def test_fixation_guides_point_at_unmarked_pixels_in_clamped_crop(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = Image.fromarray(np.random.default_rng(1).integers(0, 256, (48, 100, 3), dtype=np.uint8))
            path, output = Path(tmp) / "now.png", Path(tmp) / "focus.png"
            frame.save(path)
            for point, pixel in (([0, 1], (0, 47)), ([.97, .88], (97, 42)), ([1, 0], (99, 0))):
                result = focus_still(path, point, fixation_box(point), output)
                x, y = result["point_in_detail"]
                left, top, _, _ = result["detail_in_image"]
                with Image.open(output) as image:
                    self.assertEqual(image.crop(result["detail_in_image"]).tobytes(),
                                     frame.crop(result["pixel_box"]).tobytes())
                    self.assertEqual(image.getpixel((left + x, top + y)), frame.getpixel(pixel))
                    self.assertEqual(image.getpixel((left + x, top - 1)), (255, 195, 44))
                    self.assertEqual(image.getpixel((left - 1, top + y)), (255, 195, 44))

    def test_stopping_legacy_frame_stream_reaps_its_decoder(self):
        from reel.common import read_frames

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "film.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=96x64:r=20:d=3",
                            "-c:v", "ffv1", str(source)], check=True)
            children = []
            popen = subprocess.Popen
            def capture(*args, **kwargs):
                children.append(popen(*args, **kwargs))
                return children[-1]
            with patch("reel.common.subprocess.Popen", side_effect=capture):
                frames = read_frames(source, 96, 64, 20)
                self.assertEqual(next(frames).shape, (64, 96, 3))
                frames.close()
                self.assertIsNotNone(children[0].returncode)
                self.assertTrue(children[0].stdout.closed)

    def test_look_recovers_native_pixels_from_published_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            turn = Path(tmp)
            frame = Image.new("RGB", (11, 7))
            frame.putdata([(x, y, x + y) for y in range(7) for x in range(11)])
            frame.save(turn / "now.png")
            view = {"schema": "reel.play.v1", "current_frame": {"file": "now.png", "pts_us": 123456}}
            (turn / "view.json").write_text(json.dumps(view))
            first = inspect_turn(turn, [.21, .31, 1, .6])
            self.assertEqual(first["source_size"], [11, 7])
            self.assertEqual(first["pixel_box"], [2, 2, 11, 5])
            self.assertEqual(first["pts_us"], 123456)
            with Image.open(first["file"]) as crop:
                self.assertEqual(crop.tobytes(), frame.crop((2, 2, 11, 5)).tobytes())
            self.assertEqual(first, inspect_turn(turn, [.21, .31, 1, .6]))
            for box in ([0, 0, 0, 1], [0, -.1, .5, 1], [0, 0, float("nan"), .5]):
                with self.subTest(box=box), self.assertRaises(ValueError):
                    inspect_turn(turn, box)
            (turn / "view.json").write_text(json.dumps({**view, "current_frame": None}))
            with self.assertRaisesRegex(ValueError, "no current picture"):
                inspect_turn(turn, [0, 0, 1, 1])

    def test_optional_helper_failure_leaves_picture_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "film.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=32x24:r=5:d=0.4",
                            "-c:v", "ffv1", str(source)], check=True)
            with patch("reel.play.GAZE_PY", str(root / "missing")):
                paths = list(play(source, root / "session", gaze=True))
            packet = json.loads(paths[-1].read_text())
            self.assertEqual(packet["gaze"]["status"], "unavailable")
            detail = inspect_turn(paths[-1].parent, [0, 0, .5, 1])
            self.assertEqual(detail["pixel_box"], [0, 0, 16, 24])

    def test_reader_can_look_twice_before_requesting_more_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "film.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=48x32:r=5:d=1",
                            "-c:v", "ffv1", str(source)], check=True)
            command = [sys.executable, "-m", "reel.cli", "play", str(source), "--out", str(root / "view"),
                       "--until", ".75", "--step", ".4"]
            process = subprocess.run(command, input="look 0 0 .5 .5\nlook -1 0 1 1\nlook .5 .5 1 1\nnext\nquit\n",
                                     text=True, capture_output=True, check=True)
            first, left, error, right, second = process.stdout.splitlines()
            self.assertIn("error", json.loads(error))
            first, left, right, second = map(Path, (first, left, right, second))
            self.assertEqual(first.parent, left.parent)
            self.assertEqual(first.parent, right.parent)
            self.assertNotEqual(left, right)
            self.assertEqual(json.loads(first.read_text())["end_s"], .4)
            self.assertEqual(json.loads(second.read_text())["end_s"], .75)

    def test_look_back_reads_native_pixels_only_after_the_picture_was_delivered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "five.mkv"
            pixels = np.random.default_rng(320).integers(0, 256, (5, 60, 512, 3), dtype=np.uint8)
            subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "512x60",
                            "-framerate", "5", "-i", "-", "-c:v", "ffv1", "-pix_fmt", "bgr0", str(source)],
                           input=pixels.tobytes(), check=True)
            result = subprocess.run([sys.executable, "-m", "reel.cli", "play", str(source), "--out", str(root / "play"),
                                     "--step", ".4", "--until", ".8"], text=True, capture_output=True, check=True,
                                    input="look --at .4 0 0 1 1\nnext\nlook --at .012 .25 0 .75 .5\n"
                                          "look --at .38 0 0 1 1\nlook --at .8 0 0 1 1\nquit\n")
            first, future, second, before, held, cutoff = result.stdout.splitlines()
            self.assertEqual(json.loads(Path(first).read_text())["end_s"], .4)
            self.assertEqual(json.loads(Path(second).read_text())["end_s"], .8)
            self.assertIn("error", json.loads(future))
            self.assertIn("error", json.loads(cutoff))
            self.assertEqual(Path(held).parent, Path(second).parent)
            self.assertFalse(list(Path(first).parent.glob("*detail*.png")))
            with Image.open(before) as crop, Image.open(held) as full:
                np.testing.assert_array_equal(crop, pixels[0, :30, 128:384])
                np.testing.assert_array_equal(full, pixels[1])

    def test_gaze_samples_keep_the_film_clock_across_pauses_and_blank_pictures(self):
        try:
            from reel.gaze import PlaybackGaze
        except ImportError:
            self.skipTest("optional gaze environment is missing")
        blank = Image.new("RGB", (2, 2))
        image = blank.copy()
        image.putpixel((0, 0), (255, 0, 0))
        def picture(us):
            return (0, blank) if us < 200000 else (200000, image)
        def read(stops):
            reader = PlaybackGaze.__new__(PlaybackGaze)
            reader.last_pts = reader.last_map = None
            def infer(batch, commit):
                maps = np.tile([[0., 1.]], (len(batch), 1, 1))
                if commit:
                    reader.last_map = maps[-1]
                return maps
            reader.infer = infer
            start = 0
            samples = []
            for end in stops:
                current = {"pts_us": picture(int(end * 1e6) - 1)[0]}
                samples.extend(reader.read(start, end, current, (2, 2), [])["samples"])
                start = end
            return samples
        with patch("sound_glimpses.sheet_picture", return_value=picture):
            continuous = read([.51])
            self.assertEqual(continuous, read([.12, .27, .34, .51]))
        self.assertEqual([s["tick"] for s in continuous], [0, 1, 2, 3])
        self.assertEqual([s["pts_us"] for s in continuous], [0, 0, 200000, 200000])
        self.assertEqual([s["point"] for s in continuous], [None, None, [.75, .5], [.75, .5]])

    def test_failed_helper_exits_while_the_viewing_is_still_open(self):
        from reel.local_reader import LocalReader

        script = """import json, os, sys
for line in sys.stdin:
    print(json.dumps({"status": "unavailable", "reason": str(os.getpid()), "startup_failure": True}), flush=True)
"""
        with LocalReader([sys.executable, "-c", script], "test") as reader:
            failed = reader.request({})
            self.assertEqual(failed["status"], "unavailable")
            with self.assertRaises(ProcessLookupError):
                os.kill(int(failed["reason"]), 0)
            self.assertEqual(reader.request({}), failed)

    def test_recent_strip_keeps_earlier_ticks_and_stops_when_picture_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "held.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=96x60:r=6:d=1",
                            "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=2", "-c:v", "ffv1", "-c:a", "pcm_s16le",
                            str(src)], check=True)
            def predict(job):
                pts = [p for page in job["pages"] for p in page["pts_us"]]
                samples = []
                for tick in range(12):
                    if job["start_s"] <= tick / 6 < job["end_s"]:
                        s = max(p for p in pts if p <= tick * 1_000_000 // 6)
                        samples.append({"tick": tick, "pts_us": s, "point": [.5, .5]})
                return {"status": "available", "samples": samples}
            from reel.local_reader import LocalReader
            def make_reader(command, model):
                reader = LocalReader(command, model)
                if model == "UNISAL/DHF1K":
                    reader.request = predict
                return reader
            with patch("reel.play.LocalReader", side_effect=make_reader):
                paths = list(play(src, root / "view", gaze=True, step=.65))
            second = json.loads(paths[1].read_text())
            self.assertIsNone(second["current_frame"])
            strip = second["gaze"]["recent_details"]
            self.assertEqual([s["tick"] for s in strip["samples"]], [2, 5])
            with Image.open(paths[1].parent / strip["file"]) as im:
                self.assertEqual(im.size, (640, 220))
                for x in (160, 480):
                    self.assertGreater(im.getpixel((x, 100))[0], 240)
            self.assertNotIn("gaze", json.loads(paths[-1].read_text()))

    def test_off_clock_inspection_preserves_recurrent_state(self):
        try:
            import torch
            from reel.gaze import PlaybackGaze
        except ImportError:
            self.skipTest("optional gaze environment is missing")
        reader = PlaybackGaze.__new__(PlaybackGaze)
        reader.hidden = ["after first tick"]
        reader.model = None
        reader.mean = 0
        reader.std = 1
        reader.last_map = None

        def advance(model, inputs, state):
            state[0] = "after another tick"
            return torch.zeros((1, 1, 1, 1)), state
        with patch("reel.gaze.forward", advance):
            image = Image.new("RGB", (20, 10))
            reader.infer([image], False)
            self.assertEqual(reader.hidden, ["after first tick"])
            self.assertIsNone(reader.last_map)
            reader.infer([image], True)
            self.assertEqual(reader.hidden, ["after another tick"])
