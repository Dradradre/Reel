import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import soundfile as sf

from reel.scene_audio import SceneHistory


class SceneAudioTests(unittest.TestCase):
    def test_context_crosses_pauses_but_stops_at_discontinuities(self):
        describe = Mock(side_effect=lambda channels, rate: ["a sound"] * len(channels))
        ear = SceneHistory(describe)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "turn.wav"
            rate = 8000
            tone = np.linspace(-.1, .1, rate, dtype=np.float32)
            sf.write(wav, np.column_stack((tone, tone, -tone)), rate, subtype="FLOAT")
            self.assertEqual(ear.read(wav, 0, 1)["windows"], [])
            window = ear.read(wav, rate, 2)["windows"][0]
            self.assertEqual((window["start_s"], window["end_s"]), (0, 2))
            self.assertEqual([row["channels"] for row in window["channels"]], [[1, 2], [3]])
            signals, sr = describe.call_args.args
            np.testing.assert_array_equal(signals[0], np.tile(tone, 2))
            np.testing.assert_array_equal(signals[1], -signals[0])
            self.assertEqual(sr, rate)
            self.assertEqual(ear.read(wav, 4 * rate, 5)["windows"], [])
            describe.assert_called_once()

    def test_silent_history_expires_and_never_produces_a_caption_of_its_own(self):
        describe = Mock(return_value=["a sound"])
        ear = SceneHistory(describe)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "turn.wav"
            rate = 8000
            sf.write(wav, np.full(2 * rate, .125), rate)
            ear.read(wav, 0, 2)
            sf.write(wav, np.zeros(12 * rate), rate)
            self.assertEqual(ear.read(wav, 2 * rate, 14)["windows"], [])
            describe.assert_called_once()
            sf.write(wav, np.full(rate // 2, -.125), rate)
            window = ear.read(wav, 14 * rate, 14.49998)["windows"][0]
            self.assertEqual((window["start_s"], window["end_s"]), (4.5, 14.49998))
            signal = describe.call_args_list[-2].args[0][0]
            short = describe.call_args.args[0][0]
            np.testing.assert_array_equal(short, signal[-2 * rate:])
            np.testing.assert_array_equal(signal[:-rate // 2], np.zeros(19 * rate // 2))
            np.testing.assert_array_equal(signal[-rate // 2:], np.full(rate // 2, -.125))

    def test_recent_description_uses_only_its_own_samples_channels_and_bounds(self):
        def describe(signals, rate):
            return [str(float(np.mean(channel))) for channel in signals]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slice.wav"
            rate = 8000
            def readings(prior):
                ear = SceneHistory(describe)
                sf.write(path, np.tile([prior, -prior, prior], (4 * rate, 1)), rate, subtype="FLOAT")
                self.assertNotIn("recent_windows", ear.read(path, 0, 4))
                sf.write(path, np.tile([.375, .375, 0], (3 * rate, 1)), rate, subtype="FLOAT")
                return ear.read(path, 4 * rate, 6.99999)
            a, b = readings(.5), readings(-.25)
            self.assertNotEqual(a["windows"], b["windows"])
            expected = [{"start_s": 4., "end_s": 6.99999,
                         "channels": [{"channels": [1, 2], "impression": "0.375"}]}]
            self.assertEqual(a["recent_windows"], expected)
            self.assertEqual(b["recent_windows"], expected)

    def test_short_pauses_share_a_recent_interval_and_reset_on_a_gap(self):
        describe = Mock(return_value=["a sound"])
        ear = SceneHistory(describe)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slice.wav"
            rate = 8000
            sf.write(path, np.full(rate // 4, .25), rate)
            windows = []
            for index in range(24):
                row = ear.read(path, index * (rate // 4), (index + 1) / 4)
                windows.extend(row.get("recent_windows", []))
            self.assertEqual([(w["start_s"], w["end_s"]) for w in windows], [(2, 4), (4, 6)])
            self.assertEqual(ear.read(path, 7 * rate, 7.25)["windows"], [])

    def test_short_read_failure_preserves_the_wider_impression(self):
        ear = SceneHistory(Mock(side_effect=[["first"], ["whole recording"], RuntimeError("encoder failed")]))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slice.wav"
            sf.write(path, np.ones(16000) * .125, 8000)
            ear.read(path, 0, 2)
            reading = ear.read(path, 16000, 4)
        self.assertEqual(reading["windows"][0]["channels"][0]["impression"], "whole recording")
        self.assertEqual(reading["recent_windows"],
                         [{"start_s": 2, "end_s": 4, "status": "unavailable", "channels": []}])
