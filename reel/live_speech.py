"""Recognize speech on the film clock from the audio delivered during playback."""
from contextlib import redirect_stdout
from difflib import SequenceMatcher
from functools import partial
import json
import math
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import numpy as np

if __package__:
    from .config import ASR_PY
    from .local_reader import LocalReader
    from .asr import SR, speech_regions, speech_words, transcribe, word_tokens
else:
    from config import ASR_PY
    from local_reader import LocalReader
    from asr import SR, speech_regions, speech_words, transcribe, word_tokens

CONTEXT_SECONDS = 8
LISTENING_PEAK = 10 ** (-3 / 20)
MAX_LISTENING_GAIN = 100.0


def voiced_gap(word, channel):
    # Bound timing by the longest activity interval intersecting this word.
    intervals = [(max(word["t"], a), min(word["e"], b)) for a, b in channel["activity"]]
    start, end = max(intervals, key=lambda pair: pair[1] - pair[0], default=(0, 0))
    if start < end and all(start >= w["e"] or end <= w["t"] for w in channel["words"]):
        return start, end
    return None


def aligned_words(words, other):
    """Yield complete words and their token spans in the independent reading."""
    tokens = []
    spans = []
    for word in words:
        first = len(tokens)
        tokens.extend(word_tokens(word["w"]))
        spans.append((first, len(tokens)))
    agreed = SequenceMatcher(None, tokens, other, autojunk=False).get_matching_blocks()
    for word, (first, stop) in zip(words, spans):
        for a, b, size in agreed:
            if a <= first < stop <= a + size:
                yield word, b + first - a, b + stop - a
                break


def matching_gap_words(channel, candidates, independent):
    """Words already on the clock have first claim on an occurrence in the transcript."""
    other = word_tokens(independent)
    if other == ["unclear"]:
        return []
    anchors = list(aligned_words(channel["words"], other))
    # Two readers can give a sneeze the same spelling. A new word must belong
    # to a speech interval where they also recognize an already supported word.
    established = [(a, b) for a, b in channel["activity"]
                   if any(w["t"] < b and w["e"] > a for w, _, _ in anchors)]
    pending = []
    for word in candidates:
        if (gap := voiced_gap(word, channel)) and any(a <= gap[0] and gap[1] <= b for a, b in established):
            pending.append(dict(word, t=gap[0], e=gap[1]))
    anchors.append(({"t": math.inf, "e": math.inf}, len(other), len(other)))
    left, offset = -math.inf, 0
    additions = []
    for anchor, first, stop in anchors:
        between = [w for w in pending if left <= w["t"] and w["e"] <= anchor["t"]]
        for word, _, _ in aligned_words(between, other[offset:first]):
            if all(word["t"] >= w["e"] or word["e"] <= w["t"] for w in additions):
                additions.append(word)
        left, offset = anchor["e"], stop
    return additions


