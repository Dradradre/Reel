from contextlib import closing
from importlib.util import find_spec
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from reel.follow import draw_follow, follow_point
from reel.frames import frame_sheets
if find_spec("mcp"):
    from reel.mcp_server import Viewing
from reel.play import play


class FollowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.size = (96, 64)
        self.noise = np.random.default_rng(15).integers(0, 255, (64, 96, 3), dtype=np.uint8)

    def publish(self, n, start, stop, times):
        out = self.root / f"turn_{n:05}"
        out.mkdir()
        frames = [(t, 0, np.roll(self.noise, t // 100000, axis=1).tobytes()) for t in times]
        pages = frame_sheets(out, *self.size, frames)
        current = None
        if frames:
            Image.frombytes("RGB", self.size, frames[-1][2]).save(out / "now.png")
            current = {"file": "now.png", "pts_us": times[-1]}
        packet = dict(schema="reel.play.v1", start_s=start, end_s=stop, picture_size=self.size,
                      pages=pages, current_frame=current, audio=None)
        (out / "view.json").write_text(json.dumps(packet))
        return out, packet

    def test_vfr_held_across_turns_and_future_turn_ignored(self):
        self.publish(1, 0, .25, [0, 100000])
        folder, packet = self.publish(2, .25, .65, [100000, 300000, 500000])
        self.publish(3, .65, .9, [500000, 700000])
        report, frames = follow_point(folder, packet, .199, [.5, .5])
        self.assertEqual([f.pts for f in frames], [100000, 300000, 500000])
        self.assertEqual(report["anchor"]["point"], [.5, .5])
        self.assertAlmostEqual(report["current"]["point"][0], .5 + 4 / 96, places=2)
        self.assertEqual(len(draw_follow(self.root, folder, packet, report, frames)), 2)
        self.assertEqual(report, follow_point(folder, packet, .199, [.5, .5])[0])

    def test_unlocated_point_has_no_current_crop_and_audio_tail_has_no_picture(self):
        folder, packet = self.publish(1, 0, .5, [100000, 200000])
        mock = Mock()
        mock.track.return_value = dict(status="available", model="test", points=[[.2, .3], None])
        report, frames = follow_point(folder, packet, .15, [.2, .3], mock)
        self.assertIsNone(report["current"]["point"])
        self.assertEqual(report["last_located"]["pts_us"], 100000)
        detail = draw_follow(self.root, folder, packet, report, frames)[1][1]
        self.assertEqual(detail["selected_detail_pts_us"], 100000)
        self.assertNotIn("followed_detail_pts_us", detail)
        self.assertEqual(follow_point(folder, packet, 0, [.2, .3])[0]["status"], "no_picture_at_anchor")
        tail, audio = self.publish(2, .5, .8, [])
        self.assertIsNone(follow_point(tail, audio, .15, [.2, .3], mock)[0]["current"])
        self.assertEqual(follow_point(tail, audio, .5, [.2, .3])[0]["status"], "no_picture_at_anchor")
        self.assertEqual(follow_point(tail, audio, .8, [.2, .3])[0]["status"], "anchor_outside_view")
        self.assertEqual(follow_point(tail, {**audio, "end_s": 9.5}, 1, [.2, .3])[0]["earliest_anchor_s"], 1.5)

    @unittest.skipUnless(find_spec("mcp"), "MCP extra is not installed")
    def test_future_replacement_and_replay_have_identical_mcp_blocks(self):
        prefix = [np.roll(self.noise, i, axis=1) for i in range(4)]
        replies = []
        for name, suffix in (("dark", 0), ("light", 255)):
            source = self.root / (name + ".mkv")
            pixels = b"".join(frame.tobytes() for frame in [*prefix, np.full_like(self.noise, suffix)])
            subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pixel_format", "rgb24",
                            "-video_size", "96x64", "-framerate", "4", "-i", "-", "-c:v", "ffv1", str(source)],
                           input=pixels, check=True)
            with closing(play(source, self.root / name, until=1, step=.5, continuous=True)) as playback:
                reader = Viewing(playback, None, None, None, source=source)
                with self.assertRaises(ValueError):
                    reader.advance(1, False, follow_from_s=0)
                self.assertFalse(reader.paths)
                reader.advance(1, False)
                view = reader.advance(2, False, focus=[.5, .5], follow_from_s=0)
                follow = json.loads(view[0].text)["follow"]
                self.assertEqual(follow["frame_count"], 4)
                self.assertEqual(follow["current"]["pts_us"], 750000)
                self.assertTrue(any(isinstance(item, dict) and item.get("selected_detail_source") == "original recording"
                                    for item in json.loads(view[0].text)["images_in_order"]))
                self.assertEqual(json.loads(reader.advance(3, False)[0].text)["status"], "end_of_view")
                self.assertEqual(view, reader.advance(2, False, focus=[.5, .5], follow_from_s=0))
                replies.append(view)
        self.assertEqual(*replies)

    @unittest.skipUnless(find_spec("mcp"), "MCP extra is not installed")
    def test_follow_recovers_last_visible_detail_and_rejects_replaced_source(self):
        from reel.frames import save_frame

        movie = self.root / "film.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=768x384:r=4:d=1",
                        "-c:v", "ffv1", str(movie)], check=True)
        tracker = Mock()
        tracker.track.return_value = dict(status="available", model="test", points=[[.1, .5], [.2, .5], None, None])
        with closing(play(movie, self.root / "watched", step=1, continuous=True)) as playback:
            view = Viewing(playback, None, None, None, source=movie, tracker=tracker)
            def tracked():
                reply = view.advance(1, False, focus=[.1, .5], follow_from_s=0)
                result = json.loads(reply[0].text)
                desc = next(item for item in result["images_in_order"] if "selected_detail_pts_us" in item)
                self.assertIsNone(result["follow"]["current"]["point"])
                self.assertNotIn("followed_detail_pts_us", desc)
                self.assertEqual(desc["last_located_detail_pts_us"], 250000)
                return desc
            native = tracked()
            self.assertEqual((native["selected_detail_source"], native["last_located_detail_source"]),
                             ("original recording", "original recording"))
            frozen = view.look(1, [0, 0, 1, 1])
            def replace_after_decode(path, pts, dest):
                saved = save_frame(path, pts, dest)
                other = self.root / "replacement"
                other.write_bytes(b"new bytes")
                other.replace(movie)
                return saved
            with patch("reel.follow.save_frame", side_effect=replace_after_decode) as decoder:
                fallback = tracked()
                decoder.assert_called_once()
            self.assertEqual((fallback["selected_detail_source"], fallback["last_located_detail_source"]),
                             ("published frame sheet", "published frame sheet"))
            self.assertEqual(frozen, view.look(1, [0, 0, 1, 1], at_s=.8))
            with self.assertRaisesRegex(ValueError, "original recording has changed"):
                view.look(1, [0, 0, 1, 1], at_s=.25)
            with self.assertRaises(ValueError):
                view.observe(0, .5, None, False)
