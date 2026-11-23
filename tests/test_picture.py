from contextlib import ExitStack
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from reel.common import hdr_display_available
from reel.frames import frame_sequence, native_frames, save_frame
from reel.perceive import LEFT, PICTURE_FOOT, PICTURE_TOP, PW, TH, TW, Page, draw_picture_frames, frame_at, probe
from reel.perceive import blips, build, video_pass


class PictureTests(unittest.TestCase):
    @unittest.skipUnless(hdr_display_available(), "FFmpeg scale colour management is unavailable")
    def test_hdr_sheet_focus_and_full_still_use_the_same_display(self):
        w, h = 32, 16
        data = np.full((3, h, w), 512, dtype="<u2")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            film = root / "source.mkv"
            for transfer, codes in [("smpte2084", [195, 509]), ("arib-std-b67", [148, 615])]:
                # Two neutral patches, approximately 1 and 100 cd/m² on a 1000-nit display.
                data[0, :, :w // 2], data[0, :, w // 2:] = codes
                tags = ["-color_range", "tv", "-color_trc", transfer, "-color_primaries", "bt2020",
                        "-colorspace", "bt2020nc"]
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv444p10le",
                                "-s", f"{w}x{h}", "-r", "1", *tags, "-i", "-", "-c:v", "ffv1",
                                *tags, str(film)], input=data.tobytes(), check=True)
                self.assertEqual(probe(film)["color_transfer"], transfer)
                raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(film), "-vf",
                    "scale=out_primaries=bt709:out_transfer=srgb:out_range=pc:intent=perceptual,format=rgb24",
                    "-f", "rawvideo", "-"])
                reference = np.frombuffer(raw, np.uint8).reshape(h, w, 3)[8, [8, 24]].astype(int)
                self.assertLess(reference[0, 0], 20)
                for box, cols in [(None, [8, 24]), ([.25, 0, .75, 1], [4, 28])]:
                    [(pts, _, pixels)] = native_frames(film, w, h, box=box)
                    self.assertEqual(pts, 0)
                    shown = np.frombuffer(pixels, np.uint8).reshape(h, w, 3)
                    np.testing.assert_allclose(shown[8, cols], reference, atol=2)
                with Image.open(save_frame(film, 0, root / "still.png")) as still:
                    np.testing.assert_allclose(np.asarray(still.convert("RGB"))[8, [8, 24]], reference, atol=2)

    def test_snapshot_keeps_full_range_camera_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            film = root / "camera.mov"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x48:r=4:d=1",
                            "-c:v", "mjpeg", "-pix_fmt", "yuvj422p", str(film)], check=True)
            with patch("reel.perceive._build"):
                build(film, root / "bounded", 30, "en", until=.5000001)
            snapshot = root / "bounded" / "clip.mkv"
            self.assertEqual(probe(snapshot)["color_range"], "pc")
            expected = [(pts, rgb) for pts, _, rgb in native_frames(film, 64, 48, stop_us=500001)]
            actual = [(pts, rgb) for pts, _, rgb in native_frames(snapshot, 64, 48)]
            self.assertEqual([p for p, _ in expected], [0, 250000, 500000])
            self.assertEqual(actual, expected)

    def test_each_still_keeps_all_four_corners_in_display_proportions(self):
        colors = np.array([(210, 40, 30), (30, 200, 50), (40, 30, 220), (210, 190, 30)], dtype=np.uint8)
        still = np.full((TH, TW, 3), 80, dtype=np.uint8)
        for i, color in enumerate(colors):
            y, x = divmod(i, 2)
            rows = slice(0, TH // 5) if y == 0 else slice(-TH // 5, None)
            cols = slice(0, TW // 5) if x == 0 else slice(-TW // 5, None)
            still[rows, cols] = color
        for aspect in (9 / 16, 1, 16 / 9, 2.39):
            for stop in (0.5, 8.5, 30):
                with self.subTest(aspect=aspect, stop=stop):
                    pg = Page(0, 30, [('picture', TH + PICTURE_TOP + PICTURE_FOOT)], '')
                    v = dict(display_aspect=aspect, fps=1, th_idx=np.array([0]), thumbs=[still])
                    draw_picture_frames(pg, v, np.array([]), stop)
                    # Find the four corner patches without using the renderer’s layout coordinates.
                    page = np.asarray(pg.img)[:, LEFT:round(pg.x(stop))]
                    masks = [np.max(np.abs(page.astype(int) - color), axis=2) < 12 for color in colors]
                    self.assertTrue(all(mask.any() for mask in masks))
                    points = [np.mean(mask.nonzero(), axis=1) for mask in masks]
                    self.assertLess(points[0][0], points[2][0])
                    self.assertLess(points[0][1], points[1][1])
                    ys, xs = np.logical_or.reduce(masks).nonzero()
                    self.assertAlmostEqual(np.ptp(xs) + 1, aspect * (np.ptp(ys) + 1), delta=2.5)

    def test_no_still_borrowed_from_an_adjacent_shot(self):
        color = (199, 43, 97)
        v = dict(fps=1, th_idx=np.array([0, 2]), thumbs=np.full((2, TH, TW, 3), color, np.uint8))
        pg = Page(0, 3, [('picture', TH + PICTURE_TOP + PICTURE_FOOT)], '')
        draw_picture_frames(pg, v, np.array([1, 2]), 3)
        mask = np.all(np.asarray(pg.img) == color, axis=2)
        self.assertTrue(mask[:, LEFT:round(pg.x(1))].any())
        self.assertFalse(mask[:, round(pg.x(1)):round(pg.x(2))].any())
        self.assertTrue(mask[:, round(pg.x(2)):LEFT + PW].any())

    def test_detail_uses_display_time_and_only_the_snapshot_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            # Frame 2 stays up while the presentation clock jumps forward by five seconds.
            frames = b''.join(bytes([i]) * 32 * 24 * 3 for i in range(8))
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '32x24',
                            '-r', '4', '-i', '-', '-vf', "setpts='if(lt(N,3),N,20+N)',setsar=2",
                            '-fps_mode', 'vfr', '-c:v', 'ffv1', '-output_ts_offset', '2', str(out / 'clip.mkv')],
                           input=frames, check=True)
            (out / 'meta.json').write_text(json.dumps(dict(film='clip.mkv', dur=7, until=6)))
            self.assertAlmostEqual(probe(out / 'clip.mkv')['display_aspect'], 64 / 24)
            for at, expected in [(0, 0), (0.24999, 0), (0.25, 1), (1, 2), (5.74999, 2), (5.75, 3)]:
                with self.subTest(at=at), Image.open(frame_at(out, at)) as image:
                    self.assertEqual(image.size, (64, 24))
                    self.assertEqual(image.convert('RGB').getpixel((20, 12)), (expected,) * 3)
            for at in (-1, 6, 7, float('nan'), float('inf')):
                with self.subTest(at=at), self.assertRaises(ValueError):
                    frame_at(out, at)

    def test_snapshot_publishes_only_the_picture_visible_immediately_before_stop(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as helpers:
            root = pathlib.Path(tmp)
            film = root / 'source.mkv'
            # Each frame is a different shade. The picture ends at 2 s; the audio runs to 3 s.
            levels = [20 + 10 * i for i in range(8)]
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '32x24',
                            '-r', '4', '-i', '-', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono:d=3',
                            '-vf', 'setsar=2', '-c:v', 'ffv1', '-c:a', 'pcm_s16le', str(film)],
                           input=b''.join(bytes([v]) * 32 * 24 * 3 for v in levels), check=True)
            helpers.enter_context(patch('reel.perceive.timbre_pass', return_value=([], [])))
            for name in ('hit_pass', 'sharpness_pass'):
                helpers.enter_context(patch('reel.perceive.' + name, return_value=[]))
            helpers.enter_context(patch('reel.perceive.ocr_pass', return_value=([], [])))
            helpers.enter_context(patch('reel.perceive.sync_pass', return_value=None))
            quiet = np.full(301, -100., dtype=np.float32)
            helpers.enter_context(patch('reel.perceive.voices', return_value=dict(
                words=[], repeats=[], crowd=None, vrms=quiet, vdb=np.tile(quiet, (40, 1)))))
            for stop, index in [(1.5, 5), (1.5000001, 6), (1.5003, 6), (2., 7), (2.5, None)]:
                with self.subTest(stop=stop):
                    snap = root / str(stop)
                    build(film, snap, 30, 'en', until=stop)
                    meta = json.loads((snap / 'meta.json').read_text())
                    self.assertAlmostEqual(meta['until'], stop)
                    self.assertAlmostEqual(meta['picture_end_s'], min(stop, 2.))
                    if index is None:
                        self.assertIsNone(meta['current_frame'])
                        self.assertFalse((snap / 'now.png').exists())
                        with self.assertRaises(ValueError):
                            frame_at(snap, 2.)
                        with self.assertRaises(ValueError):
                            frame_sequence(snap, 2., stop)
                    else:
                        self.assertEqual(meta['current_frame'], dict(file='now.png', pts_us=index * 250000))
                        with Image.open(snap / 'now.png') as im:
                            self.assertEqual(im.size, (64, 24))
                            self.assertEqual(im.convert('RGB').getpixel((20, 12)), (levels[index],) * 3)

            # The 30 Hz frame at 66.667 ms is inside the prefix. Its stored Matroska
            # timestamp rounds up to 67 ms, past the requested 66.8 ms stop.
            fine = root / 'fine.mov'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '32x24',
                            '-r', '30', '-i', '-', '-c:v', 'rawvideo', '-pix_fmt', 'rgb24', str(fine)],
                           input=b''.join(bytes([v]) * 32 * 24 * 3 for v in levels), check=True)
            snap = root / 'fractional'
            build(fine, snap, 30, 'en', until=.0668)
            meta = json.loads((snap / 'meta.json').read_text())
            self.assertIn("no audio track", (snap / 'stream.md').read_text())
            self.assertEqual(meta['current_frame']['pts_us'], 66667)
            with Image.open(snap / 'now.png') as im:
                self.assertEqual(im.convert('RGB').getpixel((10, 12)), (levels[2],) * 3)

    def test_burst_hold_and_cutoff_use_the_same_presentation_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            # A 5 ms white flash between holds. A 4 or 12 fps sample can miss it entirely.
            levels = [40, 60, 235, 60, 90, 110]
            ticks = [0, 400, 410, 415, 750, 800]
            pts = '+'.join(f'eq(N,{i})*{t}' for i, t in enumerate(ticks))
            film = out / 'clip.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '32x24',
                            '-r', '1000', '-i', '-', '-vf', f"setpts='{pts}',setsar=2", '-fps_mode', 'passthrough',
                            '-enc_time_base', '1:1000', '-c:v', 'ffv1', '-output_ts_offset', '2', str(film)],
                           input=b''.join(bytes([v]) * 32 * 24 * 3 for v in levels), check=True)
            (out / 'meta.json').write_text(json.dumps(dict(film='clip.mkv', dur=.8, until=.75)))
            features = video_pass(film, probe(film), lambda _: None)
            np.testing.assert_array_equal(features['edges'][:-1], np.array(ticks) / 1000)
            self.assertEqual(len(features['mad']), len(ticks))
            flash = blips(features)
            self.assertEqual(len(flash), 1)
            self.assertAlmostEqual(flash[0][0], .410)
            self.assertAlmostEqual(flash[0][1], .005)
            for start, end, expected in [(.403, .75, [1, 2, 3]), (.41, .414, [2]), (.5, .6, [3])]:
                report = frame_sequence(out, start, end)
                self.assertEqual(report['schema'], 'reel.frames.v1')
                page, = report['pages']
                self.assertEqual(page['pts_us'], [ticks[i] * 1000 for i in expected])
                with Image.open(page['file']) as im:
                    self.assertEqual(im.width, 384 * len(expected))
                    for k, i in enumerate(expected):
                        self.assertEqual(im.getpixel((k * 384 + 192, 72)), (levels[i],) * 3)
            for start, end in [(0, .750001), (-1, .1), (.3, .3)]:
                with self.assertRaises(ValueError):
                    frame_sequence(out, start, end)

            # Cut between two presentations. The last retained picture must last up
            # to the requested time even when there is no audio to set the clip length.
            snapshot = out / 'snapshot'
            with patch('reel.perceive._build'):
                build(film, snapshot, 30, 'en', until=.65)
            info = probe(snapshot / 'clip.mkv')
            self.assertAlmostEqual(info['dur'], .65)
            bounded = video_pass(snapshot / 'clip.mkv', info, lambda _: None)
            np.testing.assert_allclose(bounded['edges'], [0, .4, .410, .415, .65])
            (snapshot / 'meta.json').write_text(json.dumps(dict(film='clip.mkv', dur=.65, until=.65)))
            held = frame_sequence(snapshot, .55, .65)
            self.assertEqual(held['pages'][0]['pts_us'], [415000])
            self.assertEqual(frame_sequence(snapshot, .55, .65), held)


if __name__ == '__main__':
    unittest.main()
