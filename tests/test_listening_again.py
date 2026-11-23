import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from reel.hearing import ListeningHistory
from reel.play import play
from reel.turn_audio import released_recording


class ListeningAgainTests(unittest.TestCase):
    def test_source_samples_survive_many_pauses_and_an_earlier_subrange(self):
        rate = 44100
        # This duration is rounded upwards by the decimal duration field in ffprobe.
        frames = 129473
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "my sound.wav"
            a = np.arange(frames, dtype=np.int16)
            original = np.column_stack([a, -a])
            sf.write(src, original, rate, subtype="PCM_16")
            turns = list(play(src, root / "session", step=.371))
            last = turns[-1].parent
            wav, _, end = released_recording(last, 0, None, 30)
            actual, sr = sf.read(wav, dtype="int16", always_2d=True)
            self.assertEqual(sr, rate)
            self.assertEqual(end, frames / rate)
            np.testing.assert_array_equal(actual, original)
            wav, _, _ = released_recording(last, .12345, .98765, 30)
            actual, _ = sf.read(wav, dtype="int16", always_2d=True)
            np.testing.assert_array_equal(actual, original[5445:43556])
            with self.assertRaises(ValueError):
                released_recording(turns[0].parent, 0, end, 30)
            removed = turns[-2].parent / "sound.wav"
            removed.unlink()
            with self.assertRaisesRegex(ValueError, "contiguous"):
                released_recording(last, 0, None, 30)
            wav, start, _ = released_recording(last, None, None, 30)
            self.assertEqual(wav, last / "sound.wav")
            self.assertEqual(start, json.loads(turns[-1].read_text())["start_s"])

    def test_questions_use_the_waveform_on_each_distinct_channel(self):
        signal = np.zeros((16000, 3), dtype=np.float32)
        signal[:, 1:] = .25
        def hear(channel, question):
            np.testing.assert_array_equal(channel, signal[:, 1])
            self.assertIn("film time 4.000000 s and ends at 5.000000 s", question)
            self.assertTrue(question.endswith("Listener question: WHAT changed?"))
            return "steady"
        with patch("reel.hearing.load_encoder_audio", return_value=signal):
            v = ListeningHistory(hear).read("bounded.wav", 4, 5, remember=False, question="WHAT changed?")
        self.assertEqual(v["question"], "WHAT changed?")
        left, right = v["windows"][0]["channels"]
        self.assertTrue(left["digital_silence"])
        self.assertNotIn("answer", left)
        self.assertEqual(right["channels"], [2, 3])
        self.assertEqual(right["answer"], "steady")

    def test_each_audio_question_keeps_the_film_time_of_its_own_window(self):
        signal = np.full((32000, 1), .15, dtype=np.float32)
        questions = []
        def hear(channel, question):
            questions.append(question)
            return "steady"

        with patch("reel.hearing.load_encoder_audio", return_value=signal), patch("reel.hearing.WINDOW_S", 1):
            reply = ListeningHistory(hear).read("bounded.wav", 21.5, 23.5, remember=False, question="At 23 s?")
        self.assertIn("film time 21.500000 s and ends at 22.500000 s", questions[0])
        self.assertIn("film time 22.500000 s and ends at 23.500000 s", questions[1])
        self.assertEqual(reply["question"], "At 23 s?")

    def test_play_supplies_the_replay_even_when_the_audio_model_is_missing(self):
        rate = 8000
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "example.wav"
            sf.write(src, np.zeros(rate), rate)
            result = subprocess.run([sys.executable, "-m", "reel.cli", "play", str(src), "--out",
                                     str(root / "play"), "--step", ".5"], input="listen What changed?\nnext\nquit\n",
                                    text=True, capture_output=True, check=True,
                                    env={**os.environ, "REEL_HEAR_PYTHON": str(root / "missing")})
            first, reread, second = result.stdout.splitlines()
            reply = json.loads(reread)
            self.assertEqual(reply["sound_descriptions"]["status"], "unavailable")
            self.assertEqual(sf.info(reply["wav"]).frames, rate // 2)
            self.assertEqual(Path(reply["wav"]).parent, Path(first).parent)
            self.assertEqual(json.loads(Path(second).read_text())["end_s"], 1.)
