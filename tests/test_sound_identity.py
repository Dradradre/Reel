import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import soundfile as sf

from reel.identify_sound import ranking, read_identity


class SoundIdentityTests(unittest.TestCase):
    def test_comparisons_stop_on_the_film_clock_and_do_not_mix_channels(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder) / "released.wav"
            rate = 8000
            data = np.tile([.125, -.125, .125, 0], (rate * 3, 1))
            data[rate * 2:] = .75
            sf.write(wav, data, rate, subtype="FLOAT")
            enc = Mock()
            enc.prepare.side_effect = lambda labels: labels
            enc.scores.side_effect = [([.4, .2], [.5, .3]), ([.2, .4], [.5, .3])]
            result = read_identity(wav, 4., 6., 4 * rate, ["hum", "rattle"], lambda: enc)
            win, = result["windows"]
            self.assertEqual((win["start_s"], win["end_s"]), (4, 6))
            left, right = win["channels"]
            self.assertEqual((left["channels"], left["shared_choice"]), ([1, 3], "hum"))
            self.assertEqual((right["channels"], right["status"], right["shared_choice"]), ([2], "unresolved", None))
            signals = [call.args[0] for call in enc.scores.call_args_list]
            np.testing.assert_array_equal(signals[0], np.full(2 * rate, .125))
            np.testing.assert_array_equal(signals[1], -signals[0])

    def test_unheard_future_does_not_give_silence_a_source(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder) / "quiet.wav"
            sf.write(wav, np.r_[np.zeros(16000), np.ones(8000) * .5], 8000)
            get = Mock(side_effect=AssertionError("silence must bypass both encoders"))
            result = read_identity(wav, 0, 2, None, [], get)
            self.assertEqual(result["windows"], [{"start_s": 0., "end_s": 2., "status": "digital_silence", "channels": []}])
            with self.assertRaisesRegex(ValueError, "two or more"):
                read_identity(wav, 0, 2, None, ["hum", " hum "], get)
            get.assert_not_called()

    def test_tied_or_invalid_scores_cannot_name_a_source(self):
        first, nearest = ranking(["tap", "click"], [.3, .3])
        self.assertIsNone(first)
        self.assertEqual([r["description"] for r in nearest], ["tap", "click"])
        for scores in ([.5], [.2, np.nan], [[.5, .6]]):
            with self.assertRaisesRegex(ValueError, "invalid acoustic ranking"):
                ranking(["tap", "click"], scores)
