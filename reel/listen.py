"""Extract audio from the decoded mix on the film's clock."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np

if __package__:
    from .sound_window import sound_window, validate_mark
    from .audio_focus import focus_recording, playback_volume
    from .common import audio_timeline, pcm_codec, probe, read_audio, reel_source, sample_boundary
    from .turn_audio import released_samples
    from .hearing import SoundReader, WINDOW_S
    from .config import COMPARE_PY
    from .sounds import AudioTagger, SOURCE_SCORE
    from .scene_audio import inspect_scene
    from .sound_events import inspect_events, ready as events_ready
    from .pitch import PitchTrace
    from .onsets import recorded_attacks
    from .stereo import StereoHistory
else:
    from sound_window import sound_window, validate_mark
    from audio_focus import focus_recording, playback_volume
    from common import audio_timeline, pcm_codec, probe, read_audio, reel_source, sample_boundary
    from turn_audio import released_samples
    from hearing import SoundReader, WINDOW_S
    from config import COMPARE_PY
    from sounds import AudioTagger, SOURCE_SCORE
    from scene_audio import inspect_scene
    from sound_events import inspect_events, ready as events_ready
    from pitch import PitchTrace
    from onsets import recorded_attacks
    from stereo import StereoHistory


def excerpt(out, t0, t1):
    out = Path(out)
    meta = json.loads((out / "meta.json").read_text())
    end = meta.get("until", meta["dur"])
    if not 0 <= t0 < t1 <= end:
        raise ValueError(f"listening range must be within 0 to {end:g} seconds")
    source = reel_source(out, meta)
    return _slice_audio(source, out, t0, t1)


def _slice_audio(source, out, t0, t1):
    info = probe(source)
    if not info["has_audio"]:
        raise ValueError("the film has no audio track")
    rate = info["audio_rate"]
    # Include exactly the samples whose presentation times fall in [t0, t1).
    start, stop = (sample_boundary(t, rate) for t in (t0, t1))
    if start == stop:
        raise ValueError("the interval contains no audio samples")
    filters = f"{audio_timeline(info)},apad=whole_len={stop},atrim=start_sample={start}:end_sample={stop}"
    target = out / f"audio_{start}-{stop}_{rate}Hz.wav"
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        wav = Path(tmp) / "excerpt.wav"
        job = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(source), "-map", f"0:{info['audio_index']}",
                              "-map_metadata", "-1", "-map_chapters", "-1", "-af", filters,
                              "-c:a", pcm_codec(info), "-rf64", "auto", str(wav)], capture_output=True)
        if job.returncode:
            raise ValueError(job.stderr.decode(errors="replace").strip())
        wav.replace(target)
    return target


def inspect_sound(out, start=None, end=None, *, compare_sounds=False, describe_sounds=False, question=None,
                  component="mix", mark_sound=None, gain_db=0., verify_sounds=False):
    out = Path(out)
    packet = out / "view.json"
    validate_mark(start, end, mark_sound)
    if packet.is_file():
        selected_range = start is not None or end is not None
        across_turns = mark_sound is not None or question is not None or (selected_range and (component != "mix" or gain_db != 0 or verify_sounds))
        if not across_turns:
            view = json.loads(packet.read_text())
            if view.get("schema") != "reel.play.v1" or not view.get("audio"):
                raise ValueError("no recorded audio in this playback interval")
            left, right = view["start_s"], view["end_s"]
            start = left if start is None else start
            end = right if end is None else end
            if not left <= start < end <= right:
                raise ValueError(f"listening range must be within {left:g} to {right:g} seconds")
        wav, start, end, first, _ = released_samples(out, start, end, WINDOW_S)
        return listening_evidence(wav, start, end, compare_sounds=compare_sounds, describe_sounds=describe_sounds,
                                  question=question, component=component, first_sample=first, mark_sound=mark_sound,
                                  turn_directory=out, gain_db=gain_db, verify_sounds=verify_sounds)
    if start is None or end is None:
        raise ValueError("specify --t0 and --t1 when listening to an existing reel")
    wav = excerpt(out, start, end)
    evidence = listening_evidence(wav, start, end, compare_sounds=compare_sounds, describe_sounds=describe_sounds,
                                  question=question, component=component, mark_sound=mark_sound, gain_db=gain_db,
                                  verify_sounds=verify_sounds)
    meta = json.loads((Path(out) / "meta.json").read_text())
    words = [w for w in meta.get("speech_with_gain", [])
             if start <= w["context_start_s"] and w["context_end_s"] <= end
             and start <= w["t"] < w["e"] <= end]
    if words and component == "mix":
        evidence["speech_with_gain"] = {
            "basis": "Candidate words from the separated voice that also appeared at the same time in "
                     "the amplified mix. The activity detector found no speech there at recorded gain. "
                     "The p values are scores from the stem decoder.",
            "words": words,
        }
    return evidence


def level_changes(signal, rate, origin, end):
    """Locate louder stretches in a channel without identifying their source."""
    # Align ten-millisecond bins to the excerpt. Integer arithmetic also works at 22.05 kHz.
    count = (len(signal) * 100 + rate - 1) // rate
    edges = (np.arange(count + 1, dtype=np.int64) * rate + 99) // 100
    edges = np.unique(np.minimum(edges, len(signal)))
    power = np.add.reduceat(np.square(signal, dtype=np.float64), edges[:-1]) / np.diff(edges)
    reference = float(np.percentile(power, 20))
    # A power ratio of ten is a 10 dB rise above this window's quieter bins.
    selected = np.flatnonzero(power > 10 * reference)
    runs = np.split(selected, np.flatnonzero(np.diff(selected) > 1) + 1) if selected.size else []
    # Keep the first and last changes, even when later peaks are much louder.
    kept = runs
    if len(runs) > 8:
        middle = sorted(runs[1:-1], key=lambda run: -power[run].max())[:6]
        kept = [runs[0], *sorted(middle, key=lambda run: run[0]), runs[-1]]
    intervals = []
    for run in kept:
        peak = run[np.argmax(power[run])]
        intervals.append({"start_s": round(origin + edges[run[0]] / rate, 6),
                          "end_s": min(end, round(origin + edges[run[-1] + 1] / rate, 6)),
                          "peak_s": round(origin + edges[peak] / rate, 6),
                          "peak_rms_dbfs": round(float(10 * np.log10(power[peak])), 1)})
    return {"reference_rms_dbfs": round(float(10 * np.log10(reference)), 1) if reference else None,
            "interval_count": len(runs), "intervals": intervals}


def listening_evidence(wav, start, end, *, compare_sounds=False, describe_sounds=False, question=None,
                       component="mix", first_sample=None, mark_sound=None, turn_directory=None, gain_db=0., verify_sounds=False):
    """Describe only this WAV using local measurements and cached AudioSet weights."""
    rate = 16000
    original = Path(wav)
    wav, focus = focus_recording(original, component, original.parent)
    playback, volume = playback_volume(wav, gain_db, original.parent)
    info = probe(wav)
    native_rate = info["audio_rate"]
    native = read_audio(wav, native_rate, channels=None)
    samples = native if native_rate == rate else read_audio(wav, rate, channels=None)
    report = {"schema": "reel.listen.v1", "wav": str(wav), "level_rate_hz": native_rate, "tagger_rate_hz": rate,
              "level_changes": "10 ms channel RMS above the window's 20th percentile by 10 dB. "
                               "If the reference is digital silence, any nonzero bin qualifies. "
                               "Up to eight runs are shown: first, last and strongest in between. Times describe level only.",
              "windows": []}
    if mark_sound is not None:
        origin = first_sample if first_sample is not None else sample_boundary(start, native_rate)
        window = sound_window(original.parent, original, start, end, origin, mark_sound, turn_directory)
        window["file"] = str((original.parent / window["file"]).resolve())
        report["recorded_sound_window"] = window
    if focus:
        report.update(component=focus, mix_wav=str(original))
        report["window_basis"] = "Levels, source candidates and spectral matches below refer to the filtered sound."
    try:
        tagger = AudioTagger(local_only=True)
    except (ImportError, OSError, RuntimeError, ValueError):
        tagger = None
        report["source_estimates"] = "unavailable; local AudioSet model or dependencies missing"
    else:
        report["source_estimates"] = "AudioSet candidates; scores are uncalibrated. No words are transcribed here."
    # The model was trained on ten-second clips. A final overlapping window covers the tail.
    width = 10 * native_rate
    last = max(0, len(native) - width)
    positions = list(range(0, last + 1, width // 2))
    if positions[-1] != last:
        positions.append(last)
    if first_sample is None:
        first_sample = sample_boundary(start, native_rate)
    origin = first_sample / native_rate
    for pos in positions:
        segment = native[pos:pos + width]
        stop = pos + len(segment)
        chunk = samples[round(pos * rate / native_rate):round(stop * rate / native_rate)]
        peak = float(np.max(np.abs(chunk), initial=0))
        # Apply one gain to all channels for the second listen, retaining their relative levels.
        gain = max(1.0, 10 ** (-3 / 20) / peak) if peak else 1.0
        window = {"start_s": round(origin + pos / native_rate, 6),
                  "end_s": min(end, round(origin + stop / native_rate, 6)),
                  "listening_gain_db": round(20 * np.log10(gain), 1), "channels": []}
        report["windows"].append(window)
        for i, signal in enumerate(chunk.T):
            power = np.mean(np.square(segment[:, i], dtype=np.float64))
            amplitude = float(np.max(np.abs(segment[:, i])))
            row = {"channel": i + 1, "digital_silence": not bool(amplitude),
                   "rms_dbfs": round(float(10 * np.log10(power)), 1) if power else None,
                   "peak_dbfs": round(float(20 * np.log10(amplitude)), 1) if amplitude else None,
                   "level_changes": level_changes(segment[:, i], native_rate, origin + pos / native_rate, end)}
            window["channels"].append(row)
            if tagger is None or not power or len(signal) < 400:
                continue
            views = {"recorded": signal}
            if gain > 1:
                views["with_gain"] = signal * gain
            for name, scores in zip(views, tagger.scores(list(views.values()))):
                candidates = [{"sound": tagger.labels[j], "score": round(float(scores[j]), 3)}
                              for j in np.argsort(scores)[::-1] if scores[j] >= SOURCE_SCORE and tagger.labels[j] != "Silence"]
                row[name] = candidates[:3]
    if info["audio_channels"] > 1:
        cues = StereoHistory().read(wav, start, end, info.get("audio_layout", ""))
        if cues:
            report["stereo_cues"] = cues
    report["pitch_contour"] = PitchTrace().read(wav, first_sample)
    report["attacks"] = recorded_attacks(wav, first_sample)
    if volume:
        report.update(wav=str(playback), recorded_wav=str(original), measured_wav=str(wav), playback_gain=volume)
    if events_ready():
        report["sound_events"] = inspect_events(original, first_sample, end)
        if volume:
            report["playback_sound_events"] = (report["sound_events"] if playback == original else
                                              inspect_events(playback, first_sample, end))
    report["scene"] = inspect_scene(original, first_sample, end, verify_sounds=verify_sounds)
    if focus:
        report["scene_basis"] = "The scene reader heard the original mix."
    if compare_sounds:
        ranges = [(p / native_rate, min(len(native), p + width) / native_rate) for p in positions]
        try:
            proc = subprocess.run([COMPARE_PY, str(Path(__file__).with_name("compare_sounds.py")), str(wav)],
                                  input=json.dumps(ranges), text=True, capture_output=True, check=True)
            matches = json.loads(proc.stdout)
            for window, names in zip(report["windows"], matches, strict=True):
                window["neighbours"] = names
            report["similarity_search"] = ("Top three relative matches among 527 AudioSet descriptions, ranked by local "
                                           "CLAP cosine similarity averaged over non-silent channels. All three can be "
                                           "wrong. These scores establish neither source presence nor words.")
        except (OSError, ValueError, subprocess.SubprocessError):
            report["similarity_search"] = "unavailable; check reel doctor and reel setup --compare-sounds"
    if describe_sounds or question is not None:
        with SoundReader() as reader:
            comparison = {"mix_wav": original, "component": component} if focus else {}
            report["sound_descriptions"] = reader.read(playback, start, end, question=question, **comparison)
    return report
