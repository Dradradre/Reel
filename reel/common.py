"""Shared helpers for Reel: ffmpeg decode, fonts, colour maps, small drawing tools."""
from __future__ import annotations
import functools, hashlib, json, math, pathlib, subprocess
from contextlib import contextmanager
from fractions import Fraction
import tempfile
from typing import TYPE_CHECKING
import numpy as np

if TYPE_CHECKING:
    from PIL import ImageDraw

MONO = "/System/Library/Fonts/Menlo.ttc"
KOR = "/System/Library/Fonts/AppleSDGothicNeo.ttc"


def run(cmd):
    return subprocess.run([str(c) for c in cmd], check=True, capture_output=True)


def playback_tracks(info, tracks):
    """Select the decoders without changing the source clock or track metadata."""
    if tracks == "both":
        return info
    if tracks not in ("picture", "sound"):
        raise ValueError("tracks must be both, picture or sound")
    has_picture = tracks == "picture"
    if not info["has_video" if has_picture else "has_audio"]:
        raise ValueError(f"the source has no {tracks} track")
    return {**info, "has_video": has_picture, "has_audio": not has_picture}


def probe(path):
    # Turns, crops and OCR revisit the same local recording. Invalidate on replacement or edit.
    try:
        version = RecordedSource.version(pathlib.Path(path))
    except OSError:
        return _probe(str(path))
    info = _cached_probe(str(pathlib.Path(path).resolve()), version)
    return {**info, "path": str(path)}


@functools.lru_cache(maxsize=16)
def _cached_probe(path, version):
    return _probe(path)


def _probe(path):
    j = json.loads(run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path]).stdout)
    def default_track(tracks):
        # max keeps the first track on ties, including files without a default flag.
        return max(tracks, key=lambda s: s.get("disposition", {}).get("default", 0), default=None)

    videos = [s for s in j["streams"] if s["codec_type"] == "video"]
    audios = [s for s in j["streams"] if s["codec_type"] == "audio"]
    v = default_track(s for s in videos if not any(s.get("disposition", {}).get(flag)
                                                  for flag in ("attached_pic", "timed_thumbnails")))
    a = default_track(audios)
    info = {"path": str(path), "dur": float(j["format"]["duration"]), "has_audio": a is not None, "has_video": v is not None}
    info["container"] = j["format"].get("format_name")
    info["alternate_streams"] = len(videos) > int(v is not None) or len(audios) > 1
    origin = float(j["format"].get("start_time", 0))
    info["origin"] = origin
    for key, track in (("audio_end", a), ("video_end", v)):
        if not track:
            continue
        if track.get("duration_ts") not in (None, "N/A") and track.get("time_base"):
            tick = Fraction(track["time_base"])
            begin = (int(track["start_pts"]) * tick if track.get("start_pts") not in (None, "N/A")
                     else Fraction(str(track.get("start_time", origin))))
            exact = begin - Fraction(str(origin)) + int(track["duration_ts"]) * tick
            info[key + "_exact"] = str(exact)
            info[key] = float(exact)
        elif track.get("duration") not in (None, "N/A"):
            info[key] = float(track.get("start_time", origin)) - origin + float(track["duration"])
        elif tag := track.get("tags", {}).get("DURATION"):
            # Matroska stores the end of each track as an absolute timestamp.
            try:
                hour, minute, second = tag.split(":")
                info[key] = int(hour) * 3600 + int(minute) * 60 + float(second) - origin
            except (TypeError, ValueError):
                pass
    if a:
        info.update(audio_index=a["index"], audio_codec=a.get("codec_name", ""), audio_channels=a["channels"], audio_layout=a.get("channel_layout", ""), audio_rate=int(a["sample_rate"]),
                    audio_format=a.get("sample_fmt", ""), audio_time_base=a["time_base"])
    if v:
        # Some VFR streams have no declared average. Frame display times come from the decoder.
        rate = v.get("avg_frame_rate", "0/0")
        if rate in ("0/0", "0/1", "N/A"):
            rate = v.get("r_frame_rate", "0/0")
        info.update(fps=float(Fraction(rate)) if rate not in ("0/0", "0/1", "N/A") else 1.0,
                    video_index=v["index"], w=int(v["width"]), h=int(v["height"]),
                    color_transfer=v.get("color_transfer"), color_range=v.get("color_range"))
        aspect = info["w"] / info["h"]
        sar = v.get("sample_aspect_ratio", "").split(":")
        if len(sar) == 2 and all(x.isdigit() and int(x) > 0 for x in sar):
            aspect *= int(sar[0]) / int(sar[1])
        # FFmpeg autorotates phone footage before our scale filter runs.
        rotation = next((item["rotation"] for item in v.get("side_data_list", []) if "rotation" in item), 0)
        info["display_aspect"] = 1 / aspect if abs(rotation) % 180 == 90 else aspect
    if info["alternate_streams"]:
        ends = [info.get(kind + "_end") for kind in ("video", "audio") if info["has_" + kind]]
        if ends and all(end is not None for end in ends):
            info["dur"] = max(ends)
    return info


