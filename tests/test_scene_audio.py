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
            signal = describe.call_args.args[0][0]
            np.testing.assert_array_equal(signal[:-rate // 2], np.zeros(19 * rate // 2))
            np.testing.assert_array_equal(signal[-rate // 2:], np.full(rate // 2, -.125))
