from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import soundfile as sf

from reel.source_examples import SourceHistory, SourceIndex


class ReferenceSourceTests(unittest.TestCase):
    def test_sound_and_name_need_support_from_separate_reference_recordings(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.npz"
            names = ["first", "second", "outside"]
            text = np.eye(3)
            vectors = np.array([text[0]] * 3 + [text[1]] * 3)
            np.savez(path, names=names, text=text, name_ids=[0, 0, 0, 1, 1, 1], vectors=vectors)
            index = SourceIndex(path)
            self.assertEqual(index.identify(text[1])["name"], "second")
            self.assertIsNone(index.identify(text[2]))
            self.assertIsNone(index.identify(np.zeros(3)))
            self.assertIsNone(index.identify(np.array([1., 1., 0.])))
            self.assertIsNone(index.match(np.array([.09, .02, 0.]), np.array([.70, .25, -np.inf])))
            self.assertIsNone(index.match(np.array([.2, .15, 0.]), np.array([.22, .15, -np.inf])))
            self.assertIsNone(index.match(np.array([.2, .2, 0.]), np.array([.6, .3, -np.inf])))

    def test_new_excerpt_name_needs_agreement_across_three_rankings(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.npz"
            names = ["bell", "water", "plane", "cloth", "door", "unknown"]
            np.savez(path, names=names, text=np.eye(6), vectors=np.eye(6), name_ids=np.arange(6))
            index = SourceIndex(path)
            words = np.array([.32, .33, .23, .11, .10, .01])
            refs = np.array([.79, .29, .41, .55, .35, -np.inf])
            clap = np.array([.55, .11, .20, .26, .30, .29])
            self.assertEqual(index.corroborated(words, refs, clap)["name"], "bell")
            for competing in (words, np.zeros(6), np.full(6, np.nan)):
                self.assertIsNone(index.corroborated(competing, competing, competing))
            self.assertIsNone(index.corroborated(words, refs, clap[::-1]))
            self.assertIsNone(index.corroborated(words, np.full(6, -np.inf), clap))
            self.assertIsNone(index.corroborated(words, refs * .5, clap))

    def test_cutoff_layout_silence_and_discontinuous_replay(self):
        rate = 8000
        calls = []
        def identify(signal, hz):
            calls.append(signal.copy())
            return {"name": "test source"}
        history = SourceHistory(identify)
        with TemporaryDirectory() as tmp:
            wav = Path(tmp) / "chunk.wav"
            data = np.zeros((rate * 2, 3), np.float32)
            data[:rate, :2] = .1
            data[rate:, 2] = .9
            sf.write(wav, data, rate, subtype="FLOAT")
            initial = history.read(wav, 0, 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(initial["windows"][0]["channels"], [{"channels": [1, 2], "name": "test source"}])
            self.assertEqual(len(calls[0]), rate)
            self.assertLess(calls[0].max(), .2)
            sf.write(wav, np.zeros((rate, 3)), rate)
            self.assertFalse(history.read(wav, rate, 2)["windows"])
            self.assertEqual(len(calls), 1)
            sf.write(wav, np.full(rate, .5), rate)
            later = history.read(wav, rate * 6, 7)["windows"][0]
            self.assertEqual((later["start_s"], later["end_s"], len(calls[-1])), (6., 7., rate))
            self.assertTrue(np.allclose(calls[-1], .5))

    def test_long_turns_have_bounded_context_and_skip_unheard_samples(self):
        rate = 8000
        with TemporaryDirectory() as tmp:
            wav = Path(tmp) / "chunk.wav"
            sf.write(wav, np.full(10 * rate, .5), rate)
            calls = []
            def identify(signal, hz):
                calls.append(len(signal))
                return {"name": "test source"}
            windows = SourceHistory(identify).read(wav, 0, 5.5)["windows"]
            self.assertEqual(calls, [4 * rate, 4 * rate])
            self.assertEqual([(w['start_s'], w['end_s']) for w in windows], [(0., 4.), (1.5, 5.5)])
