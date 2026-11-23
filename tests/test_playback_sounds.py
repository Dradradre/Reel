from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from reel.compare_sounds import Comparator, SoundMatcher
from reel.sounds import PlaybackSounds


class PlaybackSoundTests(unittest.TestCase):
    def test_history_uses_only_contiguous_samples_and_preserves_channels(self):
        tagger = Mock(labels=["Silence", "Music"])
        tagger.scores.side_effect = lambda clips: np.tile([0.8, 0.65], (len(clips), 1))
        reader = PlaybackSounds(tagger)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "released.wav"
            active = np.full(16000, 0.125)
            sf.write(wav, np.column_stack((active, active, -active)), 16000)
            first = reader.read(wav, 0, 1)
            self.assertEqual(len(tagger.scores.call_args.args[0]), 2)
            self.assertEqual(first["windows"][0]["channels"][1]["candidates"], [{"sound": "Music", "score": .65}])
            sf.write(wav, np.column_stack((active, np.zeros_like(active), -active)), 16000)
            second = reader.read(wav, 16000, 2)
            window = second["windows"][0]
            self.assertEqual((window["start_s"], window["context_start_s"]), (1, 0))
            self.assertEqual(window["channels"][1], {"channel": 2, "signal": "digital_silence", "candidates": []})
            self.assertEqual(len(tagger.scores.call_args.args[0][0]), 32000)
            self.assertEqual(reader.read(wav, 64000, 5)["windows"][0]["context_start_s"], 4)

    def test_fractional_cutoff_and_sub_encoder_signal(self):
        tagger = Mock(labels=["Music"])
        reader = PlaybackSounds(tagger)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "short.wav"
            sf.write(wav, [1e-50] * 25, 8000, subtype="DOUBLE")
            window = reader.read(wav, 0, .00301)["windows"][0]
        self.assertEqual(window["end_s"], .00301)
        self.assertEqual(window["channels"], [{"channel": 1, "signal": "nonzero", "candidates": []}])
        tagger.scores.assert_not_called()

    def test_sound_matches_keep_every_description_and_silent_channels(self):
        matcher = Comparator.__new__(Comparator)
        texts = ["a rattle", "water rushing", "a distant electric hum"]
        matcher.references = (["noise", "birds"], object())
        matcher.text_features = Mock(return_value=object())
        matcher.similarities = Mock(return_value=np.array([[.3, .18, -.1, .25, .05], [.1, .16, .37, .15, .3]]))
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "channels.wav"
            active = np.full(4800, .2)
            sf.write(wav, np.column_stack((active, np.zeros(4800), -active)), 48000)
            with patch.dict("sys.modules", {"torch": Mock()}):
                result = matcher.match(wav, 4.5, 4.6, texts)
        matcher.text_features.assert_called_once_with(texts)
        window = result["windows"][0]
        self.assertEqual((window["start_s"], window["end_s"]), (4.5, 4.6))
        self.assertEqual(window["channels"][0]["reference_neighbours"][0]["sound"], "noise")
        self.assertEqual(window["channels"][1]["reference_neighbours"], [])
        self.assertEqual([ch["cosines"] for ch in window["channels"]], [[.3, .18, -.1], [], [.1, .16, .37]])

    def test_per_second_slices_native_channels_before_resampling(self):
        rate = 44100
        first = 100
        pcm = np.zeros((rate + 233, 2), dtype=np.int16)
        pcm[rate - 1, 0] = 30000
        pcm[rate, 1] = -24000
        matcher = Comparator.__new__(Comparator)
        matcher.references = (["impact"], object())
        matcher.similarities = Mock(return_value=np.array([[.35]]))
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "native.wav"
            sf.write(wav, pcm, rate)
            end = (first + len(pcm)) / rate
            report = matcher.match(wav, first / rate, end, [], per_second=True, first_sample=first)
        left, right = report["windows"]
        self.assertEqual([left["wav_samples"], right["wav_samples"]], [[0, rate], [rate, rate + 233]])
        self.assertEqual(left["start_s"], first / rate)
        self.assertEqual(left["end_s"], right["start_s"])
        self.assertEqual(right["end_s"], end)
        self.assertEqual([bool(c["reference_neighbours"]) for c in left["channels"]], [True, False])
        self.assertEqual([bool(c["reference_neighbours"]) for c in right["channels"]], [False, True])
        calls = matcher.similarities.call_args_list
        self.assertEqual([len(call.args[0]) for call in calls], [1, 1])
        self.assertEqual([len(call.args[0][0]) for call in calls], [48000, 254])

    def test_future_and_empty_hypotheses_do_not_start_the_model(self):
        with SoundMatcher() as reader, patch.object(reader, "request") as request:
            with self.assertRaises(ValueError):
                reader.read("not-opened", 0, 1, [" "])
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "view.json").write_text('{"schema":"reel.play.v1","audio":{"wav":"sound.wav"},"end_s":2}')
                with self.assertRaisesRegex(ValueError, "delivered recording"):
                    reader.inspect(tmp, 1, 2.01, ["rain"])
            request.assert_not_called()
