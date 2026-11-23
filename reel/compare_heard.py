"""Rehear two intervals on the same film clock at their recorded levels."""
from pathlib import Path

import numpy as np

if __package__:
    from .common import open_recording, sample_boundary
    from .compare_sounds import SoundMatcher
    from .pitch import PitchTrace
    from .hearing import LIVE_CONTEXT_S
    from .listen import excerpt
    from .turn_audio import released_samples
else:
    from common import open_recording, sample_boundary
    from compare_sounds import SoundMatcher
    from pitch import PitchTrace
    from hearing import LIVE_CONTEXT_S
    from listen import excerpt
    from turn_audio import released_samples


def recordings(directory, reference, target):
    directory = Path(directory)
    for bounds in (reference, target):
        if len(bounds) != 2 or not 0 <= bounds[0] < bounds[1] or bounds[1] - bounds[0] > LIVE_CONTEXT_S:
            raise ValueError(f"give a start and end for each recording, at most {LIVE_CONTEXT_S}s apart")
    live = (directory / "view.json").is_file()
    clips = {}
    for name, (start, end) in (("reference", reference), ("target", target)):
        if live:
            wav, start, end, first, stop = released_samples(directory, start, end, LIVE_CONTEXT_S)
        else:
            wav = excerpt(directory, start, end)
        with open_recording(wav) as recording:
            rate = recording.samplerate
            if not live:
                first, stop = (sample_boundary(t, rate) for t in (start, end))
            count = len(recording)
            if not count or count != stop - first:
                raise ValueError("recording does not cover the requested sample range")
            channels = recording.channels
            energy = np.zeros(channels)
            active = np.zeros(channels, dtype=bool)
            for block in recording.blocks(blocksize=32768, dtype="float64", always_2d=True):
                active |= block.any(axis=0)
                energy += np.einsum("ij,ij->j", block, block)
        clips[name] = {
            "wav": str(wav.resolve()), "start_s": start, "end_s": end, "rate_hz": rate,
            "first_sample_on_film": first, "samples_per_channel": count, "channels": channels,
            "rms_dbfs_by_channel": [round(float(10 * np.log10(e / count)), 1) if e else None for e in energy],
            "digital_silence_by_channel": (~active).tolist(),
            "pitch_contour": PitchTrace().read(wav, first),
        }
    return clips


def sound_similarity(clips, matcher=None, alternatives=None):
    options = {} if alternatives is None else {"alternatives": alternatives}
    with (matcher or SoundMatcher()).for_replay() as reader:
        return reader.compare_recordings(clips["reference"]["wav"], clips["target"]["wav"], **options)


def describe_recordings(reader, clips, question=None):
    """Each interpretation hears only its own samples. A later event cannot enter the reference reading."""
    if question is not None:
        if not question.strip():
            raise ValueError("supply a question or omit it for an open comparison")
        question = ("You hear only this one excerpt. Describe the audible clues in this excerpt relevant "
                    "to the following question. The listener will compare the recordings.\n" + question)
    return {name: reader.read(clip["wav"], clip["start_s"], clip["end_s"], question=question)
            for name, clip in clips.items()}
