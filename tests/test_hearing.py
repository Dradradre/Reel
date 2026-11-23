import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from reel.hearing import LIVE_CONTEXT_S, RATE, ListeningHistory, SoundReader, spectrum_profile


class WaveformTests(unittest.TestCase):
    def test_question_and_picture_partitions_keep_the_whole_waveform_available(self):
        ear = SoundReader()
        sound = {"status": "available", "windows": []}
        complete = {"start_s": 3., "end_s": 7., "channels": [{"status": "read", "channels": [1], "audio_only": "sound"}]}
        def responses(job):
            if job.get("operation") == "joint":
                return {"status": "available", "windows": windows}
            self.assertEqual((job["start"], job["end"], job["remember"]), (3., 7., False))
            return sound
        with patch.object(ear, "request", side_effect=responses) as worker:
            for windows in ([complete], [{**complete, "end_s": 4.}, {**complete, "start_s": 4.}],
                            [{**complete, "start_s": 4.}], [{**complete, "end_s": 6.}]):
                result = ear.joint("clip.wav", 3, 7, 48000, ["pictures"], (32, 32), 7)
                if windows == [complete]:
                    self.assertEqual(worker.call_count, 1)
                    self.assertEqual(result["recording"]["windows"][0]["channels"],
                                     [{"channels": [1], "impression": "sound", "digital_silence": False}])
                else:
                    self.assertEqual(result["recording"], sound)
            windows = [complete]
            answer = ear.joint("clip.wav", 3, 7, 48000, ["pictures"], (32, 32), 7, "What changed?")
            self.assertEqual(answer["recording"], sound)
            self.assertEqual(worker.call_args.args[0]["question"], "What changed?")
            worker.reset_mock()
            tail = ear.joint("clip.wav", 3, 7, 48000, [], None, 3, "What continued?")
            self.assertEqual((tail["status"], tail["recording"]), ("no_simultaneous_picture", sound))
            self.assertEqual(worker.call_count, 1)

    def test_short_released_sound_retains_signal_without_an_encoder_impression(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "short.wav"
            sf.write(wav, np.full(160, .25, np.float32), RATE, subtype="FLOAT")
            report = ListeningHistory(lambda *args: self.fail("not enough samples")).read(wav, 0, .01, False)
        row, = report["windows"][0]["channels"]
        self.assertFalse(row["digital_silence"])
        self.assertEqual(row["status"], "insufficient_encoder_input")
        self.assertEqual(row["measured_profile"][0]["rms_dbfs"], -12.)

    def test_a_stopped_channel_does_not_repeat_its_previous_impression(self):
        loud = np.ones(RATE, dtype=np.float32)
        quiet = np.zeros_like(loud)
        sounds = [np.column_stack(pair) for pair in
                  ((loud, loud), (quiet, loud), (quiet, quiet), (loud, quiet))]
        heard = []
        def describe(channel):
            heard.append(channel.copy())
            return "recorded sound"
        reader = ListeningHistory(describe)
        with patch('reel.hearing.load_encoder_audio', side_effect=sounds):
            for i in range(4):
                window, = reader.read('wav', i, i + 1, True)['windows']
                if i == 1:
                    stopped, ongoing = window['channels']
                    self.assertEqual(window['current_start_s'], 1)
                    self.assertEqual(stopped['current_signal'], 'digital_silence')
                    self.assertNotIn('impression', stopped)
                    self.assertEqual(ongoing['current_signal'], 'nonzero')
                if i == 2:
                    self.assertEqual((window['start_s'], window['end_s']), (2, 3))
                    self.assertEqual(window['channels'], [{'channels': [1, 2], 'digital_silence': True}])
                if i == 3:
                    self.assertEqual((window['start_s'], window['current_start_s']), (0, 3))
        self.assertEqual(len(heard), 3)
        np.testing.assert_array_equal(heard[-1], np.concatenate([s[:, 0] for s in sounds]))

    def test_local_ear_keeps_channels_level_and_only_contiguous_history(self):
        heard = []
        def describe(channel):
            heard.append(channel.copy())
            return "sound"

        ear = ListeningHistory(describe)
        first = np.tile(np.array([.02, .02, -.02, 0], dtype=np.float32), (RATE, 1))
        second = first * 2
        with patch('reel.hearing.load_encoder_audio', side_effect=[first, second, second, first]):
            initial = ear.read('a', 2, 3, True)['windows'][0]
            continued = ear.read('b', 3, 4, True)['windows'][0]
            reset = ear.read('c', 9, 10, True)['windows'][0]
            bounded = ear.read('d', 10, 11, False)['windows'][0]
        self.assertEqual([(w['start_s'], w['end_s']) for w in [initial, continued, reset, bounded]],
                         [(2, 3), (2, 4), (9, 10), (10, 11)])
        self.assertEqual([c['channels'] for c in initial['channels']], [[1, 2], [3], [4]])
        self.assertEqual(initial['channels'][-1], {'channels': [4], 'digital_silence': True})
        self.assertEqual(len(heard), 8)
        np.testing.assert_array_equal(heard[0], first[:, 0])
        np.testing.assert_array_equal(heard[1], first[:, 2])
        np.testing.assert_array_equal(heard[2], np.concatenate([first[:, 0], second[:, 0]]))
        np.testing.assert_array_equal(heard[4], second[:, 0])

    def test_long_queries_cover_tail_and_playback_discards_old_samples(self):
        ear = ListeningHistory(lambda channel: str(len(channel)))
        with patch('reel.hearing.load_encoder_audio', return_value=np.zeros((31 * RATE, 1), dtype=np.float32)):
            windows = ear.read('file', 12, 43, False)['windows']
        self.assertEqual([(w['start_s'], w['end_s']) for w in windows], [(12, 27.5), (27.5, 43)])
        with patch('reel.hearing.load_encoder_audio', return_value=np.ones((5 * RATE, 1), dtype=np.float32)):
            ear.read('a', 0, 5, True)
            window, = ear.read('b', 5, 10, True)['windows']
        self.assertEqual(window['start_s'], 10 - LIVE_CONTEXT_S)
        self.assertEqual(int(window['channels'][0]['impression']), LIVE_CONTEXT_S * RATE)

    def test_profile_distinguishes_concentrated_and_broadband_power(self):
        t = np.arange(RATE) / RATE
        rows = spectrum_profile(.01 * np.sin(2 * np.pi * 750 * t), 11, 12)
        self.assertEqual((rows[0]['start_s'], rows[-1]['end_s']), (11, 12))
        for row in rows:
            self.assertEqual(row['band_center_hz'], 750)
            self.assertGreater(row['band_power_fraction'], .99)
        noise = np.random.default_rng(41).normal(0, .01, len(t))
        self.assertTrue(all(r['band_power_fraction'] < .04 for r in spectrum_profile(noise, 0, 1)))

    def test_missing_helper_returns_an_explicit_unavailable_result(self):
        with tempfile.TemporaryDirectory() as tmp, patch('reel.hearing.HEAR_PY', str(Path(tmp) / 'missing')):
            with SoundReader() as reader:
                result = reader.read('recording.wav', 0, 1)
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(reader.read('other.wav', 1, 2), result)
