import math
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from reel.asr import join_spelling_marks, matching_words
from reel.live_speech import LiveSpeech, SpeechReader, matching_gap_words
from reel.play import play


class LiveSpeechTests(unittest.TestCase):
    def test_timed_agreement_uses_spelling_and_the_occurrence_on_the_clock(self):
        def word(text, start):
            return dict(w=text, t=start, e=start + .2, p=.9)
        first = [word(text, i) for i, text in enumerate(["go", "go", "दिन", "দীন", "we're", "?", "Café."])]
        second = [word(text, i + 1) for i, text in enumerate(["GO!", "दीन", "দিন", "were", "!", "Cafe\u0301"])]
        self.assertEqual(list(matching_words(first, second)), [first[1], first[6]])
        channel = {"activity": [[0, 2]], "words": [word("अब", 0)]}
        self.assertEqual(matching_gap_words(channel, [word("दिन", 1)], "अब दीन"), [])
        self.assertEqual(matching_gap_words(channel, [word("दिन", 1)], "अब दिन"), [word("दिन", 1)])

    def test_decoder_joins_attached_spelling_marks_before_confidence_checks(self):
        def piece(text, start, end, p):
            return dict(w=text, t=start, e=end, p=p)
        pieces = [piece(" ส", .1, .2, .98), piece("ิ", .2, .2, .85), piece("่", .2, .3, .43),
                  piece("ง", .3, .4, .9), piece("้", .5, .6, .98)]
        grouped = join_spelling_marks(pieces)
        self.assertEqual(grouped, [piece("สิ่", .1, .3, .43), *pieces[3:]])
        self.assertEqual(list(matching_words(grouped, grouped)), grouped[:2])
        self.assertEqual(pieces[0]["w"], " ส")

    def test_original_channel_levels_between_detected_voice_intervals(self):
        rate = 44100
        start = 17.01
        end = start + .09999
        samples = np.zeros((4410, 3), dtype=np.float64)
        # The first detector interval starts on sample 441. The last gap starts
        # on sample 2208 (801 ticks at 16 kHz). Sample 2207 still overlaps voice.
        samples[441:1323] = .3
        samples[2207] = .4
        samples[2208, 1] = 1e-60
        with patch("reel.live_speech.speech_regions", return_value=[(160, 480), (640, 801)]), \
             patch("reel.live_speech.speech_words", return_value=[]):
            result = LiveSpeech().window(samples, rate, start, end, None)
        left, right = result["channels"]
        self.assertEqual(left["channels"], [1, 3])
        self.assertEqual(right["channels"], [2])
        self.assertEqual([g["signal"] for g in left["outside_speech_activity"]], ["digital_silence"] * 3)
        final = right["outside_speech_activity"][-1]
        self.assertEqual(final, {"start_s": start + 2208 / rate, "end_s": end,
                                 "signal": "nonzero", "peak_dbfs": -1200.0})

    def test_matching_words_need_full_spelling_voice_and_unoccupied_time(self):
        def word(text, start, end):
            return {"w": text, "t": start, "e": end, "p": .4}
        existing = word("uno", 2, 2.2)
        channel = {"activity": [[2, 3], [3.4, 3.8]], "words": [existing, word("ya", 3.4, 3.5)]}
        candidates = [word("un", 2.1, 2.3), word("dos", 2.3, 2.5), word("sí", 2.5, 2.8),
                      word("tres", 2.9, 3.2), word("y", 3.1, 3.2), word("fin", 3.5, 3.6),
                      word("fin", 3.6, 3.7)]
        possible = matching_gap_words(channel, candidates, "Uno, dos, si, tres y ya fin.")
        self.assertEqual([w["w"] for w in possible], ["dos", "tres", "fin"])
        self.assertEqual(possible[1]["e"], 3)
        self.assertEqual([w["w"] for w in channel["words"]], ["uno", "ya"])
        self.assertIs(channel["words"][0], existing)
        self.assertEqual(matching_gap_words(channel, [word("unclear", 3.7, 3.8)], "Unclear."), [])
        # A shifted existing word cannot use a single independent occurrence twice.
        self.assertEqual(matching_gap_words(channel, [word("uno", 2.8, 2.9)], "uno"), [])
        # Speech earlier cannot authorize words for a new vocal sound.
        channel = {"activity": [[0, 1], [2, 3]], "words": [word("hello", 0, .8)]}
        self.assertEqual(matching_gap_words(channel, [word("hey", 2.1, 2.4)], "hello hey"), [])
        channel["words"].clear()
        self.assertEqual(matching_gap_words(channel, [word("hey", 2.1, 2.4)], "hey"), [])

    def test_independent_reader_gets_only_the_released_channel_and_preserves_words_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            rate = 16000
            first = np.linspace(-.2, .2, rate, dtype=np.float32)
            sf.write(directory / "part.wav", np.column_stack((first, first, -first)), rate, subtype="FLOAT")
            view = {"schema": "reel.play.v1", "start_s": 5., "end_s": 6.,
                    "audio": {"wav": "part.wav", "first_sample_on_film": 5 * rate, "rate_hz": rate,
                              "sample_count": rate, "channels": 3},
                    "speech": {"status": "available", "windows": []}}
            (directory / "view.json").write_text(json.dumps(view))
            stable = [{"w": "가요", "t": .3, "e": .6, "p": .9}]
            proposed = [{"w": "곧", "t": 5.1, "e": 5.25, "p": .4},
                        {"w": "가요", "t": 5.3, "e": 5.6, "p": .9}]
            captures = []
            def hear(path):
                captures.append(path)
                np.testing.assert_array_equal(np.load(path), first if len(captures) == 1 else -first)
                return {"status": "available", "text": "곧 가요."} if len(captures) == 1 else {"status": "unavailable"}
            independent = Mock(failure=None)
            independent.transcribe.side_effect = hear
            with SpeechReader(independent) as reader, patch.object(reader, "request", side_effect=lambda job: LiveSpeech().read(**job)), \
                 patch("reel.live_speech.speech_regions", return_value=[(0, rate)]), \
                 patch("reel.live_speech.speech_words", return_value=stable), \
                 patch("reel.live_speech.transcribe", return_value=proposed):
                result = reader.inspect(directory)
            self.assertEqual(result["independent_word_check"], "unavailable")
            left, right = result["windows"][0]["channels"]
            self.assertEqual(left["channels"], [1, 2])
            self.assertEqual([w["w"] for w in left["words"]], ["가요"])
            self.assertEqual([w["w"] for w in left["possible_words"]], ["곧"])
            self.assertEqual([w["w"] for w in right["words"]], ["가요"])
            self.assertEqual(left["possible_words"][0]["t"], 5.1)
            self.assertNotIn("possible_words", right)
            self.assertTrue(all("_check" not in channel for channel in (left, right)))
            self.assertNotIn(".npy", json.dumps(result))
            self.assertTrue(all(not path.exists() for path in captures))

    def test_standalone_speech_worker_needs_no_image_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "PIL.py").write_text("raise ImportError('image library unavailable')\n")
            wav = root / "silence.wav"
            sf.write(wav, np.zeros(1600), 16000)
            request = dict(wav=str(wav), rate=16000, channels=1, first_sample=0, end=.1)
            script = Path(__file__).resolve().parents[1] / "reel" / "live_speech.py"
            worker = subprocess.run([sys.executable, str(script)], cwd=root, input=json.dumps(request) + "\n",
                                    env={**os.environ, "PYTHONPATH": str(root)}, text=True, capture_output=True,
                                    check=True)
            result = json.loads(worker.stdout)
            self.assertEqual(result["status"], "available")
            self.assertEqual(result["windows"][0]["channels"][0]["words"], [])

    def test_channels_activity_and_words_are_separate(self):
        rate = 16000
        left = np.ones(rate, np.float32)
        samples = np.stack([left, -left, left, np.zeros_like(left)], axis=1)
        # Independent channel polarity and a distinct voice disposition must survive.
        def recognize(y, regions, lang, min_prob, **kwargs):
            if not regions or y[0] < 0:
                return []
            return [{"t": .1, "e": 1.01, "w": "here", "p": .85}]

        with patch("reel.live_speech.speech_regions", return_value=[(0, rate)]), \
             patch("reel.live_speech.speech_words", side_effect=recognize):
            out = LiveSpeech().window(samples, rate, 3, 4, None)
        voiced, uncertain, quiet = out["channels"]
        self.assertEqual(voiced["channels"], [1, 3])
        self.assertEqual(voiced["words"], [{"t": 3.1, "e": 4, "w": "here", "p": .85}])
        self.assertEqual(uncertain, {"channels": [2], "activity": [[3., 4]], "words": []})
        self.assertEqual(quiet, {"channels": [4], "activity": [], "words": []})

    def test_request_uses_only_contiguous_released_audio_on_native_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            rate = 11025
            samples = (np.arange(12 * rate, dtype=np.int32) % 30000).astype(np.int16)
            source = root / "sound.wav"
            sf.write(source, samples, rate, subtype="PCM_16")
            paths = list(play(source, root / "view", until=11.055, step=1.317))
            middle = paths[-2].parent
            packet = json.loads((middle / "view.json").read_text())
            self.assertEqual(packet["speech"]["status"], "on_request")
            captured = []
            worker = LiveSpeech()
            def capture(data, sr, start, end, lang):
                captured.append((start, data.copy()))
                return {}
            with SpeechReader() as reader, patch.object(reader, "request", side_effect=lambda args: worker.read(**args)), \
                 patch.object(worker, "window", side_effect=capture):
                reader.inspect(middle)
                start, data = captured[-1]
                i = round(start * rate)
                np.testing.assert_array_equal(data[:, 0], samples[i:i + 8 * rate] / 32768)
                self.assertLessEqual(start + (len(data) - 1) / rate, packet["end_s"])
                copied = root / "isolated"
                copied.mkdir()
                for name in ("sound.wav", "view.json"):
                    (copied / name).write_bytes((middle / name).read_bytes())
                reader.inspect(copied)
                self.assertAlmostEqual(captured[-1][0], packet["audio"]["first_sample_on_film"] / rate)

    def test_exact_speech_replay_excludes_neighbours_and_preserves_live_history(self):
        rate = 11025
        signal = np.arange(3 * rate, dtype=np.int16).reshape(-1, 1)
        samples = np.column_stack((signal, -signal))
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "recording.wav"
            sf.write(source, samples, rate)
            paths = list(play(source, Path(tmp) / "view", step=1, continuous=True, recording_only=True))
            worker = LiveSpeech()
            captured = []
            def window(audio, sr, start, end, lang):
                captured.append((start, end, audio.copy()))
                return {}
            with SpeechReader() as reader, patch.object(reader, "request", side_effect=lambda job: worker.read(**job)), \
                 patch.object(worker, "window", side_effect=window):
                for path in paths[:2]:
                    reader.read(path.parent, json.loads(path.read_text()), "en")
                saved = worker.history.copy()
                start, end = .73013, 1.33529
                replay = reader.inspect(paths[1].parent, "ko", start_s=start, end_s=end)
                self.assertEqual(replay["language_hint"], "ko")
                first, last = math.ceil(start * rate), math.ceil(end * rate)
                self.assertEqual(captured[-1][:2], (first / rate, end))
                np.testing.assert_array_equal(captured[-1][2], samples[first:last] / 32768)
                np.testing.assert_array_equal(worker.history, saved)
                self.assertEqual(worker.next_sample, 2 * rate)
                reader.read(paths[2].parent, json.loads(paths[2].read_text()), "en")
                np.testing.assert_array_equal(captured[-1][2], samples / 32768)
                for a, b in ((0, None), (None, 1), (-1, 1), (1, 1), (.5, 2.01), (0, 8.01)):
                    with self.subTest(bounds=(a, b)), self.assertRaises(ValueError):
                        reader.inspect(paths[1].parent, start_s=a, end_s=b)

    def test_archived_transcript_can_be_read_without_a_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = {"status": "available", "language_hint": "ko", "windows": []}
            packet = {"schema": "reel.play.v1", "start_s": 0, "end_s": 1,
                      "audio": {"wav": "sound.wav", "rate_hz": 8000, "channels": 1, "first_sample_on_film": 0},
                      "speech": saved}
            (root / "view.json").write_text(json.dumps(packet))
            with SpeechReader() as reader, patch.object(reader, "request", return_value={"status": "unavailable"}) as run:
                self.assertEqual(reader.inspect(root, "ko"), saved)
                run.assert_not_called()
                self.assertEqual(reader.inspect(root, "en")["status"], "unavailable")
                self.assertEqual(run.call_args.args[0]["lang"], "en")

    def test_rechecking_an_isolated_archive_preserves_its_longer_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = {"status": "available", "windows": [{"start_s": 1., "end_s": 3., "channels": []}]}
            packet = {"schema": "reel.play.v1", "start_s": 2., "end_s": 3.,
                      "audio": {"wav": "sound.wav", "rate_hz": 16000, "first_sample_on_film": 32000,
                                "channels": 1}, "speech": saved}
            (root / "view.json").write_text(json.dumps(packet))
            with SpeechReader(Mock(failure=None)) as reader, patch.object(reader, "request") as request:
                self.assertEqual(reader.inspect(root), saved)
                request.assert_not_called()

    def test_no_helper_does_not_stop_interactive_playback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "sound.wav"
            sf.write(wav, np.zeros(8000), 8000)
            result = subprocess.run([sys.executable, "-m", "reel.cli", "play", str(wav), "--out",
                                     str(root / "play"), "--step", ".5"], input="speech\nnext\nquit\n",
                                    env={**os.environ, "REEL_ASR_PYTHON": str(root / "missing")},
                                    text=True, capture_output=True, check=True)
            first, speech, last = result.stdout.splitlines()
            self.assertEqual(json.loads(speech)["status"], "unavailable")
            self.assertEqual(json.loads(Path(first).read_text())["end_s"], .5)
            self.assertEqual(json.loads(Path(last).read_text())["end_s"], 1.)
