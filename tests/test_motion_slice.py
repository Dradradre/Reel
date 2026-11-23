import base64
import json
from importlib.util import find_spec
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np
from PIL import Image

from reel.motion_slice import motion_slices


class MotionSliceTests(unittest.TestCase):
    def test_rows_columns_and_short_exposures_use_only_the_released_clock(self):
        width, height = 90, 77
        y, x = np.indices((height, width))
        picture = np.stack((x, y, np.zeros_like(x)), axis=-1).astype(np.uint8)

        def frame(pts, shade):
            picture[:, :, 2] = shade
            return pts, 0, picture.tobytes()

        frames = [frame(0, 11), frame(100000, 20), frame(100000, 33), frame(100001, 44), frame(150000, 55)]
        with tempfile.TemporaryDirectory() as tmp:
            def render(items):
                page, = motion_slices(tmp, items, (width, height), .05, .18, .15, (1, 0))
                return page
            page = render(frames)
            self.assertEqual(page, render(frames[:-1] + [frame(150000, 99)]))
            self.assertEqual((page['pixel'], page['exposures'], page['start_s'], page['end_s']),
                             ([width - 1, 0], 3, .05, .15))
            with Image.open(Path(tmp) / page['file']) as im:
                chart = np.asarray(im)
            # Locate the first full-height column of the selected line. The source preview
            # is overlaid with its line locations and cannot match a complete column.
            for shade in (11, 33, 44):
                vertical = (chart[:, :, 0] == width - 1) & (chart[:, :, 2] == shade)
                top, column = np.where(vertical & (chart[:, :, 1] == 0))
                self.assertTrue(any(np.array_equal(chart[r:r + height, c, 1], np.arange(height))
                                    for r, c in zip(top, column)))
                horizontal = (chart[:, :, 1] == 0) & (chart[:, :, 2] == shade)
                top, column = np.where(horizontal & (chart[:, :, 0] == 0))
                self.assertTrue(any(np.array_equal(chart[r:r + width, c, 0], np.arange(width))
                                    for r, c in zip(top, column)))
            self.assertFalse(np.any(np.all(chart == [60, 0, 55], axis=-1)))

    def test_late_picture_empty_tail_and_more_than_one_page(self):
        size = (3, 2)
        items = [(20000 + i * 1000, 0, bytes([i % 128]) * 18) for i in range(243)]
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(motion_slices(tmp, items, size, 0, .02, .31, (.5, .5)), [])
            self.assertEqual(motion_slices(tmp, items, size, .31, .4, .31, (.5, .5)), [])
            pages = motion_slices(tmp, items, size, .005, .32, .31, (.5, .5))
            self.assertEqual([(p['start_s'], p['end_s'], p['exposures']) for p in pages],
                             [(.02, .26, 240), (.26, .31, 3)])

    @unittest.skipUnless(find_spec('mcp'), 'MCP extra is not installed')
    def test_first_view_replay_and_cli_all_use_the_same_pixels(self):
        from reel.mcp_server import Viewing
        from reel.observe import observe
        from reel.play import play

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            film = root / 'movie.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=64x48:r=10:d=1',
                            '-c:v', 'ffv1', str(film)], check=True)
            playback = play(film, root / 'seen', until=.4, step=.4, continuous=True)
            self.addCleanup(playback.close)
            view = Viewing(iter(playback), None, None, None, source=film)
            with self.assertRaisesRegex(ValueError, 'motion_point'):
                view.advance(1, False, motion_point=[float('nan'), 0])
            self.assertFalse(view.paths)
            normal = view.advance(1, False)
            with_chart = view.advance(1, False, motion_point=[1, 0])

            def get_chart(blocks):
                rows = json.loads(blocks[0].text)['images_in_order']
                return next((desc['motion_slice'], block) for desc, block in zip(rows, blocks[1:])
                            if isinstance(desc, dict) and 'motion_slice' in desc)

            row, picture = get_chart(with_chart)
            self.assertEqual(normal[1:], [block for block in with_chart[1:] if block != picture])
            self.assertEqual(row['exposures'], 4)
            self.assertEqual((row, picture), get_chart(view.ask_scene(0, .4, motion_point=[1, 0])))
            self.assertEqual((row, picture), get_chart(view.observe(0, .4, None, False, [1, 0])))
            with self.assertRaises(ValueError):
                view.ask_scene(0, .401, motion_point=[1, 0])
            saved = json.loads(view.paths[0].read_text())
            del saved['picture_size']
            view.paths[0].write_text(json.dumps(saved))
            self.assertEqual((row, picture), get_chart(view.advance(1, False, motion_point=[1, 0])))
            directory = view.paths[0].parent
            offline = observe(directory, 0, .4, playback_source=film, motion_point=[1, 0], focus=[0, 0, .5, .5])
            self.assertEqual(Path(offline['motion_slices'][0]['file']).read_bytes(), base64.b64decode(picture.data))
