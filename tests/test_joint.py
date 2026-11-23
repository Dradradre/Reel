from fractions import Fraction
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image
import soundfile as sf

from reel.frames import frame_sheets
from reel.hearing import ListeningHistory, WaveListener, audiovisual_positions
from reel.joint import RATE, picture_windows, read_joint, visual_input


class Capture:
    def __init__(self):
        self.audio = []
        self.joint = []

    def __call__(self, signal):
        self.audio.append(signal.copy())
        return "audio read"

    def with_picture(self, signal, layout, visual, fps, start, end, question):
        self.joint.append((signal.copy(), layout, visual, fps, start, end, question))
        return "joint read"


class JointTest(unittest.TestCase):
    size = (24, 12)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def pages(self, pts):
        frames = ((t, 0, Image.new("RGB", self.size, (i+1, 0, 0)).tobytes()) for i, t in enumerate(pts))
        return frame_sheets(self.root, *self.size, frames)

    def wav(self, samples, rate=RATE):
        path = self.root / "part.wav"
        sf.write(path, samples, rate, subtype="FLOAT")
        return path

    def test_same_signal_in_both_reads_and_separate_native_channels(self):
        left = np.linspace(-.1, .1, RATE, dtype=np.float32)
        wav = self.wav(np.column_stack((left, left, -left, left * 0)))
        reader = Capture()
        result = read_joint(reader, wav, 0, 1, 0, self.pages([0, 500_000, 1_000_000]), self.size, 1, "What moved?")
        window, = result["windows"]
        self.assertEqual(window["picture_pts_us"], [0, 500_000])
        self.assertEqual(window["wav_samples"], [0, RATE])
        self.assertEqual([c["channels"] for c in window["channels"]], [[1, 2], [3], [4]])
        self.assertEqual(window["channels"][-1], {"channels": [4], "status": "digital_silence"})
        self.assertEqual(len(reader.audio), 2)
        for i, (wave, layout, video, fps, start, end, question) in enumerate(reader.joint):
            np.testing.assert_array_equal(reader.audio[i], left if i == 0 else -left)
            np.testing.assert_array_equal(wave, reader.audio[i])
            self.assertEqual((layout, fps, start, end, question), ("video", 2, 0, 1, "What moved?"))
            self.assertEqual(video[:, 0, 0, 0].tolist(), [1, 2])

    def test_full_recording_and_aligned_picture_use_identical_encoder_samples(self):
        rate = 44100
        pcm = np.random.default_rng(7).normal(0, .1, (rate, 2)).astype(np.float32)
        wav = self.wav(pcm, rate)
        aligned, audio = Capture(), Capture()
        read_joint(aligned, wav, 0, 1, 0, self.pages([0, 500_000]), self.size, 1)
        ListeningHistory(audio).read(wav, 0, 1, False)
        self.assertEqual(len(audio.audio), 2)
        for standalone, matching in zip(audio.audio, aligned.audio, strict=True):
            np.testing.assert_array_equal(standalone, matching)

    def test_short_vfr_flash_and_held_picture_survive_without_future_pixels(self):
        pages = self.pages([0, 110_000, 111_000, 800_000])
        (start, end, entries), = picture_windows(pages, .1, .8)
        self.assertEqual([row[3] for row in entries], [0, 110_000, 111_000])
        layout, video, onsets, order = visual_input(entries, self.size, start, end)
        self.assertEqual((layout, order), ("timed_video", [0, 0, 1, 1, 2, 2]))
        np.testing.assert_allclose(onsets, [0, .01, .011])
        self.assertEqual(video[:, 0, 0, 0].tolist(), [1, 1, 2, 2, 3, 3])

        # The second exposure is displayed at 380 ms, between uniform 2 fps samples.
        (start, end, entries), = picture_windows(self.pages([0, 380_000]), 0, 1)
        self.assertEqual(visual_input(entries, self.size, start, end)[2], [0, .38])

    def test_long_context_holds_repeated_pixels_on_the_film_clock(self):
        colors = [1] * 30 + [2] * 30
        pages = frame_sheets(self.root, *self.size,
                             ((round(i * 1e6 / 30), 0, Image.new("RGB", self.size, (c, 0, 0)).tobytes())
                              for i, c in enumerate(colors)))
        (start, end, entries), = picture_windows(pages, 0, 2, long_context=True)
        layout, video, fps, order = visual_input(entries, self.size, start, end, long_context=True)
        self.assertEqual((layout, fps, order), ("video", 2., [0, 0, 30, 30]))
        self.assertEqual(video[:, 0, 0, 0].tolist(), [1, 1, 2, 2])

    def test_native_audio_ends_at_picture_boundary_and_windows_partition(self):
        times = [250_000 + i * 10_000 for i in range(62)]
        reader = Capture()
        result = read_joint(reader, self.wav(np.ones(RATE)), 0, 1, 0, self.pages(times), self.size, .87)
        windows = result["windows"]
        self.assertEqual([len(w["picture_pts_us"]) for w in windows], [48, 14])
        self.assertEqual([t for w in windows for t in w["picture_pts_us"]], times)
        self.assertEqual([w["wav_samples"] for w in windows], [[4000, 11680], [11680, 13920]])

    def test_long_context_caps_each_window_without_losing_frames_or_samples(self):
        times = [i * 10_000 for i in range(302)]
        reader = Capture()
        wav = self.wav(np.ones(3 * RATE))
        # Keep pixel values in range while encoding a fast frame sequence.
        pages = frame_sheets(self.root, *self.size,
                             ((t, 0, Image.new("RGB", self.size, (i % 256, 0, 0)).tobytes())
                              for i, t in enumerate(times)))
        result = read_joint(reader, wav, 0, 3, 0, pages, self.size, 3, long_context=True)
        self.assertEqual([w["wav_samples"] for w in result["windows"]], [[0, 38400], [38400, 48000]])
        self.assertEqual([t for w in result["windows"] for t in w["picture_pts_us"]], times[:300])

    def test_silence_subsample_and_missing_picture_do_not_get_source_descriptions(self):
        pages = self.pages([0])
        reader = Capture()
        for samples, status in [(np.zeros(RATE), "digital_silence"), (np.ones(32), "insufficient_encoder_input")]:
            end = len(samples)/RATE
            result = read_joint(reader, self.wav(samples), 0, end, 0, pages, self.size, end)
            self.assertEqual(result["windows"][0]["channels"], [{"channels": [1], "status": status}])
        result = read_joint(reader, self.wav(np.ones(RATE)), 0, 1, 0, self.pages([1_000_000]), self.size, 2)
        self.assertEqual(result["status"], "no_simultaneous_picture")
        self.assertEqual((reader.joint, reader.audio), ([], []))

    def test_exact_native_boundary_serialized_to_decimal(self):
        rate = 48000
        start, end = (float(Fraction(i, rate)) for i in (3200, 4003))
        result = read_joint(Capture(), self.wav(np.ones(803), rate), start, end, 3200,
                            self.pages([0]), self.size, end)
        self.assertEqual(result["windows"][0]["wav_samples"], [0, 803])

    def test_video_and_audio_reach_one_generation_on_the_requested_clock(self):
        ear = WaveListener.__new__(WaveListener)
        config = SimpleNamespace(video_token_id=71, audio_token_id=72, position_id_per_seconds=13,
                                 vision_config=SimpleNamespace(spatial_merge_size=2))
        ear.model = SimpleNamespace(config=SimpleNamespace(thinker_config=config))
        ear.array = np.asarray
        ear.template = Mock(return_value="watch")
        ear.generate = Mock(return_value=SimpleNamespace(text="a knock"))
        signal = np.ones(RATE)
        video = np.zeros((30, 8, 8, 3), dtype=np.uint8)
        ids = np.array([[1, 2, 3] + [71, 72] * 13 + [71, 71, 4, 5]])
        tensors = {"attention_mask": np.ones_like(ids), "input_ids": ids,
                   "video_grid_thw": np.array([[15, 2, 2]]), "video_second_per_grid": np.array([2/30]),
                   "pixel_values_videos": np.ones(12), "input_features": np.ones(9)}
        ear.processor = Mock(return_value=tensors)
        ear.processor.video_processor.temporal_patch_size = 2
        result = ear.with_picture(signal, "video", video, 30, 1, 2, None)
        self.assertEqual(result, "a knock")
        given = ear.processor.call_args.kwargs
        self.assertEqual((given["fps"], given["use_audio_in_video"]), (30, True))
        self.assertIs(given["videos"][0], video)
        np.testing.assert_array_equal(given["audio"][0], signal)
        generated = ear.generate.call_args.kwargs
        for name in ("pixel_values_videos", "input_features", "input_ids"):
            self.assertIs(generated[name], tensors[name])
        positions = generated["position_ids"][:, 0]
        np.testing.assert_allclose(positions[:, 3], positions[:, 4])
        np.testing.assert_allclose(positions[0, ids[0] == 71], 3 + np.arange(15) * (2/30) * 13, rtol=1e-6)
        np.testing.assert_array_equal(positions[1, ids[0] == 72], 3 + np.arange(13))
        self.assertLess(generated["rope_deltas"].item(), 0)

        tensors["video_second_per_grid"] = np.array([2.0])
        with self.assertRaisesRegex(ValueError, "picture clock"):
            ear.with_picture(signal, "video", video, 30, 1, 2, None)
        ear.generate.assert_called_once()

    def test_variable_clock_interleaves_audio_and_video_onsets_before_generation(self):
        ear = WaveListener.__new__(WaveListener)
        config = SimpleNamespace(video_token_id=7, audio_token_id=8, position_id_per_seconds=10,
                                 vision_config=SimpleNamespace(spatial_merge_size=2))
        ear.model = SimpleNamespace(config=SimpleNamespace(thinker_config=config))
        ear.array = np.asarray
        ear.template = Mock(return_value="watch")
        ear.generate = Mock(return_value=SimpleNamespace(text="read"))
        # Three grids with two spatial tokens each. The processor initially assumes a uniform rate.
        ids = np.array([[1, 7, 7, 8, 8, 8, 7, 7, 8, 8, 8, 7, 7, 8, 8, 8, 8, 3]])
        ear.processor = Mock(return_value={"attention_mask": np.ones_like(ids), "input_ids": ids,
                                           "video_grid_thw": np.array([[3, 2, 4]]),
                                           "video_second_per_grid": np.array([1/3])})
        ear.processor.video_processor.temporal_patch_size = 2
        signal = np.ones(RATE)
        ear.with_picture(signal, "timed_video", np.zeros((6, 8, 8, 3), np.uint8), [0., .1, .83], 0., 1., None)
        fed = ear.generate.call_args.kwargs
        actual = np.asarray(fed["input_ids"])[0]
        np.testing.assert_array_equal(actual, [1, 7, 7, 8, 7, 7, 8, 8, 8, 8, 8, 8, 8, 8, 7, 7, 8, 3])
        positions = fed["position_ids"][:, 0]
        np.testing.assert_allclose(positions[0, actual == 7], [1, 1, 2, 2, 9.3, 9.3])
        np.testing.assert_allclose(positions[0, actual == 8], np.arange(1, 11))
        self.assertTrue(np.all(np.diff(positions[0, 1:-1]) >= 0))

    def test_video_patches_share_time_but_retain_their_spatial_positions(self):
        ids = np.array([[11, 12] + [7] * 4 + [8] * 3 + [7] * 4 + [8] * 2 + [13, 14]])
        config = SimpleNamespace(audio_token_id=8, video_token_id=7, position_id_per_seconds=13,
                                 vision_config=SimpleNamespace(spatial_merge_size=2))
        media = {"input_ids": ids, "mask": np.ones_like(ids), "video_grid_thw": [[2, 4, 4]],
                 "video_second_per_grid": [.2]}
        axes, delta = audiovisual_positions(media, config)
        np.testing.assert_array_equal(axes[:, 0, 2:6], [[2]*4, [2, 2, 3, 3], [2, 3, 2, 3]])
        np.testing.assert_allclose(axes[0, 0, 9:13], 4.6)
        np.testing.assert_array_equal(axes[:, 0, -2:], [[7, 8]]*3)
        self.assertEqual(delta.item(), 9-ids.shape[1])


if __name__ == "__main__":
    unittest.main()
