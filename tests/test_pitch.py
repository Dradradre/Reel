import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from reel.listen import inspect_sound
from reel.pitch import PitchTrace, periodic_hz


class PitchTests(unittest.TestCase):
    def test_period_does_not_follow_the_loudest_harmonic_or_name_noise(self):
        for rate in (8000, 44100):
            t = np.arange(round(rate * .05)) / rate
            # The fundamental is missing from the spectrum, but the sound still repeats at 175 Hz.
            signal = .6 * np.sin(2 * np.pi * 350 * t) + .4 * np.sin(2 * np.pi * 525 * t)
            self.assertAlmostEqual(periodic_hz(signal, rate), 175, delta=1)
            self.assertAlmostEqual(periodic_hz(np.sin(2 * np.pi * 1800 * t), rate), 1800, delta=12)
            self.assertAlmostEqual(periodic_hz(np.sin(2 * np.pi * 3100 * t), rate), 3100, delta=2)
            if rate == 44100:
                self.assertAlmostEqual(periodic_hz(np.sin(2 * np.pi * 12200 * t), rate), 12200, delta=2)
                self.assertIsNone(periodic_hz(np.sin(2 * np.pi * 21200 * t), rate))
            for unclear in [np.ones_like(t), np.random.default_rng(32).normal(size=len(t))]:
                self.assertIsNone(periodic_hz(unclear, rate))

    def test_pitch_windows_use_only_released_samples_and_survive_short_turns(self):
        rate = 12000
        t = np.arange(3 * rate // 10) / rate
        tone = np.sin(2 * np.pi * (210 * t + 800 * t**2))
        stereo = np.column_stack((tone, -tone, np.zeros_like(t)))
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "samples.wav"
            sf.write(wav, stereo, rate, subtype="FLOAT")
            full = PitchTrace().read(wav, 0)
            self.assertEqual(full["channels"][0]["channels"], [1, 2])
            expected = full["channels"][0]["hz"]
            self.assertEqual(len(expected), 6)
            self.assertEqual(expected, sorted(expected))
            history = PitchTrace()
            combined = []
            first = 0
            for stop in [1, 793, 971, 1589, len(stereo)]:
                sf.write(wav, stereo[first:stop], rate, subtype="FLOAT")
                view = history.read(wav, first)
                if view["window_count"]:
                    self.assertEqual(view["first_window_sample_on_film"], 600 * len(combined))
                    combined.extend(view["channels"][0]["hz"])
                first = stop
            self.assertEqual(combined, expected)
            sf.write(wav, stereo[732:3001], rate, subtype="FLOAT")
            crop = PitchTrace().read(wav, 732)
            self.assertEqual(crop["first_window_sample_on_film"], 1200)
            self.assertEqual(crop["channels"][0]["hz"], expected[2:5])

    def test_listening_from_a_saved_turn_keeps_its_exact_first_sample(self):
        rate, width, first = 11025, 551, 1653
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sf.write(folder / "sound.wav", np.sin(np.arange(width * 3) * 2 * np.pi * 310 / rate), rate)
            view = dict(schema="reel.play.v1", start_s=first / rate, end_s=(first + width * 3) / rate,
                        audio=dict(wav="sound.wav", first_sample_on_film=first, sample_count=width * 3,
                                   rate_hz=rate, channels=1))
            (folder / "view.json").write_text(json.dumps(view))
            with patch("reel.listen.AudioTagger", side_effect=OSError), patch("reel.listen.inspect_scene"):
                trace = inspect_sound(folder)["pitch_contour"]
            self.assertEqual((trace["first_window_sample_on_film"], trace["window_count"]), (first, 3))
