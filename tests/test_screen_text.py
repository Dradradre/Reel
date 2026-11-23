import unittest
from unittest.mock import patch

from reel import screen_text


class ScreenTextTests(unittest.TestCase):
    def test_exposure_times_ties_and_exact_holds(self):
        frames = [(100, 10, b"a"), (100, 20, b"b"), (250, 0, b"b"), (450, 1, b"c"), (800, 10, b"d")]
        self.assertEqual(list(screen_text.unchanged_pictures(frames, 800)),
                         [(100, 450, b"b"), (450, 800, b"c")])
        self.assertEqual(list(screen_text.unchanged_pictures(frames, 500)), [(100, 450, b"b"), (450, 500, b"c")])
        self.assertEqual(list(screen_text.unchanged_pictures(frames, 100)), [])

    def test_text_follows_position_failure_and_exclusive_picture_end(self):
        line = dict(text="7", x=.05, y=.1, w=.1, h=.1, c=1.)
        right = dict(line, x=.75)
        shifted = dict(line, x=.09)
        tiny = dict(line, text="Ari", h=.02)
        weak = dict(line, text="unclear", c=.5)
        reads = [(100000, 300000, [line, right, tiny, weak]), (300000, 600000, [shifted, right]),
                 (600000, 800000, None), (800000, 1000000, [right]), (1000000, 1200000, []),
                 (1200000, 1300000, [right, tiny])]
        info = dict(display_aspect=9/16, w=1080, h=1920)
        messages = []
        with (patch.object(screen_text, "probe", return_value=info),
              patch.object(screen_text, "video_end", return_value=1.281),
              patch.object(screen_text, "read_pictures", return_value=reads) as decode):
            items, tags = screen_text.screen_intervals("film", 2., messages.append, subject=" ari ")
        decode.assert_called_once_with("film", (720, 1280), 1281000)
        self.assertEqual([(r["x"], r["t"], r["e"]) for r in items],
                         [(.05, .1, .6), (.75, .1, .6), (.75, .8, 1.), (.75, 1.2, 1.281)])
        self.assertEqual([tag["t"] for tag in tags], [.25, 1.25])
        self.assertTrue(any("unavailable for 1" in msg for msg in messages))
