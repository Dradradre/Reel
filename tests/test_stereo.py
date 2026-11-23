import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import numpy as np
import soundfile as sf

from reel.common import sample_boundary
from reel.play import play
from reel.stereo import channel_pairs, pair_cues, StereoHistory


class StereoTests(unittest.TestCase):
    def test_waveform_lead_and_level_have_independent_signs(self):
        rate = 48000
        waveform = np.random.default_rng(3).normal(0, .05, rate // 4)
        delayed = np.pad(waveform[:-24], (24, 0))
        samples = np.column_stack([waveform, -2 * delayed])
        cue = pair_cues(samples, rate)
        self.assertEqual(cue['second_minus_first_us'], 500)
        self.assertAlmostEqual(cue['second_minus_first_db'], 6.0, delta=.1)
        self.assertEqual(cue['correlation'], -1)
        reverse = pair_cues(samples[:, ::-1], rate)
        self.assertEqual(reverse['second_minus_first_us'], -500)
        self.assertEqual(pair_cues(np.column_stack([waveform, waveform]), rate)['second_minus_first_us'], 0)

    def test_ambiguous_or_missing_waveforms_have_no_time_difference(self):
        rate = 48000
        rng = np.random.default_rng(83)
        noise = rng.normal(0, .1, (rate // 4, 2))
        tone = np.sin(np.arange(rate // 4) * (2 * np.pi * 3000 / rate))
        for samples, status in [(noise, 'no consistent lag'), (np.column_stack([tone, tone]), 'no consistent lag'),
                                (noise[:15], 'too short'), (noise * [1, 0], 'one active channel'),
                                (noise * 0, 'digital silence')]:
            with self.subTest(status=status):
                cue = pair_cues(samples, rate)
                self.assertEqual(cue['timing'], status)
                self.assertIsNone(cue['second_minus_first_us'])

    def test_layout_uses_known_pairs(self):
        self.assertEqual(channel_pairs(1, 'mono'), [])
        self.assertEqual(channel_pairs(2, '')[0]['speakers'], None)
        self.assertEqual(channel_pairs(4, 'unknown'), [])
        self.assertEqual([p['channels'] for p in channel_pairs(6, '5.1(side)')], [[1, 2], [5, 6]])
        self.assertEqual(channel_pairs(4, '4 channels (FL+FR+FLC+FRC)')[-1]['channels'], [3, 4])

    def test_native_64_bit_integer_recording_remains_playable(self):
        rate = 16000
        left = np.random.default_rng(5).integers(-(2**55), 2**55, rate // 4, dtype=np.int64)
        raw = np.column_stack([left, np.roll(left, 8)]).astype('<i8').tobytes()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / 'high-resolution.wav'
            fmt = struct.pack('<HHIIHH', 1, 2, rate, rate * 16, 16, 64)
            wav.write_bytes(b'RIFF' + struct.pack('<I', len(raw) + 36) + b'WAVEfmt ' + struct.pack('<I', 16)
                            + fmt + b'data' + struct.pack('<I', len(raw)) + raw)
            path, = play(wav, root / 'read')
            audio = json.loads(path.read_text())['audio']
            self.assertEqual(audio['stereo_cues']['windows'][0]['pairs'][0]['second_minus_first_us'], 500.)
            kept = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path.parent / audio['wav']),
                                            '-map', '0:a:0', '-c:a', 'copy', '-f', 'data', '-'])
            self.assertEqual(kept, raw)

    def test_fractional_pauses_and_unreleased_sound(self):
        rate = 11025
        rng = np.random.default_rng(11)
        left = rng.normal(0, .1, rate)
        right = np.roll(left, 5)
        samples = np.column_stack([left, right])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / 'sound.wav'
            sf.write(wav, samples, rate, subtype='FLOAT')
            stop = .70893
            expected = StereoHistory().read(wav, 0, stop)['windows']
            boundaries = [0, .0411, .253, .5999, stop]
            history = StereoHistory()
            bins = {}
            for start, end in zip(boundaries, boundaries[1:]):
                path = root / 'part.wav'
                sf.write(path, samples[sample_boundary(start, rate):sample_boundary(end, rate)], rate, subtype='FLOAT')
                for w in history.read(path, start, end)['windows']:
                    self.assertLessEqual(w['end_s'], end)
                    bins[w['start_s']] = w
            self.assertEqual(list(bins.values()), expected)
            samples[sample_boundary(stop, rate):] = 0
            sf.write(wav, samples, rate, subtype='FLOAT')
            self.assertEqual(StereoHistory().read(wav, 0, stop)['windows'], expected)
            manifests = list(play(wav, root / 'turns', step=.137, until=stop))
            published = {}
            for path in manifests:
                view = json.loads(path.read_text())
                for w in view['audio']['stereo_cues']['windows']:
                    self.assertLessEqual(w['end_s'], view['end_s'])
                    published[w['start_s']] = w
            self.assertEqual(list(published.values()), expected)
