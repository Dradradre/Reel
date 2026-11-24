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

    def test_two_models_can_decline_their_shared_choice_without_a_score_threshold(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder) / "sound.wav"
            sf.write(wav, np.ones(8000) * .25, 8000)
            enc = Mock()
            enc.prepare.return_value = ["tapping", "rubbing", "water"]
            enc.scores.return_value = (np.array([.3, .1, .5]), np.array([.4, .1, .6]))
            win, = read_identity(wav, 0, 1, None, ["tapping", "rubbing"], lambda: enc)["windows"]
            ch, = win["channels"]
            self.assertEqual((ch["status"], ch["nearest_shared_alternative"], ch["shared_choice"]),
                             ("unresolved", "tapping", None))
            self.assertTrue(ch["outside_choices"]["both_prefer_outside"])
            enc.scores.return_value = (np.array([.6, .2, .4]), np.array([.4, .1, .6]))
            win, = read_identity(wav, 0, 1, None, ["tapping", "rubbing"], lambda: enc)["windows"]
            self.assertEqual(win["channels"][0]["shared_choice"], "tapping")

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

    def test_subframe_signal_is_retained_without_naming_a_source(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder) / "impulse.wav"
            sf.write(wav, np.r_[.5, np.zeros(319)], 8000)
            get = Mock(side_effect=AssertionError("insufficient real acoustic context"))
            win, = read_identity(wav, 0, .039875, None, ["hum", "tap"], get)["windows"]
            self.assertEqual((win["status"], win["end_s"]), ("signal", .039875))
            self.assertEqual(win["channels"], [{"channels": [1], "status": "shorter_than_encoder_frame",
                                                "shared_choice": None}])
            get.assert_not_called()

    def test_tied_or_invalid_scores_cannot_name_a_source(self):
        first, nearest = ranking(["tap", "click"], [.3, .3])
        self.assertIsNone(first)
        self.assertEqual([r["description"] for r in nearest], ["tap", "click"])
        for scores in ([.5], [.2, np.nan], [[.5, .6]]):
            with self.assertRaisesRegex(ValueError, "invalid acoustic ranking"):
                ranking(["tap", "click"], scores)
