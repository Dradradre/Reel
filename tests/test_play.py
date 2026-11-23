import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from PIL import Image

from reel.common import probe
from reel.listen import inspect_sound
from reel.play import play, recorded_spectrum


class PlaybackTests(unittest.TestCase):
    def test_default_picture_and_recording_share_the_selected_stream_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signal = np.column_stack([np.arange(4000, dtype=np.int16), -np.arange(4000, dtype=np.int16)])
            wav = root / "stereo.wav"
            sf.write(wav, signal, 16000)
            many = root / "choices.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=40x40:r=4:d=1",
                            "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=1",
                            "-f", "lavfi", "-i", "color=blue:s=32x24:r=4:d=0.25",
                            "-itsoffset", "0.125", "-i", str(wav), "-map", "0:v", "-map", "1:a",
                            "-map", "2:v", "-map", "3:a", "-c:v", "ffv1", "-c:a", "pcm_s16le",
                            "-disposition:v:0", "0", "-disposition:v:1", "default",
                            "-disposition:a:0", "0", "-disposition:a:1", "default", str(many)], check=True)
            info = probe(many)
            self.assertEqual((info["video_index"], info["audio_index"], info["dur"]), (2, 3, .375))
            paths = list(play(many, root / "seen", step=.125, continuous=True, recording_only=True))
            views = [json.loads(path.read_text()) for path in paths]
            self.assertEqual([v["end_s"] for v in views], [.125, .25, .375])
            with Image.open(paths[0].parent / views[0]["current_frame"]["file"]) as still:
                self.assertEqual(still.size, (32, 24))
                self.assertGreater(still.convert("RGB").getpixel((16, 12))[2], 245)
            self.assertFalse(views[-1]["pages"])
            blocks = [sf.read(path.parent / view["audio"]["wav"], dtype="int16")[0]
                      for path, view in zip(paths, views)]
            np.testing.assert_array_equal(np.concatenate(blocks), np.pad(signal, ((2000, 0), (0, 0))))
            for flag in ("0", "default"):
                tied = root / (flag + ".mkv")
                subprocess.run(["ffmpeg", "-v", "error", "-i", str(many), "-map", "0", "-c", "copy",
                                "-disposition:v", flag, "-disposition:a", flag, str(tied)], check=True)
                self.assertEqual((probe(tied)["video_index"], probe(tied)["audio_index"]), (0, 1))

    def test_album_cover_does_not_publish_a_video_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Image.new("RGB", (32, 32), "red").save(root / "cover.jpg")
            film = root / "covered.m4a"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=440:d=0.3",
                            "-i", str(root / "cover.jpg"), "-map", "0:a", "-map", "1:v", "-c:a", "aac",
                            "-c:v", "copy", "-disposition:v", "attached_pic", str(film)], check=True)
            self.assertFalse(probe(film)["has_video"])
            path, = list(play(film, root / "seen", until=.2, continuous=True, recording_only=True))
            packet = json.loads(path.read_text())
            self.assertEqual(packet["pages"], [])
            self.assertIsNone(packet["current_frame"])
            self.assertEqual(packet["audio"]["sample_count"], 8820)

    def test_original_bandwidth_and_recorded_level_survive_the_image_client_audio_path(self):
        for rate in (44100, 96000):
            t = np.arange(rate) / rate
            tone = np.sin(2 * np.pi * 12000 * t)
            noise = np.random.default_rng(17).normal(size=rate)
            noise *= np.sqrt(np.mean(tone ** 2) / np.mean(noise ** 2))
            pcm = (3000 * np.column_stack([tone, noise, np.zeros(rate)])).astype(np.int16)
            a, b, quiet = recorded_spectrum(pcm, rate)["channels"]
            self.assertEqual(a["rms_dbfs"], b["rms_dbfs"])
            self.assertLess(abs(a["band_center_hz"] - 12000), 32)
            self.assertGreater(a["band_power_fraction"], .95)
            self.assertLess(b["band_power_fraction"], .02)
            low, high = b["middle_80_percent_hz"]
            self.assertTrue(low < rate * .1 and high > rate * .4)
            self.assertEqual(quiet, {"channel": 3, "rms_dbfs": None})
        short = recorded_spectrum(np.array([[128, 192]], dtype=np.uint8), 48000)
        self.assertEqual(short["channels"], [{"channel": 1, "rms_dbfs": None},
                                             {"channel": 2, "rms_dbfs": -6.0}])

    def test_continuous_pcm_limit_splits_on_native_samples(self):
        from contextlib import closing

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rate = 8000
            data = np.zeros((rate, 2), np.int16)
            source = root / "source.wav"
            sf.write(source, data, rate)
            with closing(play(source, root / "session", continuous=True, step=8.,
                              max_continuous_pcm_bytes=7900, until=.51)) as player:
                packets = [json.loads(file.read_text()) for file in player]
            self.assertEqual([v["audio"]["sample_count"] for v in packets], [1975, 1975, 130])
            self.assertEqual([v["audio"]["first_sample_on_film"] for v in packets], [0, 1975, 3950])
            self.assertEqual(packets[-1]["end_s"], .51)

    def test_reader_controls_publication_and_changed_future_is_invisible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            # Earlier pictures are the same. The audio changes first, then the two futures differ.
            level = np.zeros((16000, 2), np.int16)
            level[4800:8000] = [1024, -1024]
            results = []
            for arm in (1, 2):
                samples = level.copy()
                samples[8000:] = [arm * 1000, 0]
                sf.write(root / 'input.wav', samples, 8000, subtype='PCM_16')
                source = root / f'movie{arm}.mkv'
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'gray', '-s', '16x16',
                                '-r', '10', '-i', '-', '-i', str(root / 'input.wav'), '-c:a', 'copy', '-c:v', 'ffv1',
                                str(source)], input=b''.join(bytes([0 if n < 10 else 80 * arm]) * 256 for n in range(20)),
                               check=True)
                dest = root / f'out{arm}'
                cmd = [sys.executable, '-m', 'reel.cli', 'play', str(source), '--out', str(dest), '--until', '1.0000']
                with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as cli:
                    with cli.stdin, cli.stdout:
                        first = Path(cli.stdout.readline().strip())
                        self.assertEqual(sorted(dest.iterdir()), [first.parent])
                        self.assertEqual(json.loads(first.read_text())['end_s'], .7)
                        cli.stdin.write('next\n')
                        cli.stdin.flush()
                        last = Path(cli.stdout.readline().strip())
                        self.assertEqual(json.loads(last.read_text())['end_s'], 1.)
                    self.assertEqual(cli.wait(timeout=10), 0)
                manifests = [json.loads(f.read_text()) for f in (first, last)]
                self.assertEqual([q['start_s'] for q in manifests], [0, .7])
                self.assertEqual([q['audio']['first_sample_on_film'] for q in manifests], [0, 5600])
                audio = np.concatenate([sf.read(f.parent / 'sound.wav', dtype='int16')[0] for f in (first, last)])
                np.testing.assert_array_equal(audio, samples[:8000])
                with patch('reel.listen.AudioTagger', side_effect=OSError):
                    heard = inspect_sound(last.parent, .71255, .799)
                    retained, _ = sf.read(heard['wav'], dtype='int16')
                    np.testing.assert_array_equal(retained, samples[5701:6392])
                    self.assertEqual(heard['windows'][0]['start_s'], .712625)
                    with self.assertRaises(ValueError):
                        inspect_sound(last.parent, .65, .83)
                results.append({str(f.relative_to(dest)): hashlib.sha256(f.read_bytes()).hexdigest()
                                for f in dest.rglob('*') if f.is_file()})
            self.assertEqual(*results)

    def test_holds_quiet_periods_and_audio_only_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            movie = root / 'held.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=32x24:r=1:d=1',
                            '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono:d=2.2', '-c:v', 'ffv1',
                            '-c:a', 'pcm_u8', '-output_ts_offset', '2', str(movie)], check=True)
            player = play(movie, root / 'session', step=.75)
            dirs = [next(player), player.send(.25), *player]
            packets = [json.loads(f.read_text()) for f in dirs]
            self.assertEqual([q['end_s'] for q in packets], [.75, 1., 1.75, 2.2])
            self.assertEqual([q.get('picture_end_s') for q in packets], [.75, 1., None, None])
            self.assertEqual([q['current_frame'] is not None for q in packets], [True, True, False, False])
            self.assertEqual(packets[1]['pages'][0]['pts_us'], [0])
            self.assertEqual(packets[2]['pages'], [])
            for folder in dirs:
                y, rate = sf.read(folder.parent / 'sound.wav')
                self.assertTrue(np.all(y == 0))

    def test_picture_alone_can_pause_playback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            movie = root / 'cut.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'gray', '-s', '16x16',
                            '-r', '10', '-i', '-', '-c:v', 'ffv1', str(movie)],
                           input=b''.join(bytes([20 if i < 8 else 200]) * 256 for i in range(20)), check=True)
            views = [json.loads(f.read_text()) for f in play(movie, root / 'session', until=1.25)]
            self.assertEqual([v['end_s'] for v in views], [.9, 1.25])
            self.assertTrue(all(v['audio'] is None for v in views))
            times = [pts for v in views for sheet in v['pages'] for pts in sheet['pts_us']]
            self.assertEqual(times, list(range(0, 1250000, 100000)))
