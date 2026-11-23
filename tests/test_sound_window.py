import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from PIL import Image
import soundfile as sf

from reel.common import sample_boundary
from reel.sound_window import sound_window


class SoundWindowTests(unittest.TestCase):
    def test_mark_uses_published_sample_count_across_a_rounded_pause(self):
        rate = 44100
        cut = next(i for i in range(600, 900) if sample_boundary(i/rate, rate) > i)
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pcm = np.zeros((2 * cut, 2), dtype=np.int16)
            pcm[cut, 1] = 30000
            wav = directory / "sound.wav"
            sf.write(wav, pcm, rate)
            for i in range(2):
                turn = directory / f"turn_{i + 1:05}"
                turn.mkdir()
                (turn / "view.json").write_text(json.dumps({
                    "schema": "reel.play.v1", "start_s": i * cut / rate, "end_s": (i + 1) * cut / rate,
                    "audio": {"first_sample_on_film": i * cut, "sample_count": cut, "rate_hz": rate}}))
            report = sound_window(directory, wav, 0, len(pcm)/rate, 0, [cut/rate, len(pcm)/rate], turn)
            self.assertEqual(report["mark_samples"], [cut, len(pcm)])
            img = np.array(Image.open(directory / report["file"]))
            blue = np.all(img == [119, 204, 242], axis=2)
            # The single impulse survives time compression only in the second channel.
            self.assertEqual(np.flatnonzero(blue.any(axis=0)).tolist(), [72 + 384])
            self.assertFalse(blue[:162].any())

    def test_a_mark_between_two_samples_contains_no_recording(self):
        with TemporaryDirectory() as tmp:
            wav = Path(tmp) / "one.wav"
            sf.write(wav, [.5], 8000)
            result = sound_window(tmp, wav, 0, 1/8000, 0, [.00001, .00002])
            self.assertEqual(result["mark_samples"], [1, 1])
            self.assertEqual(result["samples_per_channel"], 1)
