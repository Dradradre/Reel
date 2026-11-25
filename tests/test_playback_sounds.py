from importlib.util import find_spec
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from reel.compare_sounds import Comparator, SoundMatcher
from reel.common import resample_channels
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

    def test_later_samples_never_enter_source_estimates_or_the_next_turns_history(self):
        rate = 16000
        tagger = Mock(labels=["Music"])
        tagger.scores.side_effect = lambda clips: np.ones((len(clips), 1))
        reader = PlaybackSounds(tagger)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "recording.wav"
            prefix = np.zeros(8001)
            # The sample at 1.5 s belongs to a release ending half a sample later.
            prefix[-1] = .125
            sf.write(wav, np.r_[prefix, np.ones(rate * 5) * .5], rate, subtype="FLOAT")
            cutoff = 1.50003125
            windows = reader.read(wav, rate, cutoff)["windows"]
            self.assertEqual([(w["start_s"], w["end_s"]) for w in windows], [(1., cutoff)])
            tagger.scores.assert_called_once()
            np.testing.assert_array_equal(tagger.scores.call_args.args[0][0], prefix)
            next_sample = rate + len(prefix)
            self.assertEqual(reader.read(wav, next_sample, cutoff)["windows"], [])
            sf.write(wav, np.full(400, .25), rate, subtype="FLOAT")
            window, = reader.read(wav, next_sample, (next_sample + 400) / rate)["windows"]
            self.assertEqual(window["context_start_s"], 1.)
            np.testing.assert_array_equal(tagger.scores.call_args.args[0][0], np.r_[prefix, np.full(400, .25)])

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

    @unittest.skipUnless(find_spec("torch"), "CLAP matching needs the optional torch extra")
    def test_per_second_slices_native_channels_before_resampling(self):
        rate = 44100
        first = 100
        pcm = np.zeros((rate + 233, 2), dtype=np.int16)
        pcm[rate - 1, 0] = 30000
        pcm[rate, 1] = -24000
        matcher = Comparator.__new__(Comparator)
        matcher.references = (["impact"], object())
        matcher.similarities = Mock(side_effect=lambda channels, text, **kw: np.full((len(channels), 1), .35))
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
        *calls, continuous = matcher.similarities.call_args_list
        self.assertEqual([len(call.args[0]) for call in calls], [1, 1])
        self.assertEqual([len(call.args[0][0]) for call in calls], [48000, 254])
        self.assertEqual([call.kwargs["padding"] for call in calls], ["repeatpad", "repeatpad"])
        self.assertEqual(continuous.kwargs["padding"], "pad")
        expected = resample_channels(pcm.astype(np.float32) / 32768, rate, 48000)
        np.testing.assert_array_equal(np.stack(continuous.args[0], axis=1), expected)
        whole, = report["continuous_windows"]
        self.assertEqual(whole["wav_samples"], [0, len(pcm)])
        self.assertEqual((whole["start_s"], whole["end_s"]), (first / rate, end))

    def test_continuous_matches_bound_the_encoder_and_keep_a_silent_tail(self):
        from reel.compare_sounds import native_windows
        rate = 8000
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "tail.wav"
            pcm = np.zeros(10 * rate + 13, dtype=np.int16)
            pcm[10 * rate - 1] = 20000
            sf.write(wav, pcm, rate)
            spans = list(native_windows(wav, 3, 13 + 13 / rate, 3 * rate, 10))
        self.assertEqual([s["wav_samples"] for s, _ in spans], [[0, 80000], [80000, 80013]])
        self.assertEqual([s["start_s"] for s, _ in spans], [3, 13])
        self.assertTrue(np.any(spans[0][1]))
        self.assertFalse(np.any(spans[1][1]))

    def test_sound_after_cutoff_never_enters_either_matching_scale(self):
        matcher = Comparator.__new__(Comparator)
        matcher.references = (["click"], object())
        matcher.similarities = Mock(side_effect=AssertionError("the encoder heard the unreleased click"))
        rate, first = 44100, 17
        cutoff = (first + 11026.5) / rate
        pcm = np.zeros((3 * rate, 2), dtype=np.int16)
        pcm[11027:, 1] = 24000
        with tempfile.TemporaryDirectory() as tmp, patch.dict("sys.modules", {"torch": Mock()}):
            wav = Path(tmp) / "long.wav"
            sf.write(wav, pcm, rate)
            for detailed in (True, False):
                report = matcher.match(wav, first / rate, cutoff, [], detailed, first_sample=first)
                for window in report["windows"] + report.get("continuous_windows", []):
                    self.assertEqual(window["wav_samples"], [0, 11027])
                    self.assertEqual(window["end_s"], cutoff)
                    self.assertTrue(all(ch["reference_neighbours"] == [] for ch in window["channels"]))
        matcher.similarities.assert_not_called()

    def test_recording_similarity_keeps_channel_order_and_silent_channels(self):
        matcher = Comparator.__new__(Comparator)
        features = []
        for xy in ((1., 0.), (0., 1.), (0., 1.), (1., 0.)):
            feature = Mock()
            feature.cpu.return_value.numpy.return_value = np.array(xy)
            features.append([feature])
        matcher.audio_features = Mock(side_effect=features)
        rate = 24000
        tone = np.sin(2 * np.pi * 440 * np.arange(rate // 10) / rate).astype(np.float32)
        with tempfile.TemporaryDirectory() as tmp:
            ref, target = (Path(tmp) / name for name in ("ref.wav", "target.wav"))
            sf.write(ref, np.stack([tone, -tone, np.zeros_like(tone)], axis=1), rate, subtype="FLOAT")
            sf.write(target, np.stack([-tone, tone], axis=1), rate, subtype="FLOAT")
            comparison = matcher.compare_recordings(ref, target)
        self.assertEqual(comparison["cosines"], [[0., 1.], [1., 0.], [None, None]])
        signals = []
        for call in matcher.audio_features.call_args_list:
            channels, padding = call.args
            self.assertEqual(padding, "pad")
            self.assertEqual(len(channels), 1)
            self.assertEqual(len(channels[0]), 4800)
            signals.append(channels[0])
        np.testing.assert_array_equal(signals[0], -signals[1])
        np.testing.assert_array_equal(signals[0], signals[3])

    def test_comparing_descriptions_preserves_channels_and_an_unmeasurable_change(self):
        matcher = Comparator.__new__(Comparator)
        terms = ["running water", "metal tapping"]
        matcher.text_features = Mock()
        matcher.text_features.return_value.cpu.return_value.numpy.return_value = np.eye(2)
        matcher.references = (["running water", "tap"], matcher.text_features.return_value)

        def encode(channels, padding):
            vector = Mock()
            vector.cpu.return_value.numpy.return_value = np.array([1., 0.] if channels[0][0] > 0 else [0., 1.])
            return [vector]

        matcher.audio_features = Mock(side_effect=encode)
        with tempfile.TemporaryDirectory() as tmp:
            ref, target = (Path(tmp) / name for name in ("ref.wav", "target.wav"))
            left, quiet = np.ones(100, dtype=np.float32) * .1, np.zeros(100, dtype=np.float32)
            sf.write(ref, np.column_stack((left, quiet)), 48000)
            sf.write(target, np.column_stack((left, -left)), 48000)
            result = matcher.compare_recordings(ref, target, terms)["description_comparison"]
            self.assertEqual(result["descriptions"], terms)
            self.assertEqual(result["reference"], [[1., 0.], None])
            self.assertEqual(result["target"], [[1., 0.], [0., 1.]])
            self.assertEqual(result["target_minus_reference"], [-.5, .5])
            self.assertEqual(result["catalogue"]["reference"][1], [])
            self.assertEqual(result["catalogue"]["target"][1][0], {"sound": "tap", "cosine": 1.0})
            repeat = matcher.compare_recordings(target, target, terms)["description_comparison"]
            self.assertEqual(repeat["status"], "identical_encoder_features")
            self.assertEqual(repeat["target_minus_reference"], [0., 0.])
            self.assertNotIn("catalogue", repeat)
            with patch.object(matcher, "reference_labels", side_effect=OSError("no local vocabulary")):
                missing = matcher.compare_recordings(ref, target, terms)["description_comparison"]
            self.assertEqual(missing["catalogue"]["status"], "unavailable")
            self.assertEqual(missing["target"], result["target"])
            sf.write(ref, quiet, 48000)
            silent = matcher.compare_recordings(ref, target, terms)["description_comparison"]
            self.assertEqual(silent["status"], "no_signal_at_encoder_rate_in_one_or_both")
            self.assertIsNone(silent["target_minus_reference"])
            self.assertNotIn("catalogue", silent)
            both_silent = matcher.compare_recordings(ref, ref, terms)["description_comparison"]
            self.assertIn("Neither interval", both_silent["basis"])

    def test_future_and_empty_hypotheses_do_not_start_the_model(self):
        with SoundMatcher() as reader, patch.object(reader, "request") as request:
            with self.assertRaises(ValueError):
                reader.read("not-opened", 0, 1, [" "])
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "view.json").write_text('{"schema":"reel.play.v1","audio":{"wav":"sound.wav"},"end_s":2}')
                with self.assertRaisesRegex(ValueError, "delivered recording"):
                    reader.inspect(tmp, 1, 2.01, ["rain"])
            request.assert_not_called()
