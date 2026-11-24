import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import soundfile as sf

from reel.onsets import OnsetTrace, recorded_attacks
from reel.play import play


class AttackTimingTests(unittest.TestCase):
    rate = 16000

    def recording(self):
        samples = np.zeros((self.rate, 2))
        burst = np.random.default_rng(451).normal(0, .15, 340) * np.exp(-np.arange(340) / 110)
        for second, channels in ((.17, [0]), (.25, [0, 1]), (.33, [1]), (.54, [0]), (.70, [1])):
            first = round(second * self.rate)
            for ch in channels:
                samples[first:first + len(burst), ch] = (-1) ** ch * burst
        return samples

    def test_chunk_boundaries_and_cutoff(self):
        samples = self.recording()
        expected = OnsetTrace().scan(samples, self.rate, 0)
        self.assertEqual([r['channels'] for r in expected], [[1], [1, 2], [2], [1], [2]])
        np.testing.assert_allclose([r['time_s'] for r in expected], [.17, .25, .33, .54, .70], atol=.02)
        trace, collected = OnsetTrace(), []
        for first in range(0, len(samples), 437):
            stop = min(first + 437, len(samples))
            collected.extend(trace.scan(samples[first:stop], self.rate, first))
            due = [e for e in expected if round((e['time_s'] + .04) * self.rate) <= stop]
            self.assertEqual(collected, due)
        self.assertEqual(expected, collected)

    def test_past_audio_cannot_cross_a_gap_or_rate_or_layout_change(self):
        samples = self.recording()
        for first, rate, channels in ((10000, self.rate, 2), (3000, 8000, 2), (3000, self.rate, 1)):
            trace = OnsetTrace()
            trace.scan(samples[:3000], self.rate, 0)
            self.assertEqual(trace.scan(np.zeros((2000, channels)), rate, first), [])
        t = np.arange(self.rate) / self.rate
        tone = np.sin(2 * np.pi * 220 * t)
        self.assertEqual(OnsetTrace().scan(tone[:, None], self.rate, 0), [])

    def test_default_play_returns_only_confirmed_attacks_and_replay_uses_its_own_samples(self):
        samples = self.recording()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / 'track.wav'
            sf.write(wav, samples, self.rate, subtype='FLOAT')
            reports = [json.loads(f.read_text())['audio']['attacks'] for f in
                       play(wav, root / 'view', until=.35, step=.087, continuous=True, recording_only=True)]
            self.assertTrue(all(r['confirmation_delay_s'] == .04 for r in reports))
            attacks = [e for r in reports for e in r['events']]
            self.assertEqual([e['channels'] for e in attacks], [[1], [1, 2]])
            crop = root / 'replay.wav'
            sf.write(crop, samples[6400:9600], self.rate, subtype='FLOAT')
            replay = recorded_attacks(crop, 6400)['events']
            self.assertEqual(len(replay), 1)
            self.assertAlmostEqual(replay[0]['time_s'], .54, delta=.02)
