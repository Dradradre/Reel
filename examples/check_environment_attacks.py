"""Rebuild a controlled repetition test from the named ESC-50 audio files."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from reel.onsets import OnsetTrace
from reel.sound_glimpses import SoundChanges

if __package__:
    from .check_attack_timing import summarize
else:
    from check_attack_timing import summarize


def mixtures(inputs, audio_dir):
    recordings = {}
    for source in inputs:
        path = audio_dir / source["recording"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError(f"recording differs from the manifest: {path.name}")
        samples, rate = sf.read(path)
        if rate != 44100 or samples.ndim != 1:
            raise ValueError("expected the original mono ESC-50 recording at 44.1 kHz")
        recordings[source["category"]] = samples

    backgrounds = ["rain", "wind", "washing_machine", "engine"]
    rng = np.random.default_rng(73064)
    for category, wave in recordings.items():
        if category in backgrounds:
            continue
        # Choose the loudest 10 ms of each real recording. Surround it with
        # 15 ms before, 50 ms after. A 3 ms taper avoids sharp splice clicks.
        hop = rate // 100
        power = np.mean(wave[:len(wave) // hop * hop].reshape(-1, hop) ** 2, axis=1)
        start = int(np.clip(np.argmax(power) * hop - .015 * rate, 0, len(wave) - round(.075 * rate)))
        grain = wave[start:start + round(.075 * rate)].copy()
        fade = np.sin(np.linspace(0, np.pi / 2, round(.003 * rate))) ** 2
        grain[:len(fade)] *= fade
        grain[-len(fade):] *= fade[::-1]
        grain /= np.sqrt(np.mean(grain ** 2))
        for background, snr in zip(rng.permutation(backgrounds), [0, 6, 12, 18]):
            # Keep the unmodified background alongside its paired foreground mix.
            bg = recordings[background]
            offset = int(rng.integers(0, len(bg) - 2 * rate))
            base = bg[offset:offset + 2 * rate].copy()
            base *= .015 / max(1e-12, np.sqrt(np.mean(base ** 2)))
            count = int(rng.integers(3, 9))
            times = np.cumsum(np.r_[rng.uniform(.2, .3), rng.uniform(.08, .215, count - 1)])
            positions = np.rint(times * rate).astype(int)
            for added in (False, True):
                mix = base.copy()
                if added:
                    for first in positions:
                        mix[first:first + len(grain)] += .015 * 10 ** (snr / 20) * grain
                row = dict(source=category, background=str(background), snr_db=snr, added=added,
                           reference_s=(positions / rate).tolist() if added else [])
                yield row, mix[:, None], rate


def run(inputs, audio_dir):
    rows = []
    for row, samples, rate in mixtures(inputs, audio_dir):
        old = SoundChanges().scan_samples(samples, rate, 0)
        new = OnsetTrace().scan(samples, rate, 0)
        rows.append(dict(row, band_changes=[e["time_s"] for e in old],
                         band_rises=[e["time_s"] for e in old if "rise" in e],
                         attacks=[e["time_s"] for e in new]))
    return dict(inputs=inputs, method="ESC-50 grains, 75 ms, over paired real backgrounds. "
                "Extra peaks can belong to the background itself.", tolerance_s=.05,
                totals=summarize(rows), rows=rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="published report containing inputs, or the input list")
    parser.add_argument("audio_dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    report = run(manifest["inputs"] if isinstance(manifest, dict) else manifest, args.audio_dir)
    args.out.write_text(json.dumps(report, indent=1) + "\n")
    print(report["totals"])
