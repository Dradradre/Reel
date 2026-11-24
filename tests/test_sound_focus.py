import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from reel.scene_audio import SceneHistory
from reel.source_examples import SourceHistory
from reel.sound_focus import FocusHistory, contrast_interval, contrast_measurements


class FocusedListeningTests(unittest.TestCase):
    def setUp(self):
        self.rate = 16000
        self.samples = np.zeros(4 * self.rate, dtype=np.float32)
        t = np.arange(self.rate // 2) / self.rate
        self.samples[self.rate: self.rate + len(t)] = .3 * np.sin(2 * np.pi * 550 * t)

    def test_localizes_event_and_preserves_identical_channels(self):
        left, right = contrast_interval(self.samples, self.rate)
        self.assertLess(left / self.rate, 1.05)
        self.assertGreater(right / self.rate, 1.45)
        self.assertLess((right - left) / self.rate, 1.)
        signals = np.column_stack([self.samples, self.samples, np.zeros_like(self.samples)])
        tracker = FocusHistory()
        groups = list(tracker.passages(signals, self.rate, 0, 0))
        self.assertEqual([(r[0], r[2], r[3]) for r in groups], [([1, 2], left, right)])
        self.assertFalse(list(tracker.passages(signals, self.rate, 0, 2 * self.rate)))
        self.assertIsNone(contrast_interval(np.ones(4 * self.rate), self.rate))

    def test_short_crop_is_original_pcm_and_cannot_use_unreleased_suffix(self):
        def identify(channel, rate):
            return {'name': 'tone', 'reference_similarity': .8}
        def describe(channels, rate):
            return [str(len(c)) for c in channels]
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'original.wav'
            previous = None
            for future in (0., .8):
                pcm = self.samples.copy()
                pcm[3 * self.rate:] = future
                sf.write(wav, pcm, self.rate, subtype='FLOAT')
                source = SourceHistory(identify).read(wav, 0, 3.)
                scene = SceneHistory(describe).read(wav, 0, 3.)
                measured = contrast_measurements(wav, 0, 3.)
                focus, = source['focused_windows']
                reading, = scene['focused_windows']
                observed, = measured['windows']
                self.assertLessEqual(reading['start_s'], focus['start_s'])
                self.assertGreaterEqual(reading['end_s'], focus['end_s'])
                self.assertEqual(reading['contrast_s'], [focus['start_s'], focus['end_s']])
                self.assertEqual(observed['start_s'], focus['start_s'])
                self.assertIsNone(observed['surrounding_rms_dbfs'])
                self.assertEqual(reading['channels'][0]['impression'], str(2 * self.rate))
                if previous is not None:
                    self.assertEqual((source, scene, measured), previous)
                previous = source, scene, measured

    def test_compact_view_includes_source_that_only_the_short_listen_found(self):
        from reel.mcp_server import first_view_report
        focus = {'start_s': 1, 'end_s': 1.5, 'channels': [{'channels': [1], 'name': 'tone'}]}
        report = dict(status='view', turn=1, start_s=0, end_s=4,
                      audio={'source_examples': {'windows': [], 'focused_windows': [focus]}})
        self.assertEqual(first_view_report(report)['audio']['source_examples']['focused_windows'], [focus])
