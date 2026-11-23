"""Rehear sustained and percussive structure within an already released recording."""
from pathlib import Path
import subprocess
import tempfile
from typing import Literal, get_args

import numpy as np
from scipy.fft import irfft, next_fast_len, rfft
from scipy.ndimage import median_filter
from scipy.signal.windows import hann

if __package__:
    from .common import open_recording, probe
else:
    from common import open_recording, probe


Component = Literal["mix", "harmonic", "percussive"]
COMPONENTS = get_args(Component)
MEDIAN_WIDTH = 31
BASIS = ("Harmonic-percussive median filtering of this excerpt only. Harmonic emphasizes frequency bands "
         "that persist over time; percussive emphasizes energy spread across frequencies at a given moment. "
         "The two estimates add back to the recording. One sound can be present in both. The same mask is "
         "used on every channel. Levels are measured before playback gain.")


def harmonic_part(samples, hop, window):
    """Apply a single soft spectral mask to the original phase in every channel."""
    width = len(window)
    padded = np.pad(samples.T, ((0, 0), (width, width)))
    frames = np.lib.stride_tricks.sliding_window_view(padded, width, axis=-1)[:, ::hop, :]
    spectrum = rfft(frames * window, axis=-1)
    # Pool energy across channels. Opposite-polarity stereo must not cancel.
    magnitude = np.sqrt(np.mean(np.abs(spectrum) ** 2, axis=0))
    sustained = median_filter(magnitude, (MEDIAN_WIDTH, 1), mode="nearest") ** 2
    transient = median_filter(magnitude, (1, MEDIAN_WIDTH), mode="nearest") ** 2
    total = sustained + transient
    mask = np.divide(sustained, total, out=np.full_like(total, .5), where=total > 0)
    filtered = irfft(spectrum * mask, n=width, axis=-1) * window
    result = np.zeros_like(padded)
    # Four overlapping periodic Hann windows have a constant squared sum of 3/2.
    for offset in range(4):
        part = filtered[:, offset::4].reshape(samples.shape[1], -1)
        at = offset * hop
        result[:, at:at + part.shape[1]] += part
    return result[:, width:width + len(samples)].T / 1.5


def focus_recording(wav, component, out):
    """Return a playable component and levels on the original channel/sample clock."""
    if component not in COMPONENTS:
        raise ValueError("choose mix, harmonic or percussive for the listening component")
    wav = Path(wav)
    if component == "mix":
        return wav, None
    out = Path(out)
    dest = out / f"{wav.stem}_{component}.wav"
    with tempfile.TemporaryDirectory(dir=out) as tmp, open_recording(wav) as recording:
        rate, channels, length = recording.samplerate, recording.channels, len(recording)
        if length == 0:
            raise ValueError("the listening range contains no audio samples")
        hop = next_fast_len(max(1, round(rate * .0116)), real=True)
        window = hann(4 * hop, sym=False)
        # Read neighbouring frames for the median and all overlap-add windows before cropping.
        # Keep block boundaries on the same STFT grid, including the last partial block.
        context = (MEDIAN_WIDTH // 2 + 4) * hop
        block_size = 96 * hop
        energy = np.zeros((2, channels))
        raw = Path(tmp) / "component.f32"
        with raw.open("wb") as output:
            for start in range(0, length, block_size):
                stop = min(length, start + block_size)
                left, right = max(0, start - context), min(length, stop + context)
                recording.seek(left)
                samples = recording.read(right - left, dtype="float64", always_2d=True)
                selected = slice(start - left, stop - left)
                original = samples[selected]
                part = harmonic_part(samples, hop, window)[selected]
                if component == "percussive":
                    part = original - part
                energy[0] += np.square(original).sum(axis=0)
                energy[1] += np.square(part).sum(axis=0)
                output.write(part.astype("<f4").tobytes())
        layout = probe(wav).get("audio_layout")
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-f", "f32le", "-ar", str(rate), "-ac", str(channels)]
        if layout:
            cmd.extend(["-channel_layout", layout])
        rendered = Path(tmp) / "component.wav"
        subprocess.run([*cmd, "-i", str(raw), "-map_metadata", "-1", "-c:a", "pcm_f32le", "-rf64", "auto",
                        str(rendered)], check=True, capture_output=True)
        rendered.replace(dest)
    mix_db, component_db = [[round(float(10 * np.log10(e / length)), 2) if e else None for e in row]
                            for row in energy]
    return dest, {"name": component, "basis": BASIS, "mix_rms_dbfs": mix_db, "component_rms_dbfs": component_db}
