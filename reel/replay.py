"""A synchronized, playable excerpt using only already-presented pictures and PCM."""
from pathlib import Path
import subprocess
import tempfile

if __package__:
    from .common import probe, sample_boundary
else:
    from common import probe, sample_boundary


def replay(out, source, start, end, picture_end, first_pts, wav=None):
    out = Path(out)
    if first_pts is None or start >= picture_end:
        return {"file": str(wav), "format": "wav"} if wav else None
    begin, stop = (sample_boundary(t, 1_000_000) for t in (start, min(end, picture_end)))
    length = stop - begin
    if length <= 0:
        return {"file": str(wav), "format": "wav"} if wav else None
    # One frame can be held across start. Move it to zero, retain the original offsets of all later pictures.
    filters = (f"settb=AVTB,trim=start_pts={first_pts}:end_pts={stop},setpts='max(0,PTS-{begin})',"
               "pad=ceil(iw/2)*2:ceil(ih/2)*2")
    duration = f"setts=duration='if(eq(NEXT_PTS,NOPTS),max(1,round({length}/1000000/TB)-PTS),NEXT_PTS-PTS)'"
    command = ["ffmpeg", "-v", "error", "-nostdin", "-filter_threads", "2", "-threads:v", "2",
               "-copyts", "-start_at_zero", "-noaccurate_seek",
               "-ss", f"{max(0, first_pts)}us", "-i", str(source)]
    if wav:
        command.extend(["-i", str(wav)])
    command.extend(["-map", f"0:{probe(source)['video_index']}", "-vf", filters, "-fps_mode", "passthrough", "-enc_time_base:v", "1:1000000",
                    "-c:v", "libx264", "-threads:v", "2", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-bf", "0",
                    "-bsf:v", duration, "-video_track_timescale", "1000000", "-movie_timescale", "1000000"])
    if wav:
        command.extend(["-map", "1:a:0", "-c:a", "copy"])
    command.extend(["-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart"])
    name = f"replay_{begin}-{sample_boundary(end, 1_000_000)}.mov"
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        movie = Path(tmp) / "interval.mov"
        subprocess.run([*command, str(movie)], check=True, capture_output=True)
        movie.replace(out / name)
    return {"file": str((out / name).resolve()), "format": "mov", "video_codec": "h264",
            "audio_codec": "pcm" if wav else None, "start_s": float(start), "end_s": float(end),
            "picture_end_s": float(min(end, picture_end))}
