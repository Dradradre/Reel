from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from reel import caption_matches as captions
from reel import check_captions


class CaptionMatchesTests(unittest.TestCase):
    def test_neighbours_describe_different_recordings_with_stable_ties(self):
        index = captions.CaptionIndex.__new__(captions.CaptionIndex)
        index.captions = ["first", "same recording", "second", "third", "fourth", "fifth"]
        index.groups = [10, 10, 11, 12, 13, 14]
        index.vectors = np.array([[1.], [.99], [.8], [.8], [.4], [.1]])
        result = index.nearest(np.array([1.]))
        self.assertEqual([m["caption"] for m in result], ["first", "second", "third", "fourth"])
        self.assertEqual(result[1]["cosine"], .8)

    def test_only_released_samples_and_distinct_recorded_channels_reach_the_index(self):
        model, index = Mock(), Mock()
        model.audio_features.return_value = [Mock()]
        index.nearest.return_value = [{"caption": "a similar sound", "cosine": .6}]
        rate = 48000
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            x = np.zeros((rate + 3200, 3), dtype=np.float32)
            x[:rate, :2] = .2
            x[rate:, 2] = .6
            sf.write(wav, x, rate, subtype="FLOAT")
            result = captions.match_captions(wav, 7, 8, 7 * rate, lambda: (model, index))
            window, = result["windows"]
            self.assertEqual((window["start_s"], window["end_s"], window["wav_samples"]), (7, 8, [0, rate]))
            group, = window["active_channels"]
            self.assertEqual(group["channels"], [1, 2])
            args, _ = model.audio_features.call_args
            self.assertEqual(args[1], "pad")
            np.testing.assert_array_equal(args[0][0], x[:rate, 0])
            model.audio_features.assert_called_once()

    def test_short_sound_and_silence_are_distinct_without_model_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "short.wav"
            sf.write(wav, np.column_stack((np.full(80, .1), np.zeros(80))), 48000)
            loader = Mock(side_effect=AssertionError("too short to compare captions"))
            result = captions.match_captions(wav, 0, .01, 0, loader)
            self.assertEqual(result["windows"][0]["active_channels"],
                             [{"channels": [1], "status": "insufficient_encoder_input", "matches": []}])
            sf.write(wav, np.zeros(4800), 48000)
            self.assertEqual(captions.match_captions(wav, 0, .1, 0, loader)["windows"][0]["active_channels"], [])
            loader.assert_not_called()

    def test_no_setup_keeps_the_original_recording_accessible(self):
        with patch.object(captions, "ready", return_value=False), patch.object(captions, "LocalReader") as worker:
            self.assertEqual(captions.inspect_matches("unused", 0, 1, 0)["status"], "not_prepared")
            worker.assert_not_called()

    def test_independent_check_scores_only_the_proposed_caption_on_the_original_channel(self):
        verifier = check_captions.Verifier.__new__(check_captions.Verifier)
        verifier.encoder = Mock()
        verifier.encoder.audio.return_value = [Mock()]
        verifier.encoder.audio.return_value[0].cpu.return_value.numpy.return_value = np.array([1., 0.])
        verifier.vectors = {"footsteps": np.array([.75, 0.]), "voices": np.array([.10, 1.])}
        verifier.anchors = np.column_stack((np.r_[np.zeros(125), [.75] * 3], np.ones(128)))
        verifier.anchors[0, 0] = .15
        proposed = [{"caption": "voices", "cosine": .82}, {"caption": "footsteps", "cosine": .78}]
        result = verifier.check(np.ones(2048), proposed)
        self.assertEqual(result, [{"caption": "footsteps", "cosine": .78, "verification_dot_product": .75,
                                   "above_controls": 125}])
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "before.wav"
            sf.write(wav, np.zeros((4800, 2)), 48000)
            window = {"wav_samples": [0, 1200], "active_channels": [{"channels": [2], "matches": proposed}]}
            loader = Mock(side_effect=AssertionError("too short for verification"))
            checked = check_captions.verify(wav, 0, .025, 0, [window], loader)
            group = checked["windows"][0]["active_channels"][0]
            self.assertEqual((group["status"], group["matches"], group["withheld"]),
                             ("insufficient_verification_input", [], 2))
            loader.assert_not_called()