def hdr_picture(path):
    return probe(path).get("color_transfer") in ("smpte2084", "arib-std-b67")


@functools.lru_cache(maxsize=1)
def hdr_display_available():
    help_text = run(["ffmpeg", "-hide_banner", "-h", "filter=scale"]).stdout
    return b"out_transfer" in help_text and b"perceptual" in help_text


def display_scale(path, geometry):
    """Render tagged HDR to the sRGB display used for RGB pictures and model inputs."""
    if hdr_picture(path) and hdr_display_available():
        return f"scale={geometry}:out_primaries=bt709:out_transfer=srgb:out_range=pc:intent=perceptual"
    return f"scale={geometry}"


def video_end(path, info):
    """End of the video track on the shared clock, including a final held frame."""
    if "video_end" in info:
        return info["video_end"]
    # Unindexed containers may only declare the duration of the mix. Read packet headers.
    raw = run(["ffprobe", "-v", "error", "-select_streams", str(info["video_index"]), "-show_packets",
               "-show_entries", "packet=pts_time,duration_time", "-of", "compact=p=0:nk=0", path]).stdout
    end = 0.
    for line in raw.decode().splitlines():
        fields = dict(item.split("=", 1) for item in line.split("|") if "=" in item)
        if fields.get("pts_time") in (None, "N/A"):
            continue
        duration = fields.get("duration_time")
        span = 1 / info["fps"] if duration in (None, "N/A") else float(duration)
        end = max(end, float(fields["pts_time"]) - info["origin"] + span)
    return end


def read_frames(path, w, h, fps, ss=None, t=None):
    """Yield RGB frames resampled to a constant fps, so frame i sits at i / fps (+ ss)."""
    cmd = ["ffmpeg", "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{max(ss, 0):.6f}"]
    cmd += ["-i", str(path)]
    if t is not None:
        cmd += ["-t", f"{t:.6f}"]
    scale = display_scale(path, f"{w}:{h}:flags=area")
    cmd += ["-map", f"0:{probe(path)['video_index']}", "-vf", f"fps={fps},{scale}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    n = w * h * 3
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as p:
        try:
            while buf := p.stdout.read(n):
                if len(buf) != n:
                    raise ValueError("ffmpeg returned an incomplete picture")
                yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            if p.wait():
                raise ValueError("ffmpeg could not decode the pictures")
        finally:
            if p.poll() is None:
                p.terminate()
            p.stdout.close()


def reel_source(out, meta):
    return pathlib.Path(out) / meta["film"] if "until" in meta else pathlib.Path(meta["film"])


class RecordedSource:
    """Identify the movie and, for a saved reel, the manifest used to open it."""

    def __init__(self, source):
        self.path = pathlib.Path(source)
        self.manifest = self.path / "meta.json" if self.path.is_dir() else None
        if self.manifest:
            self.manifest_version = self.version(self.manifest)
            self.path = reel_source(self.path, json.loads(self.manifest.read_text()))
        self.movie_version = self.version(self.path)
        self.check()

    @staticmethod
    def version(path):
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    def check(self):
        if (self.version(self.path) != self.movie_version or
                self.manifest and self.version(self.manifest) != self.manifest_version):
            raise ValueError("the original recording has changed")

    @contextmanager
    def reading(self):
        self.check()
        yield self.path
        self.check()


def audio_timeline(info):
    # Resolve gaps on the sample clock, allowing for rounded packet times in containers such as Matroska.
    sample = Fraction(1, info["audio_rate"])
    tick = Fraction(info["audio_time_base"])
    tolerance = float(tick if tick > sample else sample / 2)
    return f"aresample=async=1:first_pts=0:min_comp={tolerance}:min_hard_comp={tolerance}"


def sample_boundary(t, rate):
    return math.ceil(Fraction(str(t)) * rate)


def pcm_codec(info):
    fmt = info["audio_format"].removesuffix("p")
    return {"u8": "pcm_u8", "s16": "pcm_s16le", "s32": "pcm_s32le", "flt": "pcm_f32le",
            "dbl": "pcm_f64le", "s64": "pcm_s64le"}.get(fmt, "pcm_f64le")


@contextmanager
def open_recording(wav):
    """Read a released PCM excerpt for measurement, including FFmpeg's 64-bit integer WAV."""
    import soundfile as sf
    try:
        recording = sf.SoundFile(wav)
    except sf.LibsndfileError:
        if probe(wav).get("audio_format") != "s64":
            raise
        # libsndfile has no signed 64-bit PCM subtype. Keep the original bytes for playback.
        with tempfile.TemporaryDirectory() as tmp:
            converted = pathlib.Path(tmp) / "measurement.wav"
            run(["ffmpeg", "-v", "error", "-nostdin", "-i", wav, "-map", "0:a:0",
                 "-map_metadata", "-1", "-c:a", "pcm_f64le", "-rf64", "auto", converted])
            with sf.SoundFile(converted) as recording:
                yield recording
        return
    with recording:
        yield recording


def recording_levels(recording, first, stop):
    """Measure each channel of a half-open sample interval in a released WAV."""
    if not 0 <= first <= stop <= len(recording):
        raise ValueError("sample range falls outside the released recording")
    levels = {"wav_samples": [first, stop]}
    if first == stop:
        return levels
    recording.seek(first)
    peak = np.zeros(recording.channels)
    energy = np.zeros(recording.channels)
    remaining = stop - first
    while remaining:
        block = recording.read(min(remaining, 65536), dtype="float64", always_2d=True)
        if not len(block):
            raise ValueError("the recording ended inside the requested sample range")
        peak = np.maximum(peak, np.max(np.abs(block), axis=0))
        energy += np.square(block).sum(axis=0)
        remaining -= len(block)
    levels["peak_dbfs"] = [round(float(20 * np.log10(v)), 2) if v else None for v in peak]
    levels["rms_dbfs"] = [round(float(10 * np.log10(v / (stop - first))), 2) if v else None for v in energy]
    return levels


def _audio_command(path, sr, ss, t, channels):
    info = probe(path)
    cmd = ["ffmpeg", "-v", "error"]
    filters = audio_timeline(info)
    if ss is not None or t is not None:
        # Select the native samples first. A resampling filter can otherwise bring
        # energy from the following sound into the last samples of the excerpt.
        rate = info["audio_rate"]
        start = max(0, Fraction(str(ss))) if ss is not None else Fraction(0)
        if start >= 1 and info["container"] == "wav" and info["audio_codec"].startswith("pcm_") and info["origin"] == 0:
            # Plain PCM WAVs can seek exactly to a whole second without codec preroll.
            seek = int(start)
            cmd += ["-ss", str(seek)]
            start -= seek
        trim = f"atrim=start_sample={sample_boundary(start, rate)}"
        if t is not None:
            duration = Fraction(str(t))
            if duration < 0:
                raise ValueError("audio duration must be nonnegative")
            trim += f":end_sample={sample_boundary(start + duration, rate)}"
        filters += f":osr={rate},{trim},asetpts=PTS-STARTPTS"
    cmd += ["-i", str(path), "-map", f"0:{info['audio_index']}", "-af", filters]
    if channels is None:
        channels = info["audio_channels"]
    else:
        cmd += ["-ac", str(channels)]
    cmd += ["-ar", str(sr), "-f", "f32le", "-"]
    return cmd, channels


def read_audio(path, sr=48000, ss=None, t=None, channels=2):
    cmd, channels = _audio_command(path, sr, ss, t, channels)
    raw = run(cmd).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, channels).copy()


