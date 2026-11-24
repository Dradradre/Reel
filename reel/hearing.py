"""Local waveform descriptions and optional readings of synchronized pictures and sound."""
from __future__ import annotations

import argparse
from contextlib import nullcontext, redirect_stdout
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unicodedata

import numpy as np

if __package__:
    from .joint import ANALYSIS_SAMPLES, RATE, encoder_waveform
    from .local_reader import LocalReader
    from .common import open_recording, sample_boundary
    from .config import HEAR_ENV, HEAR_PY, MODELS
else:
    from joint import ANALYSIS_SAMPLES, RATE, encoder_waveform
    from local_reader import LocalReader
    from common import open_recording, sample_boundary
    from config import HEAR_ENV, HEAR_PY, MODELS

MODEL = "mlx-community/Qwen3-Omni-30B-A3B-Instruct-8bit"
REVISION = "c87d8fc0e6df75501155432850f3f236523c2f1a"
LIVE_CONTEXT_S = 8
WINDOW_S = 30
PROMPT = ("Listen to the recording. In one short sentence describe any voices and other sounds you can hear. "
          "Describe the sound of speech without quoting it or retelling its meaning. "
          "Name a source only when you recognize its sound.")


def prepare_environment():
    # The optional audio model and the default sound tagger need different
    # versions of Transformers. Keep the installed tagger usable after setup.
    if "REEL_HEAR_PYTHON" not in os.environ and Path(HEAR_PY) == HEAR_ENV / "bin" / "python":
        uv = shutil.which("uv")
        if not Path(HEAR_PY).exists():
            create = [uv, "venv", "--python", "3.12"] if uv else [sys.executable, "-m", "venv"]
            subprocess.run([*create, str(HEAR_ENV)], check=True)
        check = [HEAR_PY, "-c", "import mlx_vlm, scipy.signal, soundfile"]
        if subprocess.run(check, capture_output=True).returncode:
            install = [uv, "pip", "install", "--python", HEAR_PY] if uv else [HEAR_PY, "-m", "pip", "install"]
            subprocess.run([*install, "mlx-vlm==0.8.0", "scipy", "soundfile"], check=True)
    subprocess.run([HEAR_PY, str(Path(__file__).resolve()), "--setup"], check=True)


def weights(download=False):
    from huggingface_hub import snapshot_download

    for cache in (MODELS / "hub", None):
        try:
            root = Path(snapshot_download(MODEL, revision=REVISION, cache_dir=cache, local_files_only=True))
            index = json.loads((root / "model.safetensors.index.json").read_text())
            files = set(index["weight_map"].values()) | {"config.json", "tokenizer.json", "preprocessor_config.json"}
            if all((root / f).is_file() for f in files):
                return root
        except (OSError, ValueError, KeyError):
            continue
    if download:
        return Path(snapshot_download(MODEL, revision=REVISION, cache_dir=MODELS / "hub"))
    raise OSError("local audio model missing; run reel setup --describe-sounds")


def load_encoder_audio(wav, start=None, end=None):
    with open_recording(wav) as recording:
        count = -1
        if start is not None:
            count = max(0, sample_boundary(end, recording.samplerate) - sample_boundary(start, recording.samplerate))
        return encoder_waveform(recording.read(count, dtype="float32", always_2d=True), recording.samplerate)


