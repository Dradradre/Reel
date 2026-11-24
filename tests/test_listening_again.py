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

    def test_question_control_withholds_repeated_answer_and_preserves_channels(self):
        pcm = np.ones((8000, 4), np.float32)
        pcm[:, 1] = -.5
        pcm[:, 3] = 0
        heard = []

        def answer(signal, question):
            heard.append((signal.copy(), question))
            return "ＹＥＳ！" if signal[0] > 0 else "no" if signal[0] < 0 else "Yes."

        with patch("reel.hearing.load_encoder_audio", return_value=pcm):
            report = ListeningHistory(answer).read("original.wav", 4, 4.5, False, question="Was it audible?",
                                                   question_control=True)
        a, b, silent = report["windows"][0]["channels"]
        self.assertEqual((a["channels"], a["answer_on_silence"]), ([1, 3], "unchanged"))
        self.assertNotIn("answer", a)
        self.assertEqual((b["answer"], b["answer_on_silence"]), ("no", "different"))
        self.assertEqual(silent, {"channels": [4], "digital_silence": True})
        self.assertEqual(len(heard), 3)
        self.assertEqual(len(set(prompt for _, prompt in heard)), 1)
        np.testing.assert_array_equal(heard[1][0], np.zeros(8000))
        with self.assertRaisesRegex(ValueError, "requires a listening question"):
            ListeningHistory(answer).read("absent.wav", 4, 4.5, False, question_control=True)

    def test_question_control_mutes_both_recordings_for_a_focused_comparison(self):
        from unittest.mock import Mock
        ear = Mock()
        ear.compare.side_effect = ["short rattle", "unknown"]
        pcm = np.ones((1600, 1), np.float32)
        with patch("reel.hearing.load_encoder_audio", side_effect=[pcm, pcm / 2]):
            report = ListeningHistory(ear).read("focus", 2, 2.1, False, question="What changed?",
                                                mix_wav="mix", component="mid", question_control=True)
        raw, silence = [c.args for c in ear.compare.call_args_list]
        np.testing.assert_array_equal(raw[0], pcm[:, 0] / 2)
        np.testing.assert_array_equal(raw[1], pcm[:, 0])
        self.assertTrue(all(not np.any(channel) for channel in silence[:2]))
        self.assertEqual(raw[2:], silence[2:])
        self.assertEqual(report["windows"][0]["channels"][0]["answer"], "short rattle")

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
