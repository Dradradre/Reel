from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf

from reel.sound_events import EventHistory, active_channels


class SoundEventTests(unittest.TestCase):
    def test_sample_windows_are_independent_of_pauses_and_end_at_released_samples(self):
        rate = 11025
        ramp = np.linspace(-.25, .25, 5 * rate, dtype=np.float32)
        pcm = np.column_stack((ramp, ramp, np.zeros_like(ramp)))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.wav"
            def listen(cuts):
                excerpts = []
                def score(signal, native_rate):
                    self.assertEqual(native_rate, rate)
                    excerpts.append(signal.copy())
                    return [("audible pattern", .8)]
                ear = EventHistory(score)
                start = 0
                counted = 0
                for end in cuts:
                    sf.write(path, pcm[start:end], rate, subtype="FLOAT")
                    result = ear.read(path, start + 17, (end + 17) / rate)
                    counted += result["windows_evaluated"]
                    for row in result["candidates"]:
                        self.assertEqual(row["channels"], [1, 2])
                        self.assertLessEqual(row["end_s"], (17 + end) / rate)
                        self.assertGreaterEqual(row["start_s"], 17 / rate)
                    start = end
                return excerpts, counted
            continuous, count = listen([len(pcm)])
            divided, other_count = listen([36, 8000, 13512, 33785, len(pcm)])
            self.assertEqual(count, other_count)
            self.assertEqual(count, 9)
            for i, (a, b) in enumerate(zip(continuous, divided, strict=True)):
                left = (i * 7680 * rate + 15999) // 16000
                right = ((i * 7680 + 15600) * rate + 15999) // 16000
                np.testing.assert_array_equal(a, ramp[left:right])
                np.testing.assert_array_equal(a, b)

    def test_discontinuity_discards_the_old_waveform_and_silence_has_no_source(self):
        def score(signal, rate):
            return [("sound", .6)]
        ear = EventHistory(score)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.wav"
            sf.write(path, np.ones(10000) * .1, 16000)
            self.assertEqual(ear.read(path, 0, .625)["windows_evaluated"], 0)
            self.assertEqual(ear.read(path, 50000, 3.75)["windows_evaluated"], 0)
            sf.write(path, np.zeros(32000), 16000)
            report = ear.read(path, 0, 2)
            self.assertGreater(report["windows_evaluated"], 0)
            self.assertEqual(report["candidates"], [])
            self.assertLess(report["through_s"], 2)

    def test_samples_after_the_requested_cutoff_never_reach_the_classifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.wav"
            signal = np.zeros(32000, np.float32)
            signal[16000:] = .8
            sf.write(path, signal, 16000, subtype="FLOAT")
            def score(*_):
                self.fail("the only nonzero samples are after the cutoff")
            ear = EventHistory(score)
            report = ear.read(path, 0, 1.)
            self.assertEqual(report["windows_evaluated"], 1)
            self.assertEqual(report["candidates"], [])
            self.assertEqual(ear.next_sample, 16000)

    def test_silent_channels_duplicates_and_opposite_phase(self):
        signal = np.array([0., .25, -.4], np.float32)
        pcm = np.column_stack((signal, np.zeros_like(signal), signal, -signal))
        groups = active_channels(pcm)
        self.assertEqual([channels for channels, _ in groups], [[1, 3], [4]])
        np.testing.assert_array_equal(groups[-1][1], -signal)

    def test_an_unsupported_window_separates_labels(self):
        results = iter([[("source", .6)], [("source", .8)], [], [("source", .7)]])
        ear = EventHistory(lambda *_: next(results))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.wav"
            sf.write(path, np.full(15600+3*7680, .3), 16000)
            events = ear.read(path, 0, 2.415)["candidates"]
        self.assertEqual([r["windows"] for r in events], [2, 1])
        self.assertEqual([r["peak_score"] for r in events], [.8, .7])
