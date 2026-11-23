import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
import librosa

from reel.asr import SR as SPEECH_RATE, corroborate_stem
from reel.listen import excerpt, inspect_sound, level_changes, listening_evidence
from reel.observe import observe
from reel.perceive import LEFT, Page, audio_pass, band_levels, build, draw_spectrum, mix_level_text
from reel.sounds import recorded_context


class AudioTests(unittest.TestCase):
    def test_context_uses_recorded_channels_and_keeps_its_time_window(self):
        class Tagger:
            labels = ["A", "Silence", "B"]
            def scores(self, channels):
                np.testing.assert_equal(channels[0], -channels[1])
                return np.array([[.8, .99, .6], [.6, .98, .2]])

        samples = np.zeros((260, 3))
        samples[100:, :2] = [.2, -.2]
        windows = recorded_context(Tagger(), samples, 10, 26)
        self.assertEqual(windows, [
            {"start_s": a, "end_s": b, "candidates": [{"sound": "A", "score": .7}]}
            for a, b in [(10, 20), (20, 26)]])

    def test_observe_partitions_audio_at_display_times_and_handles_an_audio_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            wave = np.zeros((500, 2), np.int16)
            wave[49, 1] = 16384
            wave[50, 0] = 8192
            wave[210:, 1] = -4096
            sf.write(out / "audio.wav", wave, 1000, subtype="PCM_16")
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pixel_format", "gray",
                            "-video_size", "16x16", "-framerate", "1000", "-i", "-",
                            "-i", str(out / "audio.wav"), "-vf", "setpts='if(eq(N,0),0,if(eq(N,1),50,200))'",
                            "-fps_mode", "passthrough", "-enc_time_base", "1:1000", "-c:v", "ffv1",
                            "-c:a", "copy", str(out / "clip.mkv")],
                           input=b"".join(bytes([v]) * 256 for v in [40, 120, 220]), check=True)
            (out / "meta.json").write_text(json.dumps({"film": "clip.mkv", "dur": .5, "until": .3,
                                                       "picture_end_s": .201}))
            read = observe(out, .0385, .2445)
            moments = read["moments"]
            self.assertEqual([m["picture"] and m["picture"]["pts_us"] for m in moments], [0, 50000, 200000, None])
            self.assertEqual([m["wav_samples"] for m in moments], [[0, 11], [11, 161], [161, 162], [162, 206]])
            self.assertEqual([m["peak_dbfs"] for m in moments], [[None, -6.02], [-12.04, None],
                                                                [None, None], [None, -18.06]])
            np.testing.assert_array_equal(sf.read(read["audio"]["wav"], dtype="int16")[0], wave[39:245])
            tail = observe(out, .2445, .3)
            self.assertEqual(tail["pages"], [])
            self.assertIsNone(tail["moments"][0]["picture"])
            self.assertIsNone(observe(out, .0501, .0502)["audio"])
            with self.assertRaises(ValueError):
                observe(out, .299, .301)

    def test_words_only_in_a_stem_need_timed_support_in_the_mix(self):
        rate = SPEECH_RATE
        mix = np.full(4 * rate, .01, dtype=np.float32)
        words = [dict(t=.2, e=.6, w='Home', p=.8),
                 dict(t=2.1, e=2.4, w='Good', p=.9),
                 dict(t=2.6, e=2.9, w='morning.', p=.9)]
        regions = [(0, rate), (2 * rate, 3 * rate)]
        # Only the first word overlaps activity in the mix. The reread hears the
        # middle word, followed by the right last word at the wrong time.
        reread = [dict(t=2.16, e=2.46, w='good!', p=.85), dict(t=3.1, e=3.5, w='Morning', p=.9)]
        with patch('reel.asr.transcribe', return_value=reread) as decode:
            result = corroborate_stem(words, regions, mix, [(0, rate)], 'en')
        reread_word = dict(words[1], src='stem+mix', gain_db=37., context_start_s=1., context_end_s=4.)
        self.assertEqual(result, [words[0], reread_word])
        samples, lang, offset = decode.call_args.args
        self.assertEqual((lang, offset), ('en', 1.))
        self.assertAlmostEqual(float(np.max(samples)), 10 ** (-3 / 20), places=5)
        with patch('reel.asr.transcribe') as decode:
            self.assertEqual(corroborate_stem(words, regions, np.zeros_like(mix), [], 'en'), [])
            decode.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp, patch('reel.listen.excerpt'), patch('reel.listen.listening_evidence', side_effect=lambda *args, **kwargs: {}):
            path = Path(tmp) / 'meta.json'
            path.write_text(json.dumps(dict(speech_with_gain=[reread_word])))
            self.assertEqual(inspect_sound(tmp, 1., 4.)['speech_with_gain']['words'], [reread_word])
            self.assertNotIn('speech_with_gain', inspect_sound(tmp, 2., 3.))
            path.write_text('{}')  # Older reels have no reread metadata.
            self.assertNotIn('speech_with_gain', inspect_sound(tmp, 0., 5.))

    def test_band_power_matches_a_known_tone_on_both_scales(self):
        rate = 48000
        t = np.arange(rate) / rate
        for fft, bands, low, high in [(2048, 128, 30, 16000), (4096, 64, 300, 4000)]:
            for amplitude in (1., .01):
                samples = amplitude * np.sin(2 * np.pi * 1500 * t)
                mel = librosa.feature.melspectrogram(y=samples, sr=rate, n_fft=fft, hop_length=480,
                                                     n_mels=bands, fmin=low, fmax=high)
                levels = band_levels(librosa.power_to_db(mel, top_db=None), fft, low, high)
                total_power = np.sum(10 ** (levels[:, 50] / 10))
                self.assertAlmostEqual(10 * np.log10(total_power), 10 * np.log10(amplitude**2 / 2), places=3)

    def test_spectrum_keeps_a_brief_peak_and_ignores_later_material(self):
        db = np.full((64, 12001), -100., np.float32)
        db[25, 543] = 12.  # One ten-millisecond bin in a 120 s page.

        def pixel(start, end, at):
            pg = Page(start, end, [('sound', 64)], '')
            draw_spectrum(pg, 'sound', db, 4096, 300, 4000, end, 'inferno')
            return pg.img.getpixel((int(pg.x(at)), pg.y['sound'][0] + 63 - 25))

        peak = pixel(0., 120., 5.435)
        self.assertEqual(peak, pixel(5.43, 5.44, 5.435))
        self.assertGreater(sum(peak), 120)
        quiet = pixel(0., 5., 3.)
        self.assertLess(sum(quiet), 10)
        db[:, 1000:] = 80.
        self.assertEqual(pixel(5.43, 5.44, 5.435), peak)
        self.assertEqual(pixel(0., 5., 3.), quiet)

    def test_mix_readout_preserves_background_peak_and_partial_last_window(self):
        levels = np.full(202, -65.)
        levels[112] = -18.
        levels[201] = -2.
        self.assertEqual(mix_level_text(levels, .005, 2.01),
                         '0:00.01 -65/-65@0:00.01, 0:01.01 -65/-18@0:01.12')
        self.assertEqual(mix_level_text(levels, 2., 2.003), '0:02.00 -65/-65@0:02.00')
        self.assertEqual(mix_level_text(levels, 3., 4.), '')

    def test_channel_power_survives_polarity_and_a_partial_hop(self):
        sr = 48000
        mono = np.random.default_rng(7).normal(0, 0.12, 12001).astype(np.float32)
        with tempfile.TemporaryDirectory() as tmp:
            def measure(right):
                path = Path(tmp) / 'mix.wav'
                sf.write(path, np.column_stack([mono, right]), sr, subtype='FLOAT')
                return audio_pass(path, lambda _: None)
            both = measure(mono)
            opposite = measure(-mono)
            left = measure(np.zeros_like(mono))
        for key in ('rms', 'db', 'channel_rms', 'changes'):
            np.testing.assert_array_equal(both[key], opposite[key])
        self.assertEqual(both['channel_rms'].shape, (2, 26))
        self.assertAlmostEqual(both['rms'][-1], 20 * np.log10(abs(mono[-1])), places=4)
        np.testing.assert_allclose(left['rms'], both['rms'] - 10 * np.log10(2), atol=1e-4)
        np.testing.assert_allclose(left['channel_rms'][1], -120, atol=1e-4)

    def test_native_channels_samples_gaps_and_snapshot_boundary(self):
        rate = 8000
        channels = np.random.default_rng(4).integers(-27000, 27000, (4000, 6), dtype=np.int16)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            sf.write(out / 'part.wav', channels, rate)
            film = out / 'clip.nut'
            # Nonzero container start, delayed sound and a 37.5 ms interior gap.
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=32x32:rate=10:d=1',
                            '-i', str(out / 'part.wav'), '-af',
                            "asetnsamples=n=80:p=0,asetpts='PTS+0.125/TB+if(gte(N,800),0.0375/TB,0)'",
                            '-c:v', 'ffv1', '-c:a', 'pcm_s16le', '-output_ts_offset', '2', str(film)], check=True)
            (out / 'meta.json').write_text(json.dumps(dict(film=film.name, dur=1, until=0.9)))
            expected = np.zeros((rate, 6), dtype=np.int16)
            expected[1000:1800] = channels[:800]
            expected[2100:5300] = channels[800:]
            for start, end, lo, hi in [(0, 0.125, 0, 1000), (0.125001, 0.30001, 1001, 2401),
                                       (0.25, 0.9, 2000, 7200)]:
                with self.subTest(start=start, end=end):
                    decoded, actual_rate = sf.read(excerpt(out, start, end), dtype='int16', always_2d=True)
                    self.assertEqual(actual_rate, rate)
                    np.testing.assert_array_equal(decoded, expected[lo:hi])
            for start, end in [(-1, 0.5), (0.5, 0.5), (0, 0.90001), (0, float('nan')), (0, float('inf')),
                               (0.00001, 0.00002)]:
                with self.assertRaises(ValueError):
                    excerpt(out, start, end)
            silent = out / 'visual.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-i', str(film), '-an', '-c:v', 'copy', str(silent)], check=True)
            (out / 'meta.json').write_text(json.dumps(dict(film=str(silent), dur=1)))
            with self.assertRaisesRegex(ValueError, 'no audio track'):
                excerpt(out, 0, 0.5)

    def test_bounded_read_cuts_native_samples_before_resampling(self):
        from fractions import Fraction
        from reel.common import read_audio, sample_boundary
        from reel.asr import load as speech_audio

        rate = 44100
        start, duration = Fraction("1.041791"), Fraction("0.073456")
        left, right = (sample_boundary(t, rate) for t in (start, start + duration))
        with tempfile.TemporaryDirectory() as tmp:
            source, excerpt = (Path(tmp) / name for name in ("mix.wav", "slice.wav"))
            pcm = np.zeros((rate * 2, 2), np.float32)
            # These samples are just outside the recording that the transcriber gets.
            pcm[left - 1] = [.4, .7]
            pcm[right] = [.8, -.2]
            sf.write(source, pcm, rate, subtype="FLOAT")
            self.assertFalse(speech_audio(source, start, duration).any())
            self.assertEqual(len(read_audio(source, rate, ss=start, t=0)), 0)
            pcm[left] = [.2, -.6]
            pcm[right - 1] = [-.5, .1]
            sf.write(source, pcm, rate, subtype="FLOAT")
            sf.write(excerpt, pcm[left:right], rate, subtype="FLOAT")
            for target_rate, channels in [(rate, None), (16000, 1), (48000, 2)]:
                expected = read_audio(excerpt, target_rate, channels=channels)
                got = read_audio(source, target_rate, ss=start, t=duration, channels=channels)
                self.assertGreater(np.max(np.abs(expected)), .01)
                if channels is None:
                    np.testing.assert_array_equal(got, expected)
                else:
                    np.testing.assert_allclose(got, expected, rtol=0, atol=2e-7)
            np.testing.assert_array_equal(read_audio(source, rate, ss=start, channels=None), pcm[left:])

    def test_snapshot_keeps_high_precision_and_last_sample_before_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values = np.random.default_rng(37).integers(-(2**31), 2**31, (8000, 2), dtype=np.int32)
            sf.write(root / 'source.wav', values, 8000, subtype='PCM_32')
            film = root / 'film.nut'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=32x32:rate=10:d=1',
                            '-i', str(root / 'source.wav'), '-c:v', 'ffv1', '-c:a', 'copy', str(film)], check=True)
            # Exercise the actual clip writer without loading the optional perception models.
            with patch('reel.perceive._build'):
                build(film, root / 'view', 30, 'en', until=0.25003)
            pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(root / 'view/clip.mkv'),
                                           '-map', '0:a:0', '-f', 's32le', '-'])
            np.testing.assert_array_equal(np.frombuffer(pcm, '<i4').reshape(-1, 2), values[:2001])

    def test_local_evidence_measures_native_bandwidth_even_without_a_tagger(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'high.wav'
            rate = 48000
            t = np.arange(11 * rate) / rate
            left = 0.25 * np.sin(2 * np.pi * 12000 * t)
            sf.write(path, np.column_stack((left, np.zeros_like(left))), rate, subtype='FLOAT')
            with patch('reel.listen.AudioTagger', side_effect=OSError('weights missing')) as loader:
                evidence = listening_evidence(path, 3, 14)
            loader.assert_called_once_with(local_only=True)
            self.assertEqual(evidence['level_rate_hz'], 48000)
            self.assertEqual(evidence['tagger_rate_hz'], 16000)
            self.assertEqual([(w['start_s'], w['end_s']) for w in evidence['windows']], [(3, 13), (4, 14)])
            for window in evidence['windows']:
                left_level, right_level = window['channels']
                self.assertAlmostEqual(left_level['rms_dbfs'], -15.1, places=1)
                self.assertFalse(left_level['digital_silence'])
                self.assertTrue(right_level['digital_silence'])
                self.assertNotIn('recorded', left_level)

    def test_level_changes_keep_weak_first_and_last_runs_on_the_film_clock(self):
        rate = 22050
        edges = (np.arange(42) * rate + 99) // 100
        signal = np.zeros(edges[-1] - 1)
        for i in range(0, 41, 4):
            signal[edges[i]:edges[i + 1]] = .01 if i in (0, 40) else .5
        start = 7.125
        end = start + len(signal) / rate
        changes = level_changes(signal, rate, start, end)
        self.assertEqual(changes['interval_count'], 11)
        self.assertIsNone(changes['reference_rms_dbfs'])
        self.assertEqual(len(changes['intervals']), 8)
        self.assertEqual(changes['intervals'][0]['start_s'], start)
        self.assertEqual(changes['intervals'][-1]['start_s'], start + .4)
        self.assertAlmostEqual(changes['intervals'][-1]['end_s'], end, places=6)
        self.assertEqual(level_changes(np.ones(100), rate, start, end)['intervals'], [])

    def test_missing_comparison_helper_still_returns_json_measurements(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'quiet.wav'
            sf.write(path, np.zeros(800), 8000)
            with patch('reel.listen.AudioTagger', side_effect=OSError), patch('reel.listen.COMPARE_PY', str(path.parent / 'missing')):
                report = listening_evidence(path, 0, .1, compare_sounds=True)
            self.assertEqual(report['schema'], 'reel.listen.v1')
            self.assertIn('unavailable', report['similarity_search'])
            self.assertEqual(report['windows'][0]['channels'][0]['level_changes']['interval_count'], 0)
            json.dumps(report, allow_nan=False)

if __name__ == '__main__':
    unittest.main()
