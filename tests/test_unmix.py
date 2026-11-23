import base64
import io
import json
from importlib.util import find_spec
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
import soundfile as sf

from reel.unmix import LayerReader, describe_layer, listen_layer


class LayerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.paths = []
        self.samples = np.zeros((32000, 2), dtype=np.float32)
        self.samples[:, 1] = np.arange(32000) / 65536
        self.samples[16000:, 0] = -self.samples[16000:, 1]
        for i in range(2):
            directory = self.root / f"turn_{i + 1:05}"
            directory.mkdir()
            sf.write(directory / "mix.wav", self.samples[16000*i:16000*(i+1)], 16000, subtype="FLOAT")
            manifest = directory / "view.json"
            manifest.write_text(json.dumps({"schema": "reel.play.v1", "start_s": i, "end_s": i+1, "audio": {
                "wav": "mix.wav", "rate_hz": 16000, "channels": 2, "sample_count": 16000,
                "first_sample_on_film": 16000*i}}))
            self.paths.append(manifest)

    def test_channel_silence_and_noninteger_resampling_without_downmix(self):
        wav = self.root / "stereo.wav"
        sf.write(wav, self.samples[:12347], 44100, subtype="FLOAT")
        net = Mock()
        net.separate.side_effect = lambda mix: np.array([mix / (k+1) for k in range(8)])
        quiet = listen_layer(lambda: net, wav, 1, 2)
        self.assertEqual(quiet["status"], "no_signal_at_encoder_rate")
        net.separate.assert_not_called()
        row = listen_layer(lambda: net, wav, 2, 2)
        self.assertEqual(row["samples"], (12347*16000 + 44100-1)//44100)
        self.assertEqual(row["original_samples"], 12347)
        original, rate = sf.read(row["mix_wav"], dtype="float32")
        part, rate2 = sf.read(row["layer_wav"], dtype="float32")
        self.assertNotIn(b"PEAK", Path(row["mix_wav"]).read_bytes()[:72])
        self.assertEqual(rate, rate2)
        np.testing.assert_array_equal(part, original/2)
        self.assertLess(row["layer_rms_dbfs"][1], row["mix_rms_dbfs"])
        sound = Mock()
        describe_layer(sound, {**row, "start_s": 0, "end_s": 12347/44100})
        self.assertEqual([c.args[0] for c in sound.read.call_args_list], [row["mix_wav"], row["layer_wav"]])

    def test_focusing_keeps_the_mix_when_it_matches_best_and_never_prompts_the_describer(self):
        net = Mock()
        net.separate.side_effect = lambda mix: np.array([mix / (i+1) for i in range(8)])
        layers = LayerReader()
        layers.request = Mock(side_effect=lambda job: listen_layer(lambda: net, **job))
        matcher = Mock()
        layers.matcher = matcher
        for picked in (0, 3):
            matcher.choose_layer.return_value = {"status": "available", "selected": picked}
            result = layers.inspect(self.paths[1].parent, .5, 1.5, 2, focus="an unfamiliar sound")
            self.assertEqual(result["selected_layer"], picked)
            self.assertNotIn("candidate_wavs", result)
            self.assertEqual("layer_wav" in result, bool(picked))
            paths, query = matcher.choose_layer.call_args.args
            self.assertEqual(len(paths), 9)
            self.assertEqual(query, "an unfamiliar sound")
            native, rate = sf.read(result["mix_wav"], dtype="float32")
            np.testing.assert_array_equal(native, self.samples[8000:24000, 1])
            self.assertEqual(rate, 16000)
            if picked:
                self.assertEqual(paths[picked], result["layer_wav"])
            sound = Mock()
            describe_layer(sound, result)
            self.assertNotIn(query, str(sound.read.call_args_list))
            self.assertEqual(sound.read.call_count, 2 if picked else 1)
        matcher.choose_layer.return_value = {"status": "unavailable"}
        unavailable = layers.inspect(self.paths[1].parent, .5, 1.5, 2, focus="rain")
        self.assertTrue(Path(unavailable["mix_wav"]).is_file())
        self.assertIsNone(unavailable["selected_layer"])

    def test_full_band_original_survives_omitting_the_estimate(self):
        rate = 48000
        signal = np.zeros((rate, 2), dtype=np.int32)
        signal[:, 1] = (np.sin(2*np.pi*15000*np.arange(rate)/rate)*1_000_000).astype(np.int32)
        manifest = self.paths[0]
        sf.write(manifest.parent / "mix.wav", signal, rate, subtype="PCM_32")
        view = json.loads(manifest.read_text())
        view["audio"].update(rate_hz=rate, sample_count=rate)
        manifest.write_text(json.dumps(view))
        layers = LayerReader()
        layers.request = Mock(return_value={"status": "unavailable", "reason": "not installed"})
        result = layers.inspect(manifest.parent, .25, .75, 2, 1)
        original, saved_rate = sf.read(result["mix_wav"], dtype="int32")
        self.assertEqual(saved_rate, rate)
        np.testing.assert_array_equal(original, signal[12000:36000, 1])
        self.assertEqual(result["first_sample_on_film"], 12000)

    @unittest.skipUnless(find_spec("mcp"), "MCP extra is not installed")
    def test_revisit_crosses_released_turns_and_checks_cutoff_before_subprocess(self):
        from reel.mcp_server import Viewing
        net = Mock()
        net.separate.side_effect = lambda mix: np.array([mix * (1-i/8) for i in range(8)])
        layers = LayerReader()
        layers.request = Mock(side_effect=lambda job: listen_layer(lambda: net, **job))
        viewer = Viewing(None, None, None, None, layers=layers)
        viewer.paths.extend(self.paths)
        with self.assertRaises(ValueError):
            viewer.listen_layer(1, .75, 1.25, 2, 2, True, False)
        with self.assertRaises(ValueError):
            viewer.listen_layer(2, .75, 1.25, 3, 2, True, False)
        layers.request.assert_not_called()
        blocks = viewer.listen_layer(2, .75, 1.25, 2, 2, True, False)
        report = json.loads(blocks[0].text)
        self.assertEqual(report["first_sample_on_film"], 12000)
        self.assertEqual(report["wavs_in_order"], ["mix", "layer"])
        self.assertEqual(report["recorded_channels"], 2)
        self.assertEqual(report["samples"], 8000)
        self.assertNotIn(str(self.root), blocks[0].text)
        self.assertEqual([b.type for b in blocks], ["text", "image", "image", "audio", "audio"])
        for block, factor in zip(blocks[-2:], [1, 7/8]):
            pcm, rate = sf.read(io.BytesIO(base64.b64decode(block.data)), dtype="float32")
            self.assertEqual(rate, 16000)
            np.testing.assert_array_equal(pcm, self.samples[12000:20000, 1] * factor)
