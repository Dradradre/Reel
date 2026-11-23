import unittest

import numpy as np

from examples.check_caption_streams import weighted_stream


class CaptionStreamTests(unittest.TestCase):
    def test_overlapping_context_and_shared_channels_do_not_add_listening_time(self):
        vectors = {"tap": np.array([1., 0.]), "hum": np.array([0., 1.])}
        first = {"start_sample": 0, "first_released_sample": 0, "end_sample": 8,
                 "channels": [{"channels": [1, 2], "caption": "tap"}]}
        last = {"start_sample": 6, "first_released_sample": 8, "end_sample": 9,
                "channels": [{"channels": [2], "caption": "hum"}]}
        film = {"frames": 9, "channels": 2, "windows": [first, last]}
        np.testing.assert_allclose(weighted_stream(film, "caption", vectors), [16 / 18, 1 / 18])
        last["channels"].append({"channels": [1, 2], "caption": "hum"})
        with self.assertRaisesRegex(ValueError, "only once"):
            weighted_stream(film, "caption", vectors)
        with self.assertRaisesRegex(ValueError, "complete"):
            weighted_stream({**film, "windows": [first]}, "caption", vectors)
