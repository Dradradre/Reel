import unittest

import numpy as np

from reel.memory import earlier_matches, match_note, prefer_identical_previews
from reel.perceive import aligned_voice_matches, exact_repeats, memory_voice


class MemoryTests(unittest.TestCase):
    def test_block_search_matches_dense_search_across_cuts_and_gaps(self):
        rng = np.random.default_rng(17)
        patterns = np.eye(12, dtype=np.float32)[rng.integers(12, size=350)]
        times = np.cumsum(rng.integers(1, 4, len(patterns))) * .25
        shots = np.arange(len(times)) // 9
        valid = rng.random(len(times)) > .2
        expected = []
        for i in range(len(times)):
            eligible = valid & (times < times[i] - 1) & (shots != shots[i])
            scores = np.where(eligible, patterns @ patterns[i], -1)
            expected.append(scores.argmax() if valid[i] and scores.max() > .8 else -1)
        idx, score = earlier_matches(patterns, times, valid, 1, .8, shots)
        np.testing.assert_array_equal(expected, idx)
        np.testing.assert_array_equal(idx >= 0, score > .8)

    def test_only_verified_colour_matches_take_precedence(self):
        a = np.zeros((3, 3, 3), np.uint8)
        a[0, 0] = [255, 128, 0]
        b = a.copy()
        b[0, 0] = [0, 128, 255]
        blank = np.zeros_like(a)
        images = [a, b, b, a, blank, blank]
        times = np.arange(6, dtype=float)
        shots = np.arange(6)
        idx = np.array([-1, -1, 0, 1, -1, -1])
        scores = np.where(idx >= 0, .98, 0.)
        exact = prefer_identical_previews(images, times, shots, idx, scores)
        np.testing.assert_array_equal(exact, [False, False, False, True, False, False])
        self.assertEqual(idx[3], 0)
        self.assertEqual(idx[2], 0)  # Its identical predecessor is only one second back.
        memory = dict(t=times, src=np.where(idx >= 0, times[idx], np.nan), score=scores, same_pixels=exact)
        self.assertEqual(match_note(memory, 2, 4, 'layout'), 'same preview at 3.000s / earlier 0.000s')
        self.assertEqual(match_note(memory, 2, 3, 'layout'), 'layout at 2.000s / earlier 0.000s')

    def test_matched_voice_windows_share_the_center_clock(self):
        rng = np.random.default_rng(22)
        db = rng.normal(size=(40, 650)).astype(np.float32)
        db[:, 450:600] = db[:, 20:170] + 6  # Same spectral pattern after a gain change.
        vo = dict(vdb=db, vrms=np.full(650, -20))
        memory = memory_voice(vo)
        matched = (memory['t'] >= 4.75) & (memory['t'] <= 5.75)
        np.testing.assert_allclose(memory['t'][matched] - memory['src'][matched], 4.3)
        self.assertTrue(aligned_voice_matches(memory))
        shuffled = dict(t=np.arange(6) * .1 + 6, src=np.array([.2, 1.1, .9, 1.4, .3, 1.6]), score=np.ones(6))
        self.assertFalse(aligned_voice_matches(shuffled))
        self.assertEqual(len(memory_voice(dict(vdb=db[:, :35], vrms=np.zeros(35)))['src']), 0)

    def test_waveform_repetition_survives_different_sample_values(self):
        rng = np.random.default_rng(3)
        wave = rng.normal(0, .1, 56000).astype(np.float32)
        wave[32000:40000] = wave[16000:24000] * .45
        self.assertTrue(any(abs(lag - 2.) < .01 and t <= 4.1 and t + duration >= 4.8
                            for t, duration, lag, _ in exact_repeats(wave)))