def resample_channels(pcm, rate, target_rate):
    """Resample just these samples for an encoder, keeping the recorded channels separate."""
    from scipy.signal import resample_poly

    signal = np.asarray(pcm, dtype=np.float32)
    if rate == target_rate or not len(signal):
        return signal
    divisor = math.gcd(rate, target_rate)
    return resample_poly(signal, target_rate // divisor, rate // divisor, axis=0)


def audio_blocks(path, rate, frames):
    """Stream consecutive resampled blocks with the same channels and timing as read_audio."""
    cmd, channels = _audio_command(path, rate, None, None, None)
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as process:
        try:
            while raw := process.stdout.read(frames * channels * 4):
                yield np.frombuffer(raw, np.float32).reshape(-1, channels)
            if process.wait():
                raise ValueError("could not decode the recorded audio")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()



def save_observation_png(out, image, prefix):
    """Publish identical observations under the same relative name, including concurrent requests."""
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        path = pathlib.Path(tmp) / "page.png"
        image.save(path, format="PNG")
        name = f"{prefix}_{hashlib.sha256(path.read_bytes()).hexdigest()[:24]}.png"
        path.replace(pathlib.Path(out) / name)
    return name


@functools.lru_cache(None)
def font(size, kor=False):
    from PIL import ImageFont
    return ImageFont.truetype(KOR if kor else MONO, size)


@functools.lru_cache(None)
def lut(name="magma"):
    from matplotlib import colormaps
    return (colormaps[name](np.linspace(0, 1, 256))[:, :3] * 255).astype(np.uint8)


def tc(t):
    m = int(t // 60)
    return f"{m}:{t - 60 * m:05.2f}"


def polyline(d: ImageDraw.ImageDraw, xs, ys, fill, width=1):
    pts = [(float(x), float(y)) for x, y in zip(xs, ys) if np.isfinite(y)]
    if len(pts) >= 2:
        d.line(pts, fill=fill, width=width)


def norm_rows(a, lo, hi):
    return np.clip((a - lo) / max(hi - lo, 1e-9), 0, 1)
