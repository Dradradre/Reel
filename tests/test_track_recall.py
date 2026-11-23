import unittest

import numpy as np

from examples.check_track_recall import score


class TrackRecallTests(unittest.TestCase):
    def test_constant_picture_guess_cancels_when_the_sound_changes(self):
        pairs, captions = [], {}
        vectors = dict(left=np.array([1., 0.]), right=np.array([0., 1.]))
        for ids in (["a", "b"], ["c", "d"]):
            rows = []
            for i, source in enumerate(ids):
                text = ["left", "right"][i]
                captions[source] = {key: [text, text] for key in ("audio_captions", "visual_captions")}
                for j, recording in enumerate(ids):
                    rows.append({"picture": source, "recording": recording, "responses": {
                        "picture_only": {"seen": text, "heard": text},
                        "both": {"seen": text, "heard": ["left", "right"][j]}}})
            pairs.append({"ids": ids, "assignments": rows})
        result = score(pairs, captions, vectors)["summary"]
        self.assertEqual(result["heard"]["picture_only"]["same_minus_paired_source"], 0.)
        self.assertEqual(result["heard"]["both"]["percent_of_held_out_human_gap"], 100.)
        self.assertEqual(result["seen"]["picture_only"]["same_minus_paired_source"], 1.)
        self.assertEqual(result["heard"]["picture_only"]["assignment_counts"]["tied"], 4)
        self.assertEqual(result["heard"]["both"]["assignment_counts"]["correct"], 4)
        pairs[0]["assignments"].pop()
        with self.assertRaisesRegex(ValueError, "four"):
            score(pairs, captions, vectors)
