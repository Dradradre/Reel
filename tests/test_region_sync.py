import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from reel.frames import frame_sheets
from reel.region_sync import measure, published_timing, region_bounds, region_frames, regional_timing, released_region_frames, validate_regions
from reel.sound_glimpses import sheet_picture


class RegionTimingTests(unittest.TestCase):
    def test_region_uses_only_the_picture_on_display_and_holds_short_intervals(self):
        first = np.zeros((24, 48, 3), np.uint8)
        first[:, :24] = [191, 17, 7]
        first[:, 24:] = [4, 31, 227]
        with tempfile.TemporaryDirectory() as tmp:
            for n, value in enumerate((35, 220)):
                later = np.full_like(first, value)
                frames = np.stack([first, first, later, later])
                source = Path(tmp) / f"source{n}.mkv"
                subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "48x24",
                                "-r", "4", "-i", "-", "-c:v", "ffv1", str(source)],
                               input=frames.tobytes(), check=True)
                for box, expected in [((0, 0, .5, 1), first[0, 0]), ((.5, 0, 1, 1), first[0, -1])]:
                    visible = region_frames(source, .26, .5, box, 25, 125, 32)
                    self.assertEqual(visible.shape, (125, 32, 32, 3))
                    self.assertTrue(np.all(visible == expected))

    def test_full_region_edges_on_the_raw_and_published_picture_clocks(self):
        # A 2:1 sample aspect makes this 48x32 source display as 96x32.
        # The only motion in the selected rectangle is at its far right, beginning at 60 ms.
        frames = np.zeros((8, 32, 48, 3), np.uint8)
        frames[:, :8, :, 2] = 255  # Outside the selection.
        frames[:, 8:24, :4, 0] = 255
        frames[3:, 8:24, 44:, 1] = 255
        region = (0, .25, 1, .75)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wide.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "48x32",
                            "-r", "50", "-i", "-", "-vf", "setsar=2/1", "-c:v", "ffv1", str(source)],
                           input=frames.tobytes(), check=True)
            raw = region_frames(source, .012, .113, region, 25, 5, 120)
            display = np.repeat(frames, 2, axis=2)
            pages = frame_sheets(root, 96, 32, [(20000 * j, 20000, im.tobytes()) for j, im in enumerate(display)])
            bounds = region_bounds(region, (96, 32))
            self.assertEqual(bounds, (0, 8, 96, 24))
            published = released_region_frames(sheet_picture(pages, (96, 32)), .012, .113, bounds, 25, 5, 120)
        for shown in (raw, published):
            # The 6:1 region fits in 120x20. The remaining pixels on the square encoder input are black.
            self.assertTrue(np.all(shown[:, :50] == 0))
            self.assertTrue(np.all(shown[:, 70:] == 0))
            self.assertTrue(np.all(shown[:, 50:70, 0, 0] == 255))
            self.assertEqual(shown[:, 60, -1, 1].tolist(), [0, 0, 255, 255, 255])
            self.assertTrue(np.all(shown[..., 2] == 0))

    def test_sound_in_a_still_scene_does_not_name_a_source(self):
        rate = 16000
        signal = np.tile([.1, .1, 0], (rate, 1))
        frame = np.zeros((125, 4, 4, 3), np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "sound.wav"
            sf.write(wav, signal, rate)
            with patch("reel.region_sync.region_frames", return_value=frame), patch("reel.sync.load_model") as load:
                result = measure("film", wav, 8, 8, 9, [(0, 0, 1, 1)])["windows"][0]
            load.assert_not_called()
            self.assertEqual(result["static_region_indices"], [0])
            self.assertEqual(result["channels"], [
                {"channels": [1, 2], "digital_silence": False, "near_zero_mass": [None]},
                {"channels": [3], "digital_silence": True},
            ])

    def test_released_encoder_grid_uses_original_cells_and_holds_variable_rate_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            size = (12, 8)
            frames = [(pts, 0, np.full((8, 12, 3), value, np.uint8).tobytes())
                      for pts, value in [(0, 2), (120000, 30), (155001, 60), (254000, 100), (260000, 200)]]
            page, = frame_sheets(root, *size, frames)
            page.update(file=str(root / page["file"]), pts_us=[120000, 155001, 254000], first_cell=1)
            picture = sheet_picture([page], size)
            clip = released_region_frames(picture, .155, .26, (0, 0, 8, 8), 25, 6, 4)
            self.assertEqual(clip[:, 0, 0, 0].tolist(), [30, 60, 60, 60, 60, 60])
            clip = released_region_frames(picture, .254, .26, (0, 0, 8, 8), 25, 3, 4)
            self.assertTrue(np.all(clip == 100))

    def test_long_comparisons_cover_real_media_without_padding(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "silence.wav"
            sf.write(wav, np.zeros(8 * 16000), 16000)
            result = measure("unused", wav, 2, 2, 10, [[0, 0, 1, 1]])
            self.assertEqual([(w["start_s"], w["end_s"], w["padded_s"]) for w in result["windows"]],
                             [(2, 7, 0), (5, 10, 0)])
            result = measure("unused", wav, 3.05, 3.05, 8.05, [[0, 0, 1, 1]])
            self.assertEqual(len(result["windows"]), 1)
        scene = {"start": 2, "end": 5, "picture_end": 4, "wav": "released.wav", "first_sample": 32000,
                 "pages": [{"file": "sheet", "pts_us": [3000000]}], "size": (32, 24)}
        with patch("reel.region_sync.run_timing") as run:
            published_timing(scene, [[0, 0, 1, 1]])
            job, = run.call_args.args
            self.assertEqual([job[k] for k in ("source", "origin", "start", "end", "first_sample")],
                             [None, 2, 3, 4, 32000])
            self.assertEqual(published_timing({**scene, "picture_end": 2}, [[0, 0, 1, 1]])["status"], "unavailable")
            run.assert_called_once()

    def test_invalid_coordinates_and_no_concurrent_media(self):
        validate_regions([(0, .1, 1, .2)])
        for box in [(0, 0, 2, 1), (0, 1, 1, 1), (0, 0, float('nan'), 1)]:
            with self.assertRaises(ValueError):
                validate_regions([box])
        result = regional_timing(None, {"moments": [{"picture": None}], "audio": None}, [(0, 0, 1, 1)])
        self.assertEqual(result["status"], "unavailable")