class SpeechReader(LocalReader):
    def __init__(self, waveform=None):
        super().__init__([ASR_PY, str(Path(__file__).resolve())], "Silero VAD + Whisper")
        self.waveform = waveform

    def read(self, directory, view, lang, history=None, saved=None):
        audio = view["audio"]
        job = {"wav": str((directory / audio["wav"]).resolve()), "rate": audio["rate_hz"],
               "channels": audio["channels"], "first_sample": audio["first_sample_on_film"],
               "end": view["end_s"], "lang": lang, "history_files": history}
        return self.recognize(job, saved)

    def read_excerpt(self, wav, rate, channels, first, end, lang, *, verify=False):
        job = {"wav": str(Path(wav).resolve()), "rate": rate, "channels": channels,
               "first_sample": first, "end": end, "lang": lang, "history_files": []}
        result = self.recognize(job) if verify else self.request(job)
        return {**result, "basis": "Only the selected recording enters this speech reading. Activity intervals are "
                                   "estimated voice. Timed words are supported by two listening windows. "
                                   "A short or unclear voice may yield no intervals or words."}

    def recognize(self, job, saved=None):
        if self.waveform is None or self.waveform.failure:
            return self.request(job)
        if saved:
            job["existing_windows"] = saved.get("windows", [])
        with TemporaryDirectory() as tmp:
            result = self.request(dict(job, check_dir=tmp))
            if result.get("status") != "available":
                return result
            status = "no_candidates"
            for window in result["windows"]:
                for row in window["channels"]:
                    check = row.pop("_check", None)
                    if check is None:
                        continue
                    if "file" not in check:
                        status = "unavailable"
                        continue
                    independent = self.waveform.transcribe(Path(tmp) / check["file"])
                    if independent["status"] == "available":
                        possible = matching_gap_words(row, check["words"], independent["text"])
                        if possible:
                            row["possible_words"] = possible
                        if status != "unavailable":
                            status = "available"
                    else:
                        status = "unavailable"
            result["independent_word_check"] = status
            return result

    def inspect(self, directory, lang=None, *, start_s=None, end_s=None):
        # The presenter assembles playback history. The speech worker needs only audio dependencies.
        if __package__:
            from .turn_audio import preceding_audio, released_samples
        else:
            from turn_audio import preceding_audio, released_samples
        directory = Path(directory).resolve()
        view = json.loads((directory / "view.json").read_text())
        audio = view.get("audio")
        if view.get("schema") != "reel.play.v1" or not audio:
            raise ValueError("no recorded audio in this playback interval")
        if start_s is not None or end_s is not None:
            if start_s is None or end_s is None or not start_s < end_s <= start_s + CONTEXT_SECONDS:
                raise ValueError("give both ends of a speech interval up to eight seconds long")
            wav, _, end, first, _ = released_samples(directory, start_s, end_s, CONTEXT_SECONDS)
            return self.read_excerpt(wav, audio["rate_hz"], audio["channels"], first, end, lang, verify=True)
        saved = view.get("speech", {})
        cached = saved.get("status") == "available" and saved.get("language_hint") == lang
        if cached and (self.waveform is None or self.waveform.failure
                       or saved.get("independent_word_check") in ("available", "no_candidates")):
            return saved
        history = [{"wav": str(wav), "first_sample": packet["audio"]["first_sample_on_film"]}
                   for wav, packet in preceding_audio(directory, view, max(0, view["end_s"] - CONTEXT_SECONDS))]
        first_sample = history[-1]["first_sample"] if history else audio["first_sample_on_film"]
        if cached and any(round(w["start_s"] * audio["rate_hz"]) < first_sample for w in saved.get("windows", [])):
            # A copied turn may retain a transcript of earlier audio that was not copied with it.
            return saved
        result = self.read(directory, view, lang, list(reversed(history)), saved if cached else None)
        if cached:
            previous = {(w["start_s"], w["end_s"]) for w in saved.get("windows", [])}
            checked = {(w["start_s"], w["end_s"]) for w in result.get("windows", [])}
            if result.get("status") != "available" or not previous.issubset(checked):
                return saved
        return result


def decode(wav, channels, skip=0):
    trim = ["-af", f"atrim=start_sample={skip}"] if skip else []
    result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", wav, "-map", "0:a:0", *trim,
                             "-c:a", "pcm_f64le", "-f", "f64le", "-"], capture_output=True)
    if result.returncode:
        raise ValueError("could not decode the published recording")
    return np.frombuffer(result.stdout, dtype="<f8").reshape(-1, channels)


