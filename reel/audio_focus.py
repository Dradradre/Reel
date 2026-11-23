"""Rehear spectral or stereo components of an already released recording."""
from pathlib import Path
import math
import subprocess
import tempfile
from typing import Literal, get_args

import numpy as np
from scipy.fft import irfft, next_fast_len, rfft
from scipy.ndimage import median_filter
from scipy.signal.windows import hann

if __package__:
    from .common import open_recording, probe
    from .stereo import channel_pairs
else:
    from common import open_recording, probe
    from stereo import channel_pairs


Component = Literal["mix", "harmonic", "percussive", "mid", "side"]
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


def spectral_blocks(recording, component):
    hop = next_fast_len(max(1, round(recording.samplerate * .0116)), real=True)
    window = hann(4 * hop, sym=False)
    # Align blocks on the same STFT grid and retain all neighbouring median windows.
    context = (MEDIAN_WIDTH // 2 + 4) * hop
    block_size = 96 * hop
    for start in range(0, len(recording), block_size):
        stop = min(len(recording), start + block_size)
        left, right = max(0, start - context), min(len(recording), stop + context)
        recording.seek(left)
        samples = recording.read(right - left, dtype="float64", always_2d=True)
        selected = slice(start - left, stop - left)
        original = samples[selected]
        part = harmonic_part(samples, hop, window)[selected]
        yield original, part if component == "harmonic" else original - part


def stereo_blocks(recording, pairs, component):
    for samples in recording.blocks(blocksize=65536, dtype="float64", always_2d=True):
        part = samples.copy() if component == "mid" else np.zeros_like(samples)
        for pair in pairs:
            left, right = (i - 1 for i in pair["channels"])
            if component == "mid":
                part[:, left] = part[:, right] = (samples[:, left] + samples[:, right]) * .5
            else:
                difference = (samples[:, left] - samples[:, right]) * .5
                # A listener that folds the output to mono must still hear the side signal.
                part[:, left] = part[:, right] = difference
        yield samples, part


def focus_recording(wav, component, out):
    """Return a playable component and levels on the original channel/sample clock."""
    if component not in COMPONENTS:
        raise ValueError("choose a listening component from " + ", ".join(COMPONENTS))
    wav = Path(wav)
    if component == "mix":
        return wav, None
    out = Path(out)
    dest = out / f"{wav.stem}_{component}.wav"
    with tempfile.TemporaryDirectory(dir=out) as tmp, open_recording(wav) as recording:
        rate, channels, length = recording.samplerate, recording.channels, len(recording)
        if length == 0:
            raise ValueError("the listening range contains no audio samples")
        layout = probe(wav).get("audio_layout", "")
        detail = {"basis": BASIS}
        if component in ("mid", "side"):
            pairs = channel_pairs(channels, layout)
            if not pairs:
                raise ValueError("mid and side need a channel pair; choose mix for this recording")
            detail = {"pairs": pairs, "basis": (
                "Mid is the half-sum of each listed pair. Side is half the left channel minus half the right. "
                "The chosen signal plays in phase on both channels so it remains audible in mono. Mid plus side "
                "restores the left channel; mid minus side restores the right. Unpaired channels play only in mid. "
                "A source can occur in both. Levels are measured before playback gain.")}
            blocks = stereo_blocks(recording, pairs, component)
        else:
            blocks = spectral_blocks(recording, component)
        energy = np.zeros((2, channels))
        raw = Path(tmp) / "component.f32"
        with raw.open("wb") as output:
            for original, part in blocks:
                energy[0] += np.square(original).sum(axis=0)
                energy[1] += np.square(part).sum(axis=0)
                output.write(part.astype("<f4").tobytes())
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-f", "f32le", "-ar", str(rate), "-ac", str(channels)]
        if layout:
            cmd.extend(["-channel_layout", layout])
        rendered = Path(tmp) / "component.wav"
        subprocess.run([*cmd, "-i", str(raw), "-map_metadata", "-1", "-c:a", "pcm_f32le", "-rf64", "auto",
                        str(rendered)], check=True, capture_output=True)
        rendered.replace(dest)
    mix_db, component_db = [[round(float(10 * np.log10(e / length)), 2) if e else None for e in row]
                            for row in energy]
    return dest, {"name": component, **detail, "mix_rms_dbfs": mix_db, "component_rms_dbfs": component_db}


def playback_volume(wav, gain_db, out):
    """Apply one gain to every released sample, limited by the loudest channel."""
    wav = Path(wav)
    if not math.isfinite(gain_db):
        raise ValueError("playback gain in dB must be finite")
    if gain_db == 0:
        return wav, None
    peak = 0.
    with open_recording(wav) as recording:
        for block in recording.blocks(blocksize=65536, dtype="float64", always_2d=True):
            peak = max(peak, float(np.max(np.abs(block), initial=0.)))
    peak_db = 20 * math.log10(peak) if peak else None
    applied = min(gain_db, -peak_db) if peak else 0.
    detail = {"requested_db": gain_db, "applied_db": applied, "peak_before_dbfs": peak_db,
              "basis": "One gain across the selected channels and interval. Peak limited to full scale by "
                       "reducing the gain for the whole excerpt. The replay WAV and any waveform description "
                       "use this playback level."}
    if applied == 0:
        return wav, detail
    dest = Path(out) / f"{wav.stem}_gain_{float(gain_db)}.wav"
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        rendered = Path(tmp) / "volume.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(wav), "-map", "0:a:0",
                        "-af", f"volume={applied}dB:precision=double", "-map_metadata", "-1",
                        "-c:a", "pcm_f32le", "-rf64", "auto", str(rendered)], check=True, capture_output=True)
        rendered.replace(dest)
    return dest, detail
