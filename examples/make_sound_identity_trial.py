"""Rebuild the three soundtracks for one published sound-identity trial.

python examples/make_sound_identity_trial.py video_10368.mp4 OUT --id video_10368
Give each reader a single movie under a neutral name and stop at the printed
cutoff. All three use the same moving pictures, native channel order and length.
The benchmark records the two human event labels and which answer each implies.
"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from reel.common import audio_timeline, probe


PANEL = Path(__file__).resolve().parents[1] / "docs/benchmarks/sound-identity-switch.json"


def ffmpeg(*args):
    return subprocess.check_output(["ffmpeg", "-nostdin", "-v", "error", *map(str, args)])


def build(source, out, film):
    with source.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != film["original_sha256"]:
            raise ValueError("the source differs from the published film")
    info = probe(source)
    rate, count = film["rate"], film["samples"]
    if rate != info["audio_rate"] or film["channels"] != info["audio_channels"]:
        raise ValueError("the selected audio format differs from the original")
    start = Decimal(film["parts"][0]["first_sample"]) / rate
    end = start + Decimal(count) / rate
    # Publish only after all three decoded tracks match the reference digests.
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise ValueError("choose a new output directory")
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        tmp = Path(tmp)
        files = tmp / "movies"
        files.mkdir()
        picture = tmp / "picture.mkv"
        ffmpeg("-i", source, "-map", f"0:{info['video_index']}", "-an", "-vf",
               f"trim=start={start:.9f}:end={end:.9f},setpts=PTS-STARTPTS,tpad=stop_duration=0.30:stop_mode=clone",
               "-fps_mode", "vfr", "-c:v", "ffv1", "-map_metadata", "-1", picture)
        for index, name in enumerate(("original", "switched", "silent")):
            part = film["parts"][index % 2]
            first = part["first_sample"]
            filters = f"{audio_timeline(info)},atrim=start_sample={first}:end_sample={first + count},asetpts=N/SR/TB"
            if name == "silent":
                filters += ",aeval=0:c=same"
            destination = files / f"{name}.mkv"
            ffmpeg("-i", picture, "-i", source, "-map", "0:v:0", "-map", f"1:{info['audio_index']}",
                   "-af", filters, "-c:v", "copy", "-c:a", "pcm_f32le", "-map_metadata", "-1", destination)
            pcm = ffmpeg("-i", destination, "-map", "0:a:0", "-f", "f32le", "-")
            if len(pcm) != 4 * count * film["channels"]:
                raise ValueError("the output has a different sample count")
            if name == "silent":
                if any(pcm):
                    raise ValueError("the silence control contains nonzero samples")
            elif hashlib.sha256(pcm).hexdigest() != part["native_pcm_sha256"]:
                raise ValueError("the output samples differ from the published original")
            frames = ffmpeg("-i", destination, "-map", "0:v", "-f", "framemd5", "-")
            timed_frames = b"\n".join(line for line in frames.splitlines() if not line.startswith(b"#software:"))
            if hashlib.sha256(timed_frames).hexdigest() != film["video_framemd5_sha256"]:
                raise ValueError("the decoded picture or its timing differs from the benchmark")
        files.rename(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--id", required=True, help="video_id in the benchmark")
    parser.add_argument("--panel", type=Path, default=PANEL)
    args = parser.parse_args()
    films = json.loads(args.panel.read_text())["pairs"]
    film = next((r for r in films if r["video_id"] == args.id), None)
    if film is None:
        parser.error("unknown video ID")
    try:
        build(args.source, args.out, film)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(f"Use --until {film['until_s']} for each movie.")