def outside_speech_activity(channel, rate, start, end, activity):
    """Measure the original channel in the gaps between the detector's 16 kHz intervals."""
    def sample_index(t):
        # Map the detector's integer clock back before rounding up to a native sample.
        if t >= end:
            return len(channel)
        tick = round((t - start) * SR)
        return min(len(channel), max(0, (tick * rate + SR - 1) // SR))

    gaps = []
    cursor = 0
    for a, b in [*activity, (end, end)]:
        stop = sample_index(a)
        if cursor < stop:
            peak = float(np.max(np.abs(channel[cursor:stop])))
            gap = {"start_s": start + cursor / rate, "end_s": min(end, start + stop / rate),
                   "signal": "nonzero" if peak else "digital_silence"}
            if peak:
                gap["peak_dbfs"] = round(20 * math.log10(peak), 1)
            gaps.append(gap)
        cursor = max(cursor, sample_index(b))
    return gaps


class LiveSpeech:
    def __init__(self):
        self.history = None
        self.next_sample = None
        self.rate = None

    def window(self, samples, rate, start, end, lang, check_dir=None, existing=()):
        from scipy.signal import resample_poly

        channels = []
        divisor = math.gcd(SR, rate)
        # One gain for all channels keeps the recorded balance intact. Limit the
        # boost to 40 dB so arbitrarily small sample noise is never normalized.
        peak = float(np.max(np.abs(samples), initial=0))
        gain = LISTENING_PEAK / max(peak, LISTENING_PEAK / MAX_LISTENING_GAIN) if peak else 1.
        for i, channel in enumerate(samples.T):
            duplicate = next((c for c in channels if np.array_equal(channel, samples[:, c["channels"][0] - 1])), None)
            if duplicate is not None:
                duplicate["channels"].append(i + 1)
                continue
            signal = channel.astype(np.float32, copy=False)
            if rate != SR:
                signal = resample_poly(signal, SR // divisor, rate // divisor)
            previous = next((row for row in existing if i + 1 in row["channels"]), None)
            if previous is not None:
                activity, timed = previous["activity"], list(previous["words"])
            else:
                regions = speech_regions(signal) if np.any(signal) else []
                words = speech_words(signal, regions, lang, .5, recognize=partial(transcribe, deterministic=True))
                activity = [[start + a / SR, min(end, start + b / SR)] for a, b in regions if start + a / SR < end]
                timed = [dict(w, t=max(start, start + w["t"]), e=min(end, start + w["e"])) for w in words]
                timed = [w for w in timed if w["t"] < w["e"]]
            row = {"channels": [i + 1], "activity": activity, "words": timed}
            if gain > 1 and np.any(signal):
                # A gained pass can expose a faint voice. It never enters ASR
                # or supplies anchors for the independent word check.
                assisted = []
                for a, b in speech_regions(signal * gain, pad_ms=0):
                    begin, stop = start + a / SR, min(end, start + b / SR)
                    if begin < stop and all(stop <= t or begin >= e for t, e in activity):
                        assisted.append([begin, stop])
                if assisted:
                    row["voice_with_gain"] = {"gain_db": round(20 * math.log10(gain), 1), "activity": assisted}
            if activity and (gaps := outside_speech_activity(channel, rate, start, end, activity)):
                row["outside_speech_activity"] = gaps
            if check_dir and timed:
                try:
                    full = transcribe(signal, lang, start, deterministic=True)
                    if any(voiced_gap(w, row) for w in full):
                        name = f"{float(start).hex()}-{i}.npy"
                        np.save(Path(check_dir) / name, signal, allow_pickle=False)
                        row["_check"] = {"file": name, "words": full}
                except (OSError, RuntimeError, ValueError):
                    row["_check"] = {"status": "unavailable"}
            matching = next((c for c in channels if "_check" not in row and "_check" not in c
                             and c["activity"] == activity and c["words"] == timed
                             and c.get("voice_with_gain") == row.get("voice_with_gain")
                             and c.get("outside_speech_activity") == row.get("outside_speech_activity")), None)
            if matching is None:
                channels.append(row)
            else:
                matching["channels"].append(i + 1)
        return {"start_s": start, "end_s": end, "channels": channels}

    def read(self, wav, rate, channels, first_sample, end, lang=None, history_files=None, check_dir=None,
             existing_windows=()):
        # Keep history at the recording rate and resample the whole listening window
        # together. Pausing mid-word must not introduce a join in the waveform.
        samples = decode(wav, channels)
        width = rate * CONTEXT_SECONDS
        history = self.history
        if history_files is not None:
            past = [decode(item["wav"], channels, max(0, first_sample - width - item["first_sample"]))
                    for item in history_files]
            history = np.concatenate(past) if past else samples[:0]
        elif self.next_sample != first_sample or self.rate != rate or history is None or history.shape[1] != channels:
            history = samples[:0]
        windows = []
        for pos in range(0, len(samples), width):
            block = samples[pos:pos + width]
            count = min(len(history), width - len(block))
            context = np.concatenate((history[-count:], block)) if count else block
            last_sample = first_sample + pos + len(block)
            bounds = ((last_sample - len(context)) / rate, min(end, last_sample / rate))
            options = {"check_dir": check_dir} if check_dir else {}
            for old in existing_windows:
                if (old["start_s"], old["end_s"]) == bounds:
                    options["existing"] = old["channels"]
                    break
            windows.append(self.window(context, rate, *bounds, lang, **options))
            history = context
        # A replay supplies its own history and must not replace the live listening window.
        if history_files is None:
            self.history = history.copy()
            self.next_sample = first_sample + len(samples)
            self.rate = rate
        result = {"status": "available", "windows": windows}
        if any("voice_with_gain" in c for w in windows for c in w.get("channels", ())):
            result["voice_with_gain_basis"] = (
                "The voice detector found possible speech at higher listening gain on these channels. "
                "Intervals lie on the film clock. Background sounds may also trigger it. "
                "No words were decoded from the amplified signal.")
        if any("outside_speech_activity" in c for w in windows for c in w.get("channels", ())):
            result["outside_speech_activity_basis"] = (
                "Per-channel levels in the original recording, in gaps between estimated voice intervals. "
                "Digital silence means every sample there was zero. A nonzero peak does not identify a source. "
                "Sounds can also overlap a voice.")
        if lang is not None:
            result["language_hint"] = lang
        return result


def serve():
    reader = LiveSpeech()
    for line in sys.stdin:
        try:
            with redirect_stdout(sys.stderr):
                result = reader.read(**json.loads(line))
        except Exception as error:
            result = {"status": "unavailable", "reason": str(error),
                      "startup_failure": isinstance(error, (ImportError, OSError))}
            # Failed reads leave the last successful streaming window in place.
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["--setup"]:
        # Load the same VAD and transcription backend the offline worker will use.
        sample = np.zeros(SR, dtype=np.float32)
        speech_regions(sample)
        transcribe(sample, "en", deterministic=True)
    else:
        serve()
