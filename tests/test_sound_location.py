from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
import soundfile as sf

from reel.localize_sound import locate_seconds


class SoundLocationTests(unittest.TestCase):
    def test_keeps_stereo_partial_second_and_native_cutoff(self):
        rate = 1000
        audio = np.zeros((3000, 3), dtype=np.float32)
        audio[:1000, :2] = .25
        audio[1000:1251, 2] = -.5
        audio[1251:] = 1  # private suffix
        encoder = Mock()
        def score(signal, rate, vectors):
            self.assertEqual((len(signal), rate), (1251, 1000))
            frames = np.pad(signal, (0, 29)).reshape(-1, 40)
            return np.stack((np.max(frames, axis=1), -np.min(frames, axis=1)), axis=1)

        encoder.score.side_effect = score
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "channels.wav"
            sf.write(wav, audio, rate, subtype="FLOAT")
            result = locate_seconds(wav, 4.0001, 5.2511, 4001, ["first", "second"], lambda: encoder)
        self.assertEqual(encoder.score.call_count, 2)
        self.assertEqual([r["best_interval_s"] for r in result["locations"]], [[4.001, 5.001], [5.001, 5.2511]])

    def test_silence_does_not_start_encoder_and_constant_scores_have_no_unique_location(self):
        encoder = Mock()
        encoder.score.return_value = np.full((50, 1), .4)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "track.wav"
            for value, status in [(0., "no_signal"), (.2, "available")]:
                sf.write(wav, np.full(200, value), 100, subtype="FLOAT")
                result = locate_seconds(wav, 0, 2, None, ["a sound"], lambda: encoder)
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["locations"][0]["best_interval_s"])
                if not value:
                    encoder.score.assert_not_called()
