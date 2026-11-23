import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np
from PIL import Image
import soundfile as sf

from reel.sound_glimpses import SoundChanges, sound_glimpses
from reel.observe import observe


class SoundViewTests(unittest.TestCase):
    def test_changes_survive_arbitrary_sample_boundaries_without_using_later_sound(self):
        rate = 16000
        t = np.arange(2 * rate) / rate
        frequency = np.where(t < .5, 250, np.where(t < 1.25, 2000, 250))
        changing = .25 * np.sin(2 * np.pi * frequency * t)
        steady = .25 * np.sin(2 * np.pi * 900 * t)
        recording = np.column_stack([changing, -changing, steady])
        expected = SoundChanges().scan_samples(recording, rate, 0)
        first, second = expected
        self.assertEqual(first["channels"], [1, 2])
        self.assertEqual(second["channels"], [1, 2])
        self.assertEqual(first["rise"]["hz"], [2000, 4000])
        self.assertEqual(first["fall"]["hz"], [250, 500])
        self.assertGreaterEqual(first["rise"]["db"], 3)
        self.assertTrue(.5 <= first["time_s"] < .54)
        self.assertTrue(1.25 <= second["time_s"] < 1.29)
        scan = SoundChanges()
        accumulated = []
        start = 0
        for stop in [1, 57, 1401, 8000, 8001, 8319, 8320, 8327, 20017, 23901, len(t)]:
            accumulated.extend(scan.scan_samples(recording[start:stop], rate, start))
            self.assertEqual(accumulated, [event for event in expected if event["time_s"] < stop / rate])
            start = stop

    def test_silence_and_opposite_polarity_channels_at_a_packet_boundary(self):
        rate = 16000
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "mix.wav"
            scan = SoundChanges()
            sf.write(path, np.zeros((rate, 2)), rate)
            self.assertEqual(scan.scan(path, 0), [])
            t = np.arange(rate) / rate
            tone = np.sin(2 * np.pi * 750 * t) * .2
            sf.write(path, np.column_stack([tone, -tone]), rate, subtype="FLOAT")
            event, = scan.scan(path, rate)
            self.assertEqual(event["channels"], [1, 2])
            self.assertLessEqual(1, event["time_s"])
            self.assertLess(event["time_s"], 1.1)
            # Seeking far from that audio must forget the previous spectrum.
            self.assertEqual(scan.scan(path, 8 * rate), [])

    def test_view_samples_only_available_frames_within_the_selected_range(self):
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def picture(us):
                seen.append(us)
                return 0, Image.new("RGB", (64, 36))
            event, = sound_glimpses(root, [{"time_s": 2.2}], picture, 2.125, 2.3, 2.23)["events"]
            self.assertEqual(seen, [2125000, 2200000])
            self.assertIsNone(event["frames"][-1]["pts_us"])
            self.assertLess(event["frames"][-1]["at_s"], 2.3)
            self.assertTrue((root / event["file"]).is_file())

    def test_replay_uses_held_picture_clock_and_preserves_native_stereo_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rate = 8000
            first, last = .26125, .714125
            signal = np.column_stack([np.arange(rate), -np.arange(rate)]).astype(np.int16)
            sf.write(root/"in.wav", signal, rate, subtype="PCM_16")
            for i, future in enumerate([18, 222]):
                out = root / str(i)
                out.mkdir()
                frames = np.stack([np.full((16,24,3), lev, np.uint8) for lev in [30,60,120,future]])
                source = out / "clip.mkv"
                subprocess.run(["ffmpeg","-v","error","-f","rawvideo","-pix_fmt","rgb24","-s","24x16","-r","4",
                                "-i","-","-i",str(root/"in.wav"),"-c:v","ffv1","-c:a","pcm_s16le",str(source)],
                               input=frames.tobytes(),check=True)
                (out/"meta.json").write_text(json.dumps({"film":"clip.mkv","dur":1.,"until":1.}))
                result = observe(out,first,last,media=True)
                media = result["media"]["file"]
                decoded = subprocess.check_output(["ffmpeg","-v","error","-i",media,"-map","0:a:0","-f","s16le","-"])
                np.testing.assert_array_equal(np.frombuffer(decoded,"<i2").reshape(-1,2),signal[2090:5713])
                times = json.loads(subprocess.check_output(["ffprobe","-v","error","-select_streams","v:0",
                                "-show_packets","-show_entries","packet=pts_time,duration_time","-of","json",media]))["packets"]
                self.assertEqual([float(t["pts_time"]) for t in times],[0.,.23875])
                self.assertAlmostEqual(float(times[-1]["duration_time"]),.214125,places=6)
                if i == 0:
                    original = Path(media).read_bytes()
                else:
                    self.assertEqual(original,Path(media).read_bytes())

    def test_replay_keeps_a_late_video_start_and_an_audio_only_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "color=s=32x24:r=4:d=0.5", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=1.2",
                            "-vf", "settb=AVTB,setpts=PTS+200000", "-c:v", "ffv1", "-fps_mode", "passthrough",
                            "-enc_time_base:v", "1:1000000",
                            "-c:a", "pcm_u8", str(source)], check=True)
            (root/"meta.json").write_text(json.dumps({"film": "clip.mkv", "dur": 1.2, "until": 1.2}))
            result = observe(root, 0, 1.2, media=True)
            movie = result["media"]["file"]
            streams = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                "stream=codec_type,start_time,duration", "-of", "json", movie]))["streams"]
            track = next(s for s in streams if s["codec_type"] == "video")
            self.assertAlmostEqual(float(track["start_time"]), .2, places=6)
            self.assertAlmostEqual(float(track["duration"]), .5, places=6)
            self.assertAlmostEqual(result["media"]["picture_end_s"], .7, places=6)
            tail = observe(root, .8, 1.2, media=True)
            self.assertEqual(tail["media"]["format"], "wav")
            self.assertEqual(tail["media"]["file"], tail["audio"]["wav"])