def measured_spectrum(samples, rate):
    """Recorded level, frequency spread, and concentration in a roughly 94 Hz band."""
    power = np.mean(np.square(samples, dtype=np.float64))
    row = {"rms_dbfs": round(float(10 * np.log10(power)), 1) if power else None}
    fft = max(4, 2 * round(.016 * rate))
    if not power or len(samples) < fft:
        return row
    frames = np.lib.stride_tricks.sliding_window_view(samples, fft)
    offsets = np.arange(0, len(frames), fft // 2)
    if offsets[-1] != len(frames) - 1:
        offsets = np.append(offsets, len(frames) - 1)
    # Bound scratch space even for high-rate recordings.
    window = np.hanning(fft)
    spectrum = np.zeros(fft // 2 + 1)
    for i in range(0, len(offsets), 64):
        selected = frames[offsets[i:i + 64]] * window
        spectrum += (np.abs(np.fft.rfft(selected)) ** 2).sum(axis=0)
    spectrum[1:-1] *= 2
    total = spectrum.sum()
    if total:
        bands = np.convolve(spectrum, np.ones(3), mode="valid")
        strongest = int(bands.argmax())
        edges = np.searchsorted(np.cumsum(spectrum), total * np.array([.1, .9]))
        row.update(band_center_hz=round((strongest + 1) * rate / fft, 1),
                   band_power_fraction=round(float(bands[strongest] / total), 4),
                   middle_80_percent_hz=np.round(edges * rate / fft, 1).tolist())
    return row


def spectrum_profile(channel, start, end):
    bins = min(4, max(1, len(channel) // (RATE // 4)))
    edges = np.linspace(0, len(channel), bins + 1, dtype=int)
    times = np.linspace(start, end, bins + 1)
    return [{"start_s": float(times[i]), "end_s": float(times[i + 1]),
             **measured_spectrum(channel[a:b], RATE)} for i, (a, b) in enumerate(zip(edges, edges[1:]))]


def audiovisual_positions(media, config, grid_onsets=None):
    """Align one unpadded audiovisual prompt. Explicit grid onsets also reorder its media placeholders."""
    ids = np.asarray(media["input_ids"])
    if ids.shape[0] != 1 or not np.all(np.asarray(media["mask"])):
        raise ValueError("expected one unpadded audiovisual prompt")
    ids = ids[0]
    video = ids == config.video_token_id
    audio = ids == config.audio_token_id
    content = np.flatnonzero(video | audio)
    if not np.any(audio) or not np.any(video) or content[-1] - content[0] + 1 != len(content):
        raise ValueError("missing or interrupted audiovisual tokens")
    first, last = int(content[0]), int(content[-1])
    merge = config.vision_config.spatial_merge_size
    grids, rows, cols = map(int, np.asarray(media["video_grid_thw"])[0])
    grid = np.indices((grids, rows // merge, cols // merge), dtype=np.float32).reshape(3, -1)
    if grid.shape[1] != video.sum():
        raise ValueError("the video tokens do not cover the encoded picture grid")
    if grid_onsets is None:
        grid[0] *= np.asarray(media["video_second_per_grid"]).item()
    else:
        onsets = np.asarray(grid_onsets, dtype=np.float64)
        if (onsets.shape != (grids,) or not np.all(np.isfinite(onsets)) or onsets[0] != 0
                or np.any(np.diff(onsets) <= 0)):
            raise ValueError("expected one increasing presentation time per video grid, starting at zero")
        grid[0] = onsets[grid[0].astype(np.intp)]
    grid[0] *= config.position_id_per_seconds
    if grid_onsets is not None:
        # The processor built placeholders assuming constant fps. Reinterleave just those
        # placeholders to match the real clock. Encoded pixels and audio stay in their own order.
        video_at = np.searchsorted(np.arange(audio.sum()), grid[0], side="left") + np.arange(video.sum())
        interleaved = np.full(len(content), config.audio_token_id, dtype=ids.dtype)
        interleaved[video_at] = config.video_token_id
        ids = ids.copy()
        ids[content] = interleaved
        media["input_ids"] = ids[None]
        video = ids == config.video_token_id
        audio = ids == config.audio_token_id
    # A whole picture patch and a simultaneous audio token share a time coordinate.
    # Text following the pair continues from the final media position, including both end markers.
    positions = np.tile(np.arange(len(ids), dtype=np.float32), (3, 1))
    positions[:, video] = first + grid
    positions[:, audio] = first + np.arange(audio.sum(), dtype=np.float32)
    positions[:, last + 1:] = positions[:, last].max() + 1 + np.arange(len(ids) - last - 1)
    delta = np.array([[positions.max() + 1 - len(ids)]], dtype=np.float32)
    return positions[:, None], delta


class WaveListener:
    def __init__(self):
        from mlx_vlm import load, generate
        from mlx_vlm.prompt_utils import apply_chat_template
        import mlx.core as mx

        self.load = load
        self.template = apply_chat_template
        self.generate = generate
        self.array = mx.array
        self.model = None

    def __call__(self, signal, question=None):
        if self.model is None:
            self.model, self.processor = self.load(str(weights()))
        instruction = PROMPT if question is None else (
            "Listen to this recording to answer the listener’s question. Answer briefly, with the sound you used "
            "as evidence. Distinguish hearing an event from hearing someone talk about it. "
            "If the recording does not settle the question, say so. Do not quote dialogue.\n" + question)
        prompt = self.template(self.processor, self.model.config, instruction, num_audios=1)
        result = self.generate(self.model, self.processor, prompt, audio=[signal.copy()],
                               temperature=0., max_tokens=72, verbose=False)
        return result.text.strip()

    def transcribe(self, signal):
        if self.model is None:
            self.model, self.processor = self.load(str(weights()))
        instruction = ("If there is intelligible speech in this recording, transcribe exactly what you hear, "
                       "in its original language. If you cannot make out words or syllables, answer unclear. "
                       "Give only your answer.")
        messages = [{"role": "user", "content": [{"type": "audio"}, {"type": "text", "text": instruction}]}]
        prompt = self.processor.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        result = self.generate(self.model, self.processor, prompt, audio=[signal], temperature=0.,
                               max_tokens=128, verbose=False)
        return result.text.strip()

    def compare(self, focused, original, component, question=None):
        if self.model is None:
            self.model, self.processor = self.load(str(weights()))
        emphasis = {"harmonic": "sustained frequency bands", "percussive": "brief broadband changes",
                    "mid": "the average waveform of the two paired channels",
                    "side": "the waveform difference between the two paired channels"}[component]
        instruction = (f"These are two versions of the same interval. The first emphasizes {emphasis}. "
                       "The second is the original recording. Use the first to locate a sound, then listen to "
                       "the original to decide what is audible. " + PROMPT)
        if question is not None:
            instruction += "\nAnswer the question from the original recording. " + question
        prompt = self.template(self.processor, self.model.config, instruction, num_audios=2)
        # The generic mlx-vlm media loader keeps only the first recording for this processor.
        media = dict(self.processor(text=[prompt], audio=[focused.copy(), original.copy()], padding=True,
                                    return_tensors="np", return_attention_mask=True, add_special_tokens=False))
        token = self.processor.tokenizer.convert_tokens_to_ids(self.processor.audio_token)
        positions = np.flatnonzero(media["input_ids"][0] == token)
        if (media["input_features"].shape[0] != 2 or not len(positions)
                or np.count_nonzero(np.diff(positions) > 1) != 1):
            raise ValueError("the local reader did not encode both recordings")
        media["mask"] = media.pop("attention_mask")
        return self.generate(self.model, self.processor, prompt, **media,
                             temperature=0., max_tokens=72, verbose=False).text.strip()

    def with_picture(self, signal, layout, visual, fps, start, end, question):
        if self.model is None:
            self.model, self.processor = self.load(str(weights()))
        if layout in ("video", "timed_video"):
            placeholders = {"video": "bounded interval"}
            layout_text = "The moving pictures and recording cover the same interval."
        else:
            placeholders = {"num_images": len(visual), "num_audios": 1}
            layout_text = "The timestamped pictures run left to right then top to bottom through the recording."
        instruction = (f"Film time {start:.6f} to {end:.6f} seconds. {layout_text} "
                       "Describe briefly what is audible and what visibly moves. If the source is identifiable, "
                       "say what action and timing support it. Distinguish heard sounds from events mentioned in speech. "
                       "Leave the source open when uncertain. Do not quote words.")
        if question:
            instruction += "\nListener question: " + question
        prompt = self.template(self.processor, self.model.config, instruction, **placeholders)
        if layout in ("video", "timed_video"):
            # mlx-vlm's generic media loader drops this processor's fps and
            # use_audio_in_video kwargs. Prepare its interleaved inputs directly.
            config = self.model.config.thinker_config
            grid_onsets = fps if layout == "timed_video" else None
            if grid_onsets is not None:
                # The processor requires an fps to construct a tensor. The explicit grid times
                # replace its uniform token clock before anything reaches the language model.
                if self.processor.video_processor.temporal_patch_size != 2 or len(visual) != 2 * len(grid_onsets):
                    raise ValueError("irregular video needs two input pictures per timed patch")
                fps = len(visual) / (end - start)
            media = dict(self.processor(text=[prompt], videos=[visual], audio=[signal.copy()],
                                        fps=fps, use_audio_in_video=True, padding=True,
                                        position_id_per_seconds=config.position_id_per_seconds,
                                        return_attention_mask=True, add_special_tokens=False))
            media["mask"] = media.pop("attention_mask")
            patch = self.processor.video_processor.temporal_patch_size
            seconds = media["video_second_per_grid"].item()
            if (not math.isclose(seconds, patch / fps, rel_tol=1e-5)
                    or media["video_grid_thw"][0, 0].item() != math.ceil(len(visual) / patch)):
                raise ValueError("the audiovisual encoder changed the released picture clock")
            # mlx-vlm 0.8.0 assigns ordinary text positions to an audio-in-video span.
            # Explicit positions reach the language model during prefill and subsequent decoding.
            positions, delta = audiovisual_positions(media, config, grid_onsets)
            if grid_onsets is not None:
                media["input_ids"] = self.array(media["input_ids"])
            media.update(position_ids=self.array(positions), rope_deltas=self.array(delta))
        else:
            media = {"image": visual, "audio": [signal.copy()]}
        return self.generate(self.model, self.processor, prompt, **media,
                             temperature=0., max_tokens=96, verbose=False).text.strip()


CONTROL_PROMPT = ("\nUse yes or no for a yes/no question, otherwise a short phrase. "
                  "If the recording gives insufficient evidence, answer unknown.")


def answer_key(text):
    """Compare surface answers across scripts without losing signs, digits or internal punctuation."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split()).rstrip(".!?。！？").strip()


def validate_question_control(question, enabled):
    if enabled and (not isinstance(question, str) or not question.strip()):
        raise ValueError("the silence comparison requires a listening question")


class ListeningHistory:
    def __init__(self, describe, model=MODEL):
        self.model = model
        self.describe = describe
        self.previous = None
        self.end = None
        self.start = None

    def read(self, wav, start, end, remember, question=None, mix_wav=None, component="mix", question_control=False):
        validate_question_control(question, question_control)
        signal = load_encoder_audio(mix_wav or wav, start, end)
        focus = None
        if mix_wav is not None:
            focus = load_encoder_audio(wav, start, end)
            if focus.shape != signal.shape or remember:
                raise ValueError("compare two recordings of the same released interval")
        if not len(signal):
            raise ValueError("no samples for the sound description")
        previous = self.previous if remember and self.end == start else None
        if previous is not None and previous.shape[1] != signal.shape[1]:
            previous = None
        width = (LIVE_CONTEXT_S if remember else WINDOW_S) * RATE
        # Balance longer requests so their final description has enough surrounding sound.
        count = (len(signal) + width - 1) // width
        size = (len(signal) + count - 1) // count
        previous_start = self.start
        windows = []
        for pos in range(0, len(signal), size):
            current = signal[pos:pos + size]
            focused = focus[pos:pos + size] if focus is not None else None
            current_start = start + pos / RATE
            left = current_start
            right = end if pos + len(current) == len(signal) else min(end, start + (pos + len(current)) / RATE)
            headroom = width - len(current)
            if headroom and previous is not None and len(previous):
                past = previous[-headroom:]
                context = np.concatenate([past, current])
                left = max(previous_start, left - len(past) / RATE)
            else:
                context = current
            context_start = left
            observed = context
            if not np.any(current):
                # Describe the newly heard silence while keeping the preceding sound in memory.
                observed, left = current, current_start
            channels = []
            silent_answer = None
            for i, channel in enumerate(observed.T):
                # Duplicated channels carry one signal. Opposite-polarity or independent
                # channels reach the encoder separately, without cancellation in a downmix.
                same = None
                for earlier in channels:
                    j = earlier["channels"][0] - 1
                    if (np.array_equal(channel, observed[:, j])
                            and (focused is None or np.array_equal(focused[:, i], focused[:, j]))):
                        same = earlier
                        break
                if same is not None:
                    same["channels"].append(i + 1)
                    continue
                row = {"channels": [i + 1], "digital_silence": not bool(np.any(channel))}
                active_now = bool(np.any(current[:, i]))
                if left < current_start:
                    row["current_signal"] = "nonzero" if active_now else "digital_silence"
                if active_now and len(channel) < ANALYSIS_SAMPLES:
                    row["status"] = "insufficient_encoder_input"
                elif active_now:
                    args = ()
                    if question is not None:
                        timed_question = (f"This recording begins at film time {left:.6f} s and ends at "
                                          f"{right:.6f} s. Its first sample is at elapsed time zero. "
                                          f"Use the film clock for times unless the listener asks for elapsed time.\n"
                                          f"Listener question: {question}")
                        if question_control:
                            timed_question += CONTROL_PROMPT
                        args = (timed_question,)
                    key = "answer" if question is not None else "impression"
                    if focused is None:
                        row[key] = self.describe(channel, *args)
                    else:
                        row[key] = self.describe.compare(focused[:, i], channel, component, *args)
                    if question_control:
                        if silent_answer is None:
                            silence = np.zeros_like(channel)
                            silent_answer = (self.describe(silence, *args) if focused is None else
                                             self.describe.compare(silence, silence, component, *args))
                        unchanged = answer_key(row[key]) == answer_key(silent_answer)
                        row["answer_on_silence"] = "unchanged" if unchanged else "different"
                        if unchanged:
                            del row[key]
                if not row["digital_silence"]:
                    row["measured_profile"] = spectrum_profile(channel, left, right)
                channels.append(row)
            window = {"start_s": left, "end_s": right, "channels": channels}
            if left < current_start:
                window["current_start_s"] = current_start
            windows.append(window)
            previous = context if remember else None
            previous_start = context_start
        # A short tail may still be a view into a much longer decoded interval.
        self.previous = previous.copy() if previous is not None and previous.base is not None else previous
        self.start = previous_start if remember else None
        self.end = end if remember else None
        return {**({"question": question} if question is not None else {}),
                **({"question_control": "The same question and time span were also given to the model with every "
                    "sample set to zero. Answers repeated on silence are withheld. Different wording alone can "
                    "pass this comparison. Use the speech evidence for quotable words."} if question_control else {}),
                "status": "available", "model": self.model, "encoder_rate_hz": RATE,
                "basis": ("The reader heard the filtered channel and the same original channel together. "
                          "Its description and measured profile refer to the original. " if focus is not None else "")
                         + "Audio-only model impressions over the stated windows. Source guesses are fallible. "
                         "Check whether a sound mentioned in speech has separate acoustic evidence. "
                         "Use the speech track for quotable words. No picture or filename entered the model.",
                "profile_basis": "Level, 10th to 90th power percentiles in Hz and strongest 93.75 Hz band share "
                                 "of measured power below 8 kHz. "
                                 "A band with a small share can be the maximum in broadband noise.",
                "windows": windows}


class SoundReader(LocalReader):
    def __init__(self):
        super().__init__([HEAR_PY, str(Path(__file__).resolve()), "--serve"], MODEL)
        if __package__:
            from . import flamingo
        else:
            import flamingo
        self.waveform = None
        backend = os.environ.get("REEL_AUDIO_READER", "qwen")
        if backend not in ("qwen", "flamingo"):
            raise ValueError("REEL_AUDIO_READER must be qwen or flamingo")
        if backend == "flamingo":
            self.waveform = LocalReader([sys.executable, str(Path(flamingo.__file__).resolve()), "--serve"], flamingo.MODEL)

    def for_replay(self):
        if self.waveform is not None and self.waveform.process is not None:
            return nullcontext(self)
        return super().for_replay()

    def close(self):
        if self.waveform is not None:
            self.waveform.close()
        super().close()

    def request(self, job):
        if self.waveform is not None and self.process is None and not job.get("operation") and not job.get("mix_wav"):
            return self.waveform.request(job)
        # Qwen handles joint, transcription and paired-waveform operations. Retire
        # the audio-only worker before loading the larger model in this session.
        if self.waveform is not None:
            self.waveform.close()
        return super().request(job)

    def transcribe(self, samples):
        """Read one private 16 kHz mono window, without a picture or proposed words."""
        return self.request({"operation": "transcribe", "samples": str(Path(samples).resolve())})

    def read(self, wav, start, end, *, remember=False, question=None, mix_wav=None, component="mix", question_control=False):
        validate_question_control(question, question_control)
        job = {"wav": str(Path(wav).resolve()), "start": start, "end": end, "remember": remember}
        if question_control:
            job["question_control"] = True
        if mix_wav is not None:
            job.update(mix_wav=str(Path(mix_wav).resolve()), component=component)
        if question is not None:
            if not question.strip():
                raise ValueError("supply a question or omit it for an open description")
            job["question"] = question
        return self.request(job)

    def joint(self, wav, start, end, first_sample, pages, size, picture_end, question=None, *, long_context=False):
        if question is not None and not question.strip():
            raise ValueError("supply a question or omit it for an open description")
        start, end = float(start), float(end)
        if pages and picture_end > start:
            result = self.request({"operation": "joint", "wav": str(Path(wav).resolve()),
                                   "start": start, "end": end, "first_sample": first_sample,
                                   "pages": pages, "size": size, "picture_end": float(picture_end),
                                   "question": question, "long_context": long_context})
        else:
            result = {"status": "no_simultaneous_picture", "requested_start_s": start, "requested_end_s": end,
                      "windows": []}
            if question is not None:
                result["question"] = question
        windows = result.get("windows", [])
        # A camera's frame rate, delayed first picture or early final frame must not set
        # the length of an auditory phrase. Reuse a complete open listen when it already exists.
        if (question is None and len(windows) == 1 and windows[0]["start_s"] == start and windows[0]["end_s"] == end
                and all(c["status"] in ("read", "digital_silence") for c in windows[0]["channels"])):
            channels = [{"channels": c["channels"], "digital_silence": c["status"] == "digital_silence",
                         **({"impression": c["audio_only"]} if c["status"] == "read" else {})}
                        for c in windows[0]["channels"]]
            recording = {"status": "available", "model": MODEL, "encoder_rate_hz": RATE,
                         "windows": [{"start_s": start, "end_s": end, "channels": channels}]}
        else:
            recording = self.read(wav, start, end, question=question)
        return {**result, "recording": recording}

    def inspect(self, directory, question=None, start=None, end=None, component="mix", gain_db=0.):
        if __package__:
            from .audio_focus import focus_recording, playback_volume
            from .stereo import StereoHistory
            from .turn_audio import released_recording
        else:
            from audio_focus import focus_recording, playback_volume
            from stereo import StereoHistory
            from turn_audio import released_recording
        wav, start, end = released_recording(directory, start, end, WINDOW_S)
        original = wav
        wav, focus = focus_recording(wav, component, wav.parent)
        playback, gain = playback_volume(wav, gain_db, wav.parent)
        comparison = {"mix_wav": original, "component": component} if focus else {}
        result = {"wav": str(playback), "start_s": start, "end_s": end,
                  "sound_descriptions": self.read(playback, start, end, question=question, **comparison)}
        if gain:
            result.update(playback_gain=gain, recorded_wav=str(original))
        if focus:
            result.update(component=focus, mix_wav=str(original))
        cues = StereoHistory().read(wav, start, end)
        if cues:
            result["stereo_cues"] = cues
        return result


def serve():
    listener = None
    ear = None
    for line in sys.stdin:
        try:
            with redirect_stdout(sys.stderr):
                if listener is None:
                    ear = WaveListener()
                    listener = ListeningHistory(ear)
                job = json.loads(line)
                # A replay or a question at this pause must not erase the ongoing first viewing.
                operation = job.pop("operation", None)
                if operation == "transcribe":
                    signal = np.load(job["samples"], allow_pickle=False)
                    if signal.ndim != 1 or not 0 < len(signal) <= (LIVE_CONTEXT_S * RATE + 1):
                        raise ValueError("expected one released mono speech window")
                    result = {"status": "available", "text": ear.transcribe(signal)}
                elif operation == "joint":
                    if __package__:
                        from .joint import read_joint
                    else:
                        from joint import read_joint
                    result = {"model": MODEL, **read_joint(ear, **job)}
                else:
                    active = listener if job["remember"] else ListeningHistory(ear)
                    result = active.read(**job)
        except Exception as error:
            result = {"status": "unavailable", "model": MODEL, "reason": str(error), "startup_failure": ear is None or ear.model is None}

        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    for option in ("--setup", "--check", "--serve"):
        mode.add_argument(option, action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve()
    else:
        import mlx_vlm
        import scipy.signal
        import soundfile
        weights(download=args.setup)
