import base64
import io
import json
import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from reel.compare_heard import describe_recordings, recordings


class ReferenceListeningTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = []
        self.samples = np.zeros((16000, 2), dtype=np.int16)
        self.samples[4000:12000] = np.arange(8000, dtype=np.int16)[:, None]
        self.samples[12000:, 0] = 888
        for i in range(2):
            directory = Path(tmp.name) / f"turn_{i + 1:05}"
            directory.mkdir()
            sf.write(directory / "mix.wav", self.samples[i * 8000:(i + 1) * 8000], 8000)
            manifest = directory / "view.json"
            manifest.write_text(json.dumps({"schema": "reel.play.v1", "start_s": i, "end_s": i + 1,
                                            "audio": {"wav": "mix.wav", "channels": 2, "rate_hz": 8000,
                                                      "first_sample_on_film": i * 8000, "sample_count": 8000}}))
            self.paths.append(manifest)

    def test_changing_the_target_never_changes_the_reference_reader_input(self):
        reader = Mock()
        pair = recordings(self.paths[1].parent, [0.5, 1.25], [1.25, 1.5])
        again = recordings(self.paths[1].parent, [0.5, 1.25], [1.5, 2])
        for clips in (pair, again):
            describe_recordings(reader, clips, "What changed?")
        a, b, c, d = reader.read.call_args_list
        self.assertEqual(a, c)
        self.assertNotEqual(b, d)
        self.assertEqual(a.args, (pair["reference"]["wav"], 0.5, 1.25))
        self.assertEqual(b.args, (pair["target"]["wav"], 1.25, 1.5))
        self.assertEqual(pair["reference"]["rms_dbfs_by_channel"][0], pair["reference"]["rms_dbfs_by_channel"][1])
        self.assertEqual(again["target"]["rms_dbfs_by_channel"][1], None)

    @unittest.skipUnless(find_spec("mcp"), "MCP extra is not installed")
    def test_media_order_and_both_limits_on_a_replayed_turn(self):
        from reel.mcp_server import Viewing
        viewer = Viewing(None, None, None, None)
        viewer.paths.extend(self.paths)
        for ref, target in [([0, 0.25], [0.8, 1.2]), ([1.1, 1.4], [0, 0.25])]:
            with self.assertRaises(ValueError):
                viewer.compare_heard(1, ref, target, describe=False)
        blocks = viewer.compare_heard(2, [0, 0.25], [0.75, 1.25], describe=False, native_audio=True)
        info = json.loads(blocks[0].text)
        self.assertEqual([b.type for b in blocks], ["text", "image", "image", "audio", "audio"])
        self.assertNotEqual(blocks[1].data, blocks[2].data)
        self.assertEqual(info["reference"]["digital_silence_by_channel"], [True, True])
        self.assertEqual(info["target"]["digital_silence_by_channel"], [False, False])
        self.assertNotIn(str(self.paths[0].parent), blocks[0].text)
        for block, (first, last) in zip(blocks[-2:], [(0, 2000), (6000, 10000)]):
            pcm, _ = sf.read(io.BytesIO(base64.b64decode(block.data)), dtype="int16")
            np.testing.assert_array_equal(pcm, self.samples[first:last])
        with patch("reel.mcp_server.MAX_INLINE_PCM_BYTES", 8000):
            with self.assertRaisesRegex(ValueError, "shorten"):
                viewer.compare_heard(2, [0, 0.25], [0.75, 1.25], describe=False, native_audio=True)
