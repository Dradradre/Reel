"""Read a continuous piece of the recording from already delivered playback turns."""
import json
from pathlib import Path
import subprocess
import tempfile

if __package__:
    from .common import sample_boundary
else:
    from common import sample_boundary


def sample_at(view, time):
    """Use the decoder's sample index at a published boundary, even when its time was rounded to a float."""
    audio = view["audio"]
    if "first_sample_on_film" in audio:
        first = audio["first_sample_on_film"]
        if time == view["start_s"]:
            return first
        if time == view["end_s"] and "sample_count" in audio:
            return first + audio["sample_count"]
    return sample_boundary(time, audio["rate_hz"])


def preceding_turns(directory, view, earliest):
    """Walk published turn directories backwards while their film clocks meet."""
    serial = directory.name.removeprefix("turn_")
    if not serial.isdigit() or not directory.name.startswith("turn_"):
        return
    start = view["start_s"]
    for index in range(int(serial) - 1, 0, -1):
        if start <= earliest:
            break
        previous = directory.parent / f"turn_{index:05}"
        manifest = previous / "view.json"
        if not manifest.is_file():
            break
        packet = json.loads(manifest.read_text())
        if packet.get("schema") != "reel.play.v1" or packet["end_s"] != start:
            break
        yield previous, packet
        start = packet["start_s"]


def preceding_audio(directory, view, earliest):
    """Yield recorded WAVs with the same channel count and sample rate, newest first."""
    audio = view["audio"]
    for previous, packet in preceding_turns(directory, view, earliest):
        track = packet.get("audio")
        if not track or (track["rate_hz"], track["channels"]) != (audio["rate_hz"], audio["channels"]):
            break
        wav = previous / track["wav"]
        if not wav.is_file():
            break
        yield wav, packet


def released_recording(directory, start, end, default_seconds):
    wav, start, end, _, _ = released_samples(directory, start, end, default_seconds)
    return wav, start, end


def released_samples(directory, start, end, default_seconds):
    """Return the excerpt, its film times and its exact half-open native sample range."""
    if __package__:
        from .common import pcm_codec, probe
    else:
        from common import pcm_codec, probe
    directory = Path(directory).resolve()
    view = json.loads((directory / "view.json").read_text())
    audio = view.get("audio")
    if view.get("schema") != "reel.play.v1" or not audio:
        raise ValueError("no recorded audio in this playback interval")
    end = view["end_s"] if end is None else end
    requested = start
    start = max(0, end - default_seconds) if start is None else start
    if not 0 <= start < end <= view["end_s"]:
        raise ValueError("listening range must fall within the delivered recording")
    parts = [(directory / audio["wav"], view), *preceding_audio(directory, view, start)]
    available = parts[-1][1]["start_s"]
    if requested is not None and start < available:
        raise ValueError(f"the contiguous recording begins at {available:g} seconds")
    start = max(start, available)
    if start >= end:
        raise ValueError("the requested recording is unavailable")
    parts = [(wav, packet) for wav, packet in reversed(parts) if packet["start_s"] < end and packet["end_s"] > start]
    rate = audio["rate_hz"]
    left = sample_at(parts[0][1], start)
    right = sample_at(parts[-1][1], end)
    if len(parts) == 1 and (start, end) == (parts[0][1]["start_s"], parts[0][1]["end_s"]):
        return parts[0][0], start, end, left, right
    first = sample_at(parts[0][1], parts[0][1]["start_s"])
    if right <= left:
        raise ValueError("the listening range contains no audio samples")
    name = f"heard_{left}-{right}_{rate}Hz.wav"
    codec = pcm_codec(probe(parts[0][0]))
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        work = Path(tmp)
        lines = []
        for i, (wav, _) in enumerate(parts):
            track = work / f"part_{i}.wav"
            track.symlink_to(wav)
            lines.append(f"file '{track.name}'")
        (work / "parts.txt").write_text("\n".join(lines) + "\n")
        filters = f"atrim=start_sample={left-first}:end_sample={right-first},asetpts=N/SR/TB"
        result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "concat", "-safe", "0", "-i", "parts.txt",
                                 "-map", "0:a:0", "-af", filters, "-map_metadata", "-1", "-c:a", codec,
                                 "-rf64", "auto", "clip.wav"], cwd=work, capture_output=True)
        if result.returncode:
            raise ValueError("could not reassemble the published recording")
        (work / "clip.wav").replace(directory / name)
    return directory / name, start, end, left, right
