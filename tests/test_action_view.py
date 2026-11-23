import json
from fractions import Fraction
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw
import soundfile as sf

from reel.action_view import action_frames, action_views, draw_peaks, exposure_view, missing_sequence, sound_during_pictures
from reel.common import probe
from reel.frames import frame_sheets


class ActionViewTests(unittest.TestCase):
    def test_sound_belongs_only_to_the_visible_native_exposure(self):
        rate = 11025
        origin = 1092
        times = [98000, 100000, 100001, 100005, 100005, 103000, 107000]
        # The picture at 100001 us lives between adjacent audio samples.
        # An impulse at 103 ms lands on an exposure omitted from the displayed selection.
        pcm = np.zeros((100, 2), dtype=np.float64)
        pcm[1103 - origin, 1] = -.25
        pcm[1136 - origin, 0] = .5
        pcm[-1, :] = 1  # Audio continues after the picture stops.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "turn.wav"
            sf.write(path, pcm, rate, subtype="DOUBLE")
            result = sound_during_pictures(path, origin, times, set(times) - {103000}, Fraction(108, 1000))
        self.assertEqual([r["pts_us"] for r in result], [98000, 100000, 100001, 100005, 107000])
        self.assertEqual([r["wav_samples"] for r in result], [[0, 11], [11, 11], [11, 11], [11, 44], [88, 99]])
        self.assertNotIn("peak_dbfs", result[1])
        self.assertNotIn("peak_dbfs", result[2])
        self.assertEqual([r.get("peak_dbfs") for r in (result[0], result[3], result[4])],
                         [[None, None], [None, -12.04], [None, None]])

    def test_replay_pairs_published_cells_with_exact_channel_samples(self):
        times = [0, 3125, 8200, 8200, 15500, 18970]
        size = (12, 10)
        frames = [(pts, 0, bytes([i * 31]) * 360) for i, pts in enumerate(times)]
        pcm = np.zeros((14, 2))
        pcm[3, 1] = -.5
        pcm[-1, 0] = 1  # The recording continues after the picture has ended.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pages = frame_sheets(root, *size, frames)
            pages[0].update(first_cell=1, pts_us=times[1:])
            sf.write(root / "excerpt.wav", pcm, 1000, subtype="DOUBLE")
            scene = dict(pages=pages, size=size, start=.006, picture_end=.0185,
                         wav=root / "excerpt.wav", first_sample=6)
            page = exposure_view(root, scene, [])
            with Image.open(root / page["file"]) as sheet:
                tile = sheet.width // 3
                self.assertEqual([sheet.getpixel((i * tile + tile // 2, 0))[0] for i in range(3)], [31, 93, 124])
        self.assertEqual([r["pts_us"] for r in page["frames"]], [3125, 8200, 15500])
        self.assertEqual([r["wav_samples"] for r in page["frames"]], [[0, 3], [3, 10], [10, 13]])
        self.assertEqual([r["peak_dbfs"] for r in page["frames"]], [[None, None], [None, -6.02], [None, None]])
        self.assertNotIn("selected_by_band_rise", page)

    def test_long_replay_shows_neighbours_of_a_rise_only_within_the_picture_track(self):
        size = (12, 10)
        times = [i * 25000 for i in range(35)]
        frames = [(pts, 0, bytes([i + 10]) * 360) for i, pts in enumerate(times)]
        rise = {"time_s": .4, "rise": {"db": 12}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sf.write(root / "excerpt.wav", np.zeros(1000), 1000)
            scene = dict(pages=frame_sheets(root, *size, frames), size=size, start=0, picture_end=.85,
                         wav=root / "excerpt.wav", first_sample=0)
            outside = {"time_s": .85, "rise": {"db": 30}}
            fall = {"time_s": .25, "fall": {"db": 40}}
            self.assertIsNone(exposure_view(root, scene, [outside, fall]))
            page = exposure_view(root, scene, [outside, rise, fall])
            self.assertEqual(page["selected_by_band_rise"], rise)
            self.assertEqual([r["pts_us"] for r in page["frames"]], times[13:19])
            self.assertEqual(page["frames"][-1]["wav_samples"], [450, 475])
            still = [(pts, 0, frames[0][2]) for pts in times]
            scene["pages"] = frame_sheets(root, *size, still)
            self.assertIsNone(exposure_view(root, scene, [rise]))

    def test_brief_local_changes_in_a_continuously_changing_scene(self):
        size = (128, 96)
        frames = []
        for n in range(40):
            image = np.full((size[1], size[0], 3), 45 + n, np.uint8)
            if n in (12, 13):
                image[16:48, 16:48] = (255, 30, 50)
            if n == 14:
                image[32:64, 80:112] = (30, 220, 255)
            frames.append((n * 25_000, 25_000, image.tobytes()))
        action = {"pages": [{"pts_us": [0, 250_000, 500_000, 750_000], "additional_frames": []}]}
        with tempfile.TemporaryDirectory() as tmp:
            sheet = missing_sequence(tmp, frames, size, [{}, {}], action, {"pts_us": 975_000})
            self.assertIn("selection", sheet)
            times = sheet["pts_us"]
            self.assertTrue(300_000 in times or 325_000 in times)
            self.assertIn(350_000, times)
            self.assertEqual(times, sorted(set(times)))
            with Image.open(Path(tmp) / sheet["file"]) as shown:
                for i, stamp in enumerate(times):
                    y = (i // 3) * (size[1] + 24)
                    x = (i % 3) * 136 + (136 - size[0]) // 2
                    crop = shown.crop((x, y, x + size[0], y + size[1]))
                    self.assertEqual(crop.tobytes(), frames[stamp // 25_000][2])

    def test_first_view_completes_missed_picture_runs_without_repeating_a_static_scene(self):
        size = (8, 8)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def sequence(levels, sampled, sheets):
                frames = [(i * 1000, 1000, bytes([level]) * 192) for i, level in enumerate(levels)]
                action = {"pages": [{"pts_us": sampled, "additional_frames": []}]}
                return missing_sequence(root, frames, size, sheets, action, {"pts_us": frames[-1][0]})

            # Both ends show the same picture. The middle appearance has no representative.
            regular_sheet = {"file": "frame-page.png", "pts_us": [0, 1000, 2000]}
            self.assertEqual(sequence([10, 90, 10], [0], [regular_sheet]), regular_sheet)
            self.assertIsNone(sequence([10] * 16, [0], [{}, {}]))
            self.assertIsNone(sequence(range(16), [0], [{}, {}]))
            levels = [10] * 8 + [90] * 2 + [10] + [90] * 8
            compact = sequence(levels, [0, 8000], [{}, {}])
            self.assertEqual(compact["pts_us"], [0, 8000, 10000, 11000])
            self.assertEqual(compact["exposures_per_picture"], [8, 2, 1, 8])
            with Image.open(root / compact["file"]) as sheet:
                self.assertEqual([sheet.getpixel((i % 3 * 136 + 68, i // 3 * 32 + 4))[0] for i in range(4)],
                                 [10, 90, 10, 90])
            self.assertIsNone(sequence(levels, [0, 8000, 10000], [{}, {}]))

    def test_regular_views_and_a_flash_between_identical_views(self):
        # Cell one changes before its centre. Cell two briefly flashes after its centre.
        def frame(pts, level):
            return (pts, 0, bytes([level]) * (8 * 12 * 3))

        frames = [frame(0, 18), frame(100000, 80), frame(200000, 250), frame(235000, 80),
                  frame(530000, 150), frame(550000, 80), frame(600000, 255)]
        with tempfile.TemporaryDirectory() as tmp:
            overview, = action_views(tmp, frames, (12, 8), 0, .6, .6, None, 0)['pages']
            with Image.open(Path(tmp) / overview['file']) as image:
                self.assertEqual(image.getpixel((173, 249)), (18,) * 3)
                self.assertEqual(image.getpixel((349, 249)), (150,) * 3)
            page, = action_views(tmp, frames, (12, 8), 0, .6, .6, None, 0, chronological=True)['pages']
            self.assertEqual(page['pts_us'], [100000, 235000])
            self.assertEqual(page['additional_frames'], [{'at_s': 0., 'pts_us': 0},
                                                        {'at_s': .53, 'pts_us': 530000}])
            row, = page['rows']
            self.assertEqual([v['pts_us'] for v in row['pictures']], [0, 100000, 235000, 530000])
            with Image.open(Path(tmp) / page['file']) as image:
                for i, level in enumerate([18, 80, 80, 150]):
                    self.assertEqual(image.getpixel((85 + (2 * i + 1) * 88, 61)), (level,) * 3)

    def test_peak_at_serialized_recording_edges(self):
        rate = 11025
        start, end = (Fraction(str(i / rate)) for i in (1, 2))
        image = Image.new("RGB", (96, 32))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.wav"
            sf.write(path, [1.], rate, subtype="FLOAT")
            with sf.SoundFile(path) as wav:
                draw_peaks(ImageDraw.Draw(image), wav, 1, start, end, 80, 0, 16)
        self.assertEqual(image.getpixel((80, 24)), (119, 204, 242))

    def test_order_wraps_at_a_shared_picture_and_sample_boundary(self):
        rate = 11025
        left = Fraction(1, 800)
        right = left + Fraction(7, 5)
        n = 5
        split = left + (right - left) * 3 / n
        t = (split * 1_000_000).__ceil__()
        first, stop = (int((v * rate).__ceil__()) for v in (left, right))
        pcm = np.zeros((stop - first, 2), np.float32)
        peak = int((split * rate).__ceil__())
        pcm[peak - first - 1, 0] = 1
        pcm[peak - first, 1] = 1
        frames = [(0, 0, bytes(96)), (95000, 0, bytes([100]) * 96), (t, 0, bytes([170]) * 96)]
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'track.wav'
            sf.write(wav, pcm, rate, subtype='FLOAT')
            page, = action_views(tmp, frames, (8, 4), left, right, right, wav, first, chronological=True)['pages']
            self.assertEqual([(r['start_s'], r['end_s']) for r in page['rows']],
                             [(float(left), float(split)), (float(split), float(right))])
            seen = [v['at_s'] for r in page['rows'] for v in r['pictures']]
            self.assertEqual(seen, sorted(seen))
            with Image.open(Path(tmp) / page['file']) as image:
                pixels = np.asarray(image)
            blue = np.all(pixels == [119, 204, 242], axis=2)
            self.assertTrue(blue[186:216, -1].any())
            self.assertFalse(blue[216:246].any())
            height = pixels.shape[0] // 2
            self.assertTrue(blue[height + 216:, 85].any())
            self.assertTrue((pixels[height:height + 186, 85] == [249, 185, 109]).all(axis=1).any())

    def test_replay_bands_follow_the_film_clock_and_keep_channels_separate(self):
        rate = 16000
        start = Fraction(1, 17)
        end = start + Fraction(6, 5)
        first = (start * rate).__ceil__()
        times = (first + np.arange(rate * 2)) / rate - float(start)
        # Each channel plays the same two notes in opposite order at equal level.
        sounding = ((times > .2) & (times < .4)) | ((times > .8) & (times < 1))
        hz = np.where(times < .6, 375, 3000)
        pcm = np.stack([np.sin(2 * np.pi * f * times) * sounding for f in (hz, 3375 - hz)], axis=1)
        frames = [(0, 40000, bytes([55]) * 96)]
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "channels.wav"
            def render():
                sf.write(wav, pcm, rate, subtype="FLOAT")
                page, = action_views(tmp, frames, (8, 4), start, end, end, wav, first, chronological=True)["pages"]
                with Image.open(Path(tmp) / page["file"]) as image:
                    return np.asarray(image).copy()
            original = render()
            pcm[(end * rate).__ceil__() - first:] = 1
            np.testing.assert_array_equal(original, render())

        # Read the rendered centres of the 250–500 Hz and 2–4 kHz bands. At
        # this rate the display has seven bands, with high frequencies on top.
        x_start = 85
        span = original.shape[1] - x_start
        for ch in range(2):
            band_top = 186 + 2 * 30 + ch * 68 + 14
            def brightness(t, band_from_top):
                x = x_start + int(t / float(end - start) * span)
                y = band_top + int((band_from_top + .5) * 54 / 7)
                return int(original[y, x].sum())
            for t, expected in ((.3, 4 if ch == 0 else 1), (.9, 1 if ch == 0 else 4)):
                self.assertGreater(brightness(t, expected), brightness(t, 5 - expected) + 100)
                self.assertGreater(brightness(t, expected), brightness(.6, expected) + 100)

    def test_a_picture_that_ends_before_the_centre_and_repeated_static_frames(self):
        raw = bytes([30]) * 8 * 12 * 3
        with tempfile.TemporaryDirectory() as tmp:
            page, = action_views(tmp, [(40000, 0, raw), (60000, 0, raw)], (12, 8),
                                 0., .30, .11, None, 0)['pages']
            self.assertEqual(page['pts_us'], [None])
            self.assertEqual(page['additional_frames'], [{'at_s': .06, 'pts_us': 60000}])
            static, = action_views(tmp, [(0, 0, raw), (10000, 0, raw)], (12, 8),
                                   0., .3, .3, None, 0)['pages']
            self.assertEqual(static['additional_frames'], [])

    def test_picture_change_and_split_channel_peaks_share_clock(self):
        rate = 12000
        base = np.zeros((8, 12, 3), dtype=np.uint8)
        changed = np.full_like(base, 230)
        frames = [(0, 50_000, base.tobytes()), (270_000, 200_000, changed.tobytes())]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / 'slice.wav'
            samples = np.zeros((rate // 2, 2))
            samples[round(.17 * rate), 1] = .8
            sf.write(wav, samples, rate, subtype='FLOAT')
            sheet, = action_views(root, frames, (12, 8), .1, .6, .47, wav, rate // 10)['pages']
            self.assertEqual(sheet['pts_us'], [0, None])
            pixels = np.array(Image.open(root / sheet['file']))
            orange = np.all(pixels == [249, 185, 109], axis=2)
            blue = np.all(pixels == [119, 204, 242], axis=2)
            motion = np.flatnonzero(orange.any(axis=0))
            self.assertFalse(blue[181:211].any())
            peak, = np.flatnonzero(blue[211:].any(axis=0))
            self.assertLessEqual(np.abs(motion - peak).min(), 1)
            self.assertEqual(peak, 85 + int(.17 / .5 * (pixels.shape[1] - 85)))

    def test_a_transition_exactly_at_the_pause_remains_visible(self):
        old = bytes(3 * 8 * 12)
        new = bytes([180]) * len(old)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page, = action_views(root, [(500000, 500000, new)], (12, 8), .5, 1., 1., None, 0,
                                 previous_pixels=old)['pages']
            pixels = np.array(Image.open(root / page['file']))
            orange = np.all(pixels == [249, 185, 109], axis=2)
            self.assertTrue(orange[:, 85].any())

    def test_requested_focus_uses_pixels_before_resizing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            film = root / 'strip.mkv'
            rgb = np.zeros((40, 720, 3), dtype=np.uint8)
            rgb[:, 199, 0] = 255
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '720x40',
                            '-r', '2', '-i', '-', '-c:v', 'ffv1', str(film)], input=rgb.tobytes(), check=True)
            size, frames = action_frames(film, probe(film), .12, .22, [199 / 720, 0, 200 / 720, 1])
            pts, _, raw = list(frames)[0]
            self.assertEqual(pts, 0)
            red = np.frombuffer(raw, dtype=np.uint8).reshape(size[1], size[0], 3)
            self.assertGreater(red[:, size[0] // 2, 0].min(), 250)
            self.assertFalse(red[:, :, 1:].any())

    def test_playback_revisit_checks_horizon_and_keeps_the_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            movie = root / 'movie.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=64x48:r=10:d=2',
                            '-f', 'lavfi', '-i', 'sine=frequency=800:sample_rate=8000:duration=2',
                            '-c:v', 'ffv1', '-c:a', 'pcm_s16le', str(movie)], check=True)
            cmd = [sys.executable, '-m', 'reel.cli', 'play', str(movie), '--out', str(root / 'play'),
                   '--until', '1.5', '--step', '.5']
            with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as player:
                with player.stdin, player.stdout:
                    def ask(text):
                        player.stdin.write(text + '\n')
                        player.stdin.flush()
                        return player.stdout.readline().strip()

                    first = Path(player.stdout.readline().strip())
                    self.assertIn('action_view', json.loads(first.read_text()))
                    second = Path(ask('next'))
                    self.assertEqual(json.loads(second.read_text())['end_s'], 1.)
                    self.assertIn('error', json.loads(ask('observe 0.98 1.0001')))
                    self.assertIn('error', json.loads(ask('observe 0.1 0.7 --focus 0 0 1 2')))
                    result = json.loads(ask('observe 0.132 0.706 --focus 0.1 0.2 0.6 0.8'))
                    self.assertEqual(result['action_view']['box'], [.1, .2, .6, .8])
                    self.assertEqual(result['audio']['samples_per_channel'], 4592)
                    sheet, = result['action_view']['pages']
                    self.assertTrue(Path(sheet['file']).is_file())
                    details = result['action_view']['frame_pages']
                    self.assertEqual([pts for page in details for pts in page['pts_us']],
                                     [moment['picture']['pts_us'] for moment in result['moments']])
                    self.assertTrue(all(Path(page['file']).is_file() for page in details))
                    self.assertTrue(all(t < 706000 for t in sheet['pts_us']))
                    third = Path(ask('next'))
                    self.assertEqual(json.loads(third.read_text())['start_s'], 1.)
                self.assertEqual(player.wait(timeout=10), 0)
