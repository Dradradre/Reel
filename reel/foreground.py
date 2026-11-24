"""Rehear changes against an earlier background on the same recorded channel."""
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.fft import irfft, rfft
from scipy.signal.windows import hann

if __package__:
    from .common import open_recording
else:
    from common import open_recording


def read_channel(path, index):
    with open_recording(path) as wav:
        if not 1 <= index <= wav.channels:
            raise ValueError("the channel must exist in both recordings")
        return wav.read(dtype="float32", always_2d=True)[:, index-1], wav.samplerate


def contrast(background, recording, rate):
    """Keep target phase, attenuating bands already strong in the earlier reference."""
    hop = max(1, round(rate * .008))
    width = 4 * hop
    if len(background) < max(8*hop, round(.24*rate)) or len(recording) < width:
        return None
    if not np.any(background):
        return recording.copy()
    window = hann(width, sym=False).astype(np.float32)
    def frames(signal):
        return np.lib.stride_tricks.sliding_window_view(signal, width)[::hop]
    # Limit memory while estimating a background in a high-rate channel. Take up
    # to 128 evenly spaced complete windows over the explicitly selected interval.
    source = frames(background)
    selection = np.linspace(0,len(source)-1,min(128,len(source)),dtype=int)
    noise = np.percentile(np.abs(rfft(source[selection]*window))**2, 65, axis=0)
    padded = np.pad(recording, width)
    out = np.zeros_like(padded)
    target = frames(padded)
    for i in range(0,len(target),32):
        freq = rfft(target[i:i+32]*window)
        power = np.abs(freq)**2
        ratio = np.divide(noise, np.maximum(power,1e-20))
        gain = np.sqrt(np.clip(1-ratio, .01, 1))
        filtered = irfft(freq*gain,n=width)*window
        for j,block in enumerate(filtered):
            offset=(i+j)*hop
            out[offset:offset+width] += block
    return out[width:width+len(recording)]/1.5


def render(background, recording, channel, directory):
    bed, rate = read_channel(background, channel)
    mix, other = read_channel(recording, channel)
    if rate != other:
        raise ValueError("the background and target must have the same sample rate")
    if len(mix) > 10*rate or len(bed)>8*rate:
        raise ValueError("choose a background up to eight seconds and a target up to ten seconds")
    result = contrast(bed, mix, rate)
    if result is None:
        return {"status":"too_short_for_background_estimate"}
    paths = []
    for name, samples in (("earlier_channel",bed),("original_channel",mix),("foreground_estimate",result)):
        path = Path(directory)/(name+".wav")
        sf.write(path, samples, rate, subtype="FLOAT")
        paths.append(path)
    return {"status":"available", "rate_hz":rate, "channel":channel, "paths":paths,
            "basis":"The estimate attenuates frequency bands active in the earlier reference. "
                    "The same source can continue through both intervals. Listen to the original and estimate."}
