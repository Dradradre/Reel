import base64
from contextlib import closing
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from scipy.fft import next_fast_len
from scipy.signal.windows import hann
import soundfile as sf

from reel.audio_focus import focus_recording, harmonic_part
from reel.hearing import ListeningHistory, RATE
from reel.play import play


class AcousticFocusTests(unittest.TestCase):
    def test_comparison_keeps_both_waveforms_on_the_same_clock(self):
        calls = []

        class Ear:
            def compare(self, focus, mix, component, question):
                calls.append((focus, mix, component, question))
                return "two sounds"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            t = np.arange(31 * RATE) / RATE
            tone = (.2 * np.sin(2 * np.pi * 300 * t)).astype("float32")
            mix = np.column_stack((tone, -tone))
            focused = np.zeros_like(mix)
            focused[::13] = mix[::13]
            for name, data in (("mix", mix), ("focus", focused)):
                sf.write(root / f"{name}.wav", data, RATE, subtype="FLOAT")
            result = ListeningHistory(Ear()).read(root / "focus.wav", 2., 33., False, "What changes?",
                                                  mix_wav=root / "mix.wav", component="percussive")
            self.assertEqual(len(calls), 4)
            for i in range(2):
                np.testing.assert_array_equal(np.concatenate([c[0] for c in calls[i::2]]), focused[:, i])
                np.testing.assert_array_equal(np.concatenate([c[1] for c in calls[i::2]]), mix[:, i])
            for n, window in enumerate(result["windows"]):
                self.assertEqual([row["answer"] for row in window["channels"]], ["two sounds"] * 2)
                for _, _, component, question in calls[2*n:2*n+2]:
                    self.assertEqual(component, "percussive")
                    self.assertIn(f'film time {window["start_s"]:.6f} s', question)
                    self.assertIn(f'{window["end_s"]:.6f} s', question)

    def test_linked_masks_reconstruct_every_channel_across_processing_blocks(self):
        rate = 8000
        t = np.arange(32617) / rate
        note = .2 * np.sin(2 * np.pi * 440 * t)
        clicks = np.zeros_like(t)
        clicks[900::2111] = .5
        wave = note + clicks
        samples = np.column_stack([wave, -.4 * wave, clicks, np.zeros_like(wave)])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "original.wav"
            sf.write(wav, samples, rate, subtype="DOUBLE")
            self.assertEqual(focus_recording(wav, "mix", root), (wav, None))
            outputs = {}
            for name in ("harmonic", "percussive"):
                path, report = focus_recording(wav, name, root)
                data, actual_rate = sf.read(path, always_2d=True)
                self.assertEqual(rate, actual_rate)
                self.assertEqual(data.shape, samples.shape)
                self.assertIsNone(report["component_rms_dbfs"][3])
                np.testing.assert_array_equal(data[:, 3], 0)
                np.testing.assert_allclose(data[:, 1], -.4 * data[:, 0], atol=2e-8)
                outputs[name] = data
            np.testing.assert_allclose(outputs["harmonic"] + outputs["percussive"], samples, atol=4e-8)
            hop = next_fast_len(round(rate * .0116), real=True)
            reference = harmonic_part(samples, hop, hann(4 * hop, sym=False))
            np.testing.assert_allclose(outputs["harmonic"], reference, atol=3e-8)
            self.assertGreater(abs(outputs["percussive"][900, 0]), .35)
            np.testing.assert_allclose(outputs["harmonic"][3800:4100, 0], note[3800:4100], atol=.005)

    def test_mid_and_side_preserve_layout_and_only_transform_paired_channels(self):
        import subprocess
        rate = 24000
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples = np.random.default_rng(882).uniform(-.7, .7, (65541, 6)).astype("<f4")
            samples[:, 1] = samples[:, 0]
            samples[:, 5] = -samples[:, 4]
            wav = root / "surround.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(rate), "-ac", "6",
                            "-channel_layout", "5.1", "-i", "-", "-c:a", "pcm_f32le", str(wav)],
                           input=samples.tobytes(), check=True)
            parts = []
            for name in ("mid", "side"):
                dest, report = focus_recording(wav, name, root)
                part, sr = sf.read(dest, dtype="float32", always_2d=True)
                self.assertEqual(sr, rate)
                self.assertEqual([pair["channels"] for pair in report["pairs"]], [[1, 2], [5, 6]])
                parts.append(part)
            mid, side = parts
            np.testing.assert_array_equal(mid[:, :4], samples[:, :4])
            np.testing.assert_array_equal(mid[:, 4:], 0)
            np.testing.assert_array_equal(side[:, :4], 0)
            np.testing.assert_array_equal(side[:, 4], samples[:, 4])
            np.testing.assert_array_equal(side[:, 5], samples[:, 4])
            np.testing.assert_array_equal(mid[:, 4] + side[:, 4], samples[:, 4])
            np.testing.assert_array_equal(mid[:, 5] - side[:, 5], samples[:, 5])
            mono = root / "mono.wav"
            sf.write(mono, samples[:, 0], rate)
            with self.assertRaisesRegex(ValueError, "channel pair"):
                focus_recording(mono, "side", root)

    def test_selected_audio_and_measurements_use_only_the_released_samples(self):
        try:
            from reel.mcp_server import Viewing
        except ModuleNotFoundError as error:
            if error.name == "mcp":
                self.skipTest("MCP transport is optional")
            raise
        rate = 8000
        rng = np.random.default_rng(1029)
        samples = rng.integers(-10000, 10000, (16000, 2), dtype=np.int16)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            replies = []
            for i in range(2):
                wav = root / f"mix{i}.wav"
                sf.write(wav, samples, rate)
                with closing(play(wav, root / str(i), until=.875, step=.25, continuous=True)) as playback:
                    reader = Viewing(playback, None, None, None)
                    for n in range(1, 5):
                        reader.advance(n, False, False)
                    blocks = reader.listen(4, None, .100125, .875, True, False, "harmonic")
                    side = reader.listen(4, None, .100125, .875, True, False, "side")
                    side_pcm, side_rate = sf.read(io.BytesIO(base64.b64decode(side[-1].data)), always_2d=True)
                    self.assertEqual(side_rate, rate)
                    expected = samples[801:7000].astype(np.float64) / 32768
                    delta = (expected[:, 0] - expected[:, 1]) * .5
                    np.testing.assert_array_equal(side_pcm, np.column_stack([delta, delta]))
                    with self.assertRaises(ValueError):
                        reader.listen(4, None, .1, .875125, False, False, "percussive")
                info = json.loads(blocks[0].text)
                self.assertEqual([b.type for b in blocks], ["text", "image", "audio"])
                self.assertEqual(info["component"]["name"], "harmonic")
                self.assertEqual(info["first_sample_on_film"], 801)
                pcm, sr = sf.read(io.BytesIO(base64.b64decode(blocks[-1].data)), always_2d=True)
                self.assertEqual((sr, pcm.shape), (rate, (6199, 2)))
                self.assertEqual(info["samples_per_channel"], len(pcm))
                self.assertNotIn(str(root), blocks[0].text)
                replies.append(blocks)
                samples[7000:] = 0
            self.assertEqual(replies[0], replies[1])
