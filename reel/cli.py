#!/usr/bin/env python3
"""reel - turn a film into a reel a reader can watch.

  reel watch FILM --out DIR [--subject NAME] [--genre lol] [--lang ko] [--page 30] [--title T] [--until SECONDS]
  reel zoom DIR --t0 S --t1 S
  reel frame DIR --at S     save the full frame on display at S
  reel frame DIR --t0 S --t1 S     read every frame displayed in the interval
  reel listen DIR --t0 S --t1 S [--verify-sounds] [--gain-db DB] [--compare-sounds] [--describe-sounds] [--question TEXT]    inspect the recorded sound locally
  reel layer DIR --t0 START --t1 END (--layer RANK | --focus TEXT) [--channel N] [--describe-sounds]
  reel compare-heard DIR --reference START END --target START END [--similarity] [--alternatives DESCRIPTION ...] [--question TEXT]
  reel match DIR --t0 S --t1 S [--per-second | --locate | --identify] ["sound description" ...]    compare descriptions with the recorded channels
  reel observe DIR --t0 S --t1 S [--joint] [--motion-point X Y] [--focus LEFT TOP RIGHT BOTTOM] [--question TEXT] [--media] [--sync-region LEFT TOP RIGHT BOTTOM]     read each displayed picture with its matching sound
  reel speech TURN [--lang CODE] [--verify]               read words from a delivered audio interval
  reel follow TURN --from S --point X Y [--out NEW_DIR]    follow a pictured point through published frames
  reel look TURN --box LEFT TOP RIGHT BOTTOM     inspect a region of the published full-resolution picture
  reel play FILM --out NEW_DIR [--until S] [--verify-sounds] [--identify-sounds] [--tracks both|picture|sound] [--step S] [--continuous] [--max-frames N] [--pause-on-sound-change] [--speech] [--lang CODE] [--gaze] [--media] [--joint] [--describe-sounds]     enter next to advance one interval
  reel mcp FILM [--until S] [--verify-sounds] [--identify-sounds] [--tracks both|picture|sound] [--image-client] [--pause-on-sound-change] [--speech] [--lang CODE] [--gaze] [--describe-sounds] [--joint]    serve pictures, sound and text over MCP stdio
  reel doctor            which senses are available on this machine
  reel setup [--identify-sounds] [--follow] [--sync] [--layers] [--speech] [--captions] [--sound-labels] [--sound-events] [--compare-sounds] [--describe-sounds] [--locate-sounds]    fetch models for the selected senses
"""
import shlex
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import argparse, importlib, json, pathlib, re, shutil, subprocess, sys
from config import HOME, UNISAL, MODELS, GENRES, SEP_PY, CROWD_PY, ASR_PY, HEAR_PY, COMPARE_PY, GAZE_PY, SYNC_PY, SYNCHFORMER, SYNC_CKPT, SYNC_URL

HERE = pathlib.Path(__file__).resolve().parent


def observation_options(parser):
    parser.add_argument("--motion-point", type=float, nargs=2, metavar=("X", "Y"),
                        help="show the pixels on two fixed lines through this point (0 to 1) over time")
    parser.add_argument("--joint", action="store_true", help="interpret the matching waveform and pictures together")
    parser.add_argument("--focus", type=float, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                        help="follow this region's motion alongside the recorded sound")


def has(py, mod):
    return subprocess.run([py, "-c", f"import {mod}"], capture_output=True).returncode == 0


def doctor():
    from common import hdr_display_available
    try:
        hearing = subprocess.run([HEAR_PY, str(HERE / "hearing.py"), "--check"], capture_output=True).returncode == 0
    except OSError:
        hearing = False
    try:
        compare = subprocess.run([COMPARE_PY, str(HERE / "compare_sounds.py"), "--check"], capture_output=True).returncode == 0
    except OSError:
        compare = False
    from unmix import PYTHON as unmix_py, ready as layers_ready
    from point_reader import PYTHON as follow_py, ready as follow_ready
    import identify_sound
    from scene_audio import READY as captions_ready
    from localize_sound import ready as location_ready
    from sound_events import ready as events_ready
    from config import CAPTION_PY
    captions = (captions_ready.is_file() and pathlib.Path(CAPTION_PY).is_file() and
                subprocess.run([CAPTION_PY, str(HERE / "scene_audio.py"), "--check"], capture_output=True).returncode == 0)
    rows = [("ffmpeg (decode)", shutil.which("ffmpeg") is not None, "brew install ffmpeg"),
            ("HDR pictures on an sRGB display", bool(shutil.which("ffmpeg")) and hdr_display_available(), "brew upgrade ffmpeg"),
            ("MCP picture and sound transport", has(sys.executable, "mcp.server.fastmcp"), "pip install -e '.[mcp]'"),
            ("picture, sound, memory", has(sys.executable, "cv2") and has(sys.executable, "librosa"), "pip install -e ."),
            ("recorded channel levels and timing", has(sys.executable, "soundfile") and has(sys.executable, "numpy"), "pip install -e ."),
            ("voices: Demucs", has(SEP_PY, "demucs"), "pip install -e '.[separate]'"),
            ("crowd: Mel-Band Roformer", has(CROWD_PY, "audio_separator"), "pip install -e '.[crowd]'"),
            ("speech: mlx-whisper (Apple GPU)", has(ASR_PY, "mlx_whisper"), "pip install -e '.[mlx]'"),
            ("speech activity (Silero) and CPU transcription", has(ASR_PY, "faster_whisper"), "pip install -e '.[speech]'"),
            ("on-screen text: Apple Vision (ocrmac)", has(sys.executable, "ocrmac"), "pip install -e '.[ocr]'"),
            ("sounds like: AudioSet tagger (AST)", subprocess.run([sys.executable, str(HERE / "sounds.py"), "--check"], capture_output=True).returncode == 0, "pip install -e '.[tags]' and reel setup --sound-labels"),
            ("short sound events: YAMNet", events_ready() and subprocess.run(
                [sys.executable, str(HERE / "sound_events.py"), "--check"], capture_output=True).returncode == 0,
             "pip install -e '.[events]' and reel setup --sound-events"),
            ("sound identity: PE-AV and CLAP", identify_sound.READY.is_file() and pathlib.Path(identify_sound.PYTHON).is_file(), "reel setup --identify-sounds"),
            ("first-listen descriptions: CoNeTTE", captions, "reel setup --captions"),
            ("waveform and optional joint picture/sound readings: Qwen3-Omni", hearing, "reel setup --describe-sounds"),
            ("overlapping sounds: MixIT separator", layers_ready() and shutil.which(unmix_py) is not None
             and has(unmix_py, "tensorflow"), "reel setup --layers"),
            ("locate a described sound: PE-A-Frame", location_ready(), "reel setup --locate-sounds"),
            ("optional sound comparison: local CLAP", compare, "pip install -e '.[tags]' and reel setup --compare-sounds"),
            ("moving point: online BootsTAPIR", follow_ready() and subprocess.run(
                [follow_py, str(HERE / "point_reader.py"), "--check"], capture_output=True).returncode == 0, "reel setup --follow"),
            ("gaze: PyTorch", has(GAZE_PY, "torch"), "pip install -e '.[gaze]'"),
            ("sync: Synchformer libraries", has(SYNC_PY, "omegaconf") and has(SYNC_PY, "timm") and has(SYNC_PY, "einops"), "pip install -e '.[sync]'"),
            ("sync: Synchformer weights", (SYNCHFORMER / "logs/sync_models" / SYNC_CKPT / f"{SYNC_CKPT}.pt").exists(), "reel setup --sync"),
            ("gaze: UNISAL weights", (UNISAL / "training_runs/pretrained_unisal/weights_best.pth").exists(), "reel setup"),
            ("genre page: LoL reference frame", (GENRES / "lol.png").exists(), f"put a full broadcast frame at {GENRES / 'lol.png'}")]
    w = max(len(r[0]) for r in rows)
    for name, ok, fix in rows:
        print(f"  {'ok ' if ok else '-- '} {name.ljust(w)}  {'' if ok else fix}")
    print(f"\n  models and caches: {HOME}")


def setup():
    HOME.mkdir(parents=True, exist_ok=True)
    if "--follow" in sys.argv:
        from point_reader import prepare_environment
        prepare_environment()
        print("online picture point tracking ready")
    if "--layers" in sys.argv:
        from unmix import prepare_environment
        prepare_environment()
        print("overlapping sound layers ready")
    if "--speech" in sys.argv:
        subprocess.run([ASR_PY, str(HERE / "live_speech.py"), "--setup"], check=True)
        print("local speech activity and transcription ready")
    if "--identify-sounds" in sys.argv:
        from identify_sound import prepare_environment
        prepare_environment()
        print("sound identity comparison ready")
    if "--captions" in sys.argv:
        from scene_audio import prepare_environment
        try:
            prepare_environment()
        except (ValueError, OSError) as error:
            raise SystemExit(str(error))
        print("first-listen descriptions ready")
    if "--sound-events" in sys.argv:
        from sound_events import setup as setup_events
        try:
            setup_events()
        except (ValueError, OSError) as error:
            raise SystemExit(str(error))
        print("short sound events ready")
    if "--sound-labels" in sys.argv:
        subprocess.run([sys.executable, str(HERE / "sounds.py"), "--setup"], check=True)
        print("playback sound labels ready")
    if "--describe-sounds" in sys.argv:
        from hearing import prepare_environment
        prepare_environment()
        print("local waveform and joint picture/sound readings ready")
    if "--compare-sounds" in sys.argv:
        subprocess.run([COMPARE_PY, str(HERE / "compare_sounds.py"), "--setup"], check=True)
        print("sound comparison ready")
    if "--locate-sounds" in sys.argv:
        from localize_sound import prepare_environment
        prepare_environment()
        print("sound location ready")
    optional_models = {"--identify-sounds", "--locate-sounds", "--follow", "--layers", "--speech", "--captions", "--sound-events", "--sound-labels", "--describe-sounds", "--compare-sounds"}
    if "--sync" not in sys.argv and optional_models.intersection(sys.argv):
        return
    if not UNISAL.exists():
        subprocess.run(["git", "clone", "--depth", "1", "https://github.com/rdroste/unisal.git", str(UNISAL)], check=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    GENRES.mkdir(parents=True, exist_ok=True)
    if "--sync" in sys.argv:
        if not SYNCHFORMER.exists():
            subprocess.run(["git", "clone", "--depth", "1", "https://github.com/v-iashin/Synchformer.git", str(SYNCHFORMER)], check=True)
        d = SYNCHFORMER / "logs" / "sync_models" / SYNC_CKPT
        d.mkdir(parents=True, exist_ok=True)
        for name in (f"cfg-{SYNC_CKPT}.yaml", f"{SYNC_CKPT}.pt"):
            if not (d / name).exists():
                print("downloading", name)
                subprocess.run(["curl", "-L", "-o", str(d / name), f"{SYNC_URL}/{SYNC_CKPT}/{name}"], check=True)
        print("sync model ready")
    print("gaze model ready; the crowd model downloads itself on first use")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "doctor":
        doctor(); return
    if cmd == "setup":
        setup(); return
    if cmd == "mcp":
        ap = argparse.ArgumentParser(prog="reel mcp", description="Serve one film to a local MCP client over stdio.")
        ap.add_argument("source", type=pathlib.Path)
        ap.add_argument("--tracks", choices=("both", "picture", "sound"), default="both",
                        help="present the selected tracks on the original film clock")
        ap.add_argument("--until", type=float)
        ap.add_argument("--image-client", action="store_true",
                        help="deliver local waveform descriptions and supported words with images; attach WAV on request")
        ap.add_argument("--pause-on-sound-change", action="store_true", help="also pause when recorded frequency bands change")
        ap.add_argument("--speech", action="store_true", help="include the local speech evidence at every pause")
        ap.add_argument("--lang", help="language hint for the speech reader")
        ap.add_argument("--joint", action="store_true", help="read synchronized pictures and sound at every pause")
        ap.add_argument("--gaze", action="store_true", help="include timed details selected by the visual gaze model")
        ap.add_argument("--describe-sounds", action="store_true", help="include the local waveform reader's first impression at every pause")
        ap.add_argument("--identify-sounds", action="store_true", help="compare possible sound sources with two waveform encoders")
        ap.add_argument("--verify-sounds", action="store_true", help="compare generated sound descriptions with the recorded waveform using CLAP")
        args = ap.parse_args(sys.argv[2:])
        if args.until is not None and not 0 < args.until < float("inf"):
            ap.error("--until must be a finite positive number of seconds")
        if not args.source.is_file() and not (args.source / "meta.json").is_file():
            ap.error("supply an existing film or saved reel directory")
        if importlib.util.find_spec("mcp") is None:
            ap.error("install the MCP transport with pip install -e '.[mcp]'")
        from mcp_server import serve
        serve(args.source, until=args.until, tracks=args.tracks, speech=args.speech, lang=args.lang, joint=args.joint, gaze=args.gaze,
              describe_sounds=args.describe_sounds, pause_on_sound_change=args.pause_on_sound_change,
              image_client=args.image_client, verify_sounds=args.verify_sounds, identify_sounds=args.identify_sounds)
        return
    if cmd == "layer":
        from hearing import SoundReader
        from unmix import LayerReader, describe_layer
        ap = argparse.ArgumentParser(prog="reel layer")
        ap.add_argument("out", help="a published turn or saved reel")
        ap.add_argument("--t0", type=float, required=True)
        ap.add_argument("--t1", type=float, required=True)
        selection = ap.add_mutually_exclusive_group(required=True)
        selection.add_argument("--layer", type=int, choices=range(1, 9))
        selection.add_argument("--focus", help="listen for a sound described in your own words using local CLAP")
        ap.add_argument("--channel", type=int, default=1)
        ap.add_argument("--describe-sounds", action="store_true")
        x = ap.parse_args(sys.argv[2:])
        try:
            with LayerReader() as layers, SoundReader() as sound:
                result = layers.inspect(x.out, x.t0, x.t1, x.channel, x.layer, x.focus)
                if x.describe_sounds:
                    describe_layer(sound, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            ap.error(str(error))
        return
    if cmd == "compare-heard":
        from compare_heard import describe_recordings, recordings, sound_similarity
        from hearing import SoundReader
        ap = argparse.ArgumentParser(prog="reel compare-heard")
        ap.add_argument("out", help="a published turn or a saved reel")
        for flag in ("--reference", "--target"):
            ap.add_argument(flag, nargs=2, type=float, metavar=("START", "END"), required=True)
        ap.add_argument("--question", help="ask about an audible difference or similarity")
        ap.add_argument("--similarity", action="store_true", help="compare the two original recordings directly with CLAP")
        ap.add_argument("--alternatives", nargs="+", metavar="DESCRIPTION", help="compare each description across both recordings")
        ap.add_argument("--recordings-only", action="store_true", help="return both original WAVs without worded descriptions")
        x = ap.parse_args(sys.argv[2:])
        if x.recordings_only and x.question is not None:
            ap.error("omit --recordings-only to ask a question")
        try:
            clips = recordings(x.out, x.reference, x.target)
            report = dict(clips)
            if x.similarity or x.alternatives is not None:
                report["sound_similarity"] = sound_similarity(clips, alternatives=x.alternatives)
            if not x.recordings_only:
                with SoundReader() as sound:
                    report["independent_readings"] = describe_recordings(sound, clips, x.question)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            ap.error(str(error))
        return
    if cmd == "match":
        from compare_sounds import SoundMatcher, match_excerpt
        from localize_sound import locate
        ap = argparse.ArgumentParser(prog="reel match")
        ap.add_argument("out")
        ap.add_argument("--t0", type=float, required=True)
        ap.add_argument("--t1", type=float, required=True)
        mode = ap.add_mutually_exclusive_group()
        mode.add_argument("--identify", action="store_true", help="rank source descriptions with PE-AV and CLAP")
        mode.add_argument("--locate", action="store_true", help="find the second most similar to each supplied description")
        mode.add_argument("--per-second", action="store_true", help="compare consecutive seconds on the film clock")
        ap.add_argument("descriptions", nargs="*")
        x = ap.parse_intermixed_args(sys.argv[2:])
        try:
            if x.locate or x.identify:
                wav, start, end, first = match_excerpt(x.out, x.t0, x.t1)
                if x.identify:
                    from identify_sound import IdentityReader
                    with IdentityReader() as identity:
                        report = identity.read(wav, start, end, first, x.descriptions)
                else:
                    report = locate(wav, start, end, x.descriptions, first)
            else:
                with SoundMatcher() as matcher:
                    report = matcher.inspect(x.out, x.t0, x.t1, x.descriptions, per_second=x.per_second)
            print(json.dumps(report, ensure_ascii=False))
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            ap.error(str(error))
        return
    if cmd == "speech":
        from contextlib import nullcontext
        from hearing import SoundReader
        from live_speech import SpeechReader
        ap = argparse.ArgumentParser(prog="reel speech")
        ap.add_argument("turn")
        ap.add_argument("--lang", help="spoken language code (default: detect)")
        ap.add_argument("--t0", type=float, help="start of a previously heard speech interval")
        ap.add_argument("--t1", type=float, help="end of that interval, up to eight seconds later")
        ap.add_argument("--verify", action="store_true", help="check gaps with the local waveform reader")
        x = ap.parse_args(sys.argv[2:])
        try:
            with (SoundReader() if x.verify else nullcontext()) as sound, SpeechReader(sound) as reader:
                print(json.dumps(reader.inspect(x.turn, x.lang, start_s=x.t0, end_s=x.t1), ensure_ascii=False))
        except (ValueError, OSError) as error:
            ap.error(str(error))
        return
    if cmd == "follow":
        from follow import draw_follow, follow_point
        from point_reader import PointReader
        ap = argparse.ArgumentParser(prog="reel follow")
        ap.add_argument("turn", type=pathlib.Path, help="a published turn directory")
        ap.add_argument("--from", dest="anchor", type=float, required=True)
        ap.add_argument("--point", nargs=2, type=float, required=True, metavar=("X", "Y"))
        ap.add_argument("--out", type=pathlib.Path, help="create a new directory for the illustrated track")
        x = ap.parse_args(sys.argv[2:])
        try:
            packet = json.loads((x.turn / "view.json").read_text())
            with PointReader() as tracker:
                report, frames = follow_point(x.turn, packet, x.anchor, x.point, tracker)
            if x.out and frames:
                x.out.mkdir(parents=True, exist_ok=False)
                report["images"] = [str(p.resolve()) for p, _ in draw_follow(x.out, x.turn, packet, report, frames)]
        except (OSError, ValueError) as exc:
            ap.error(str(exc))
        print(json.dumps(report, ensure_ascii=False))
        return
    if cmd == "look":
        from attention import inspect_turn
        ap = argparse.ArgumentParser(prog="reel look")
        ap.add_argument("turn")
        ap.add_argument("--box", type=float, nargs=4, required=True, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
        x = ap.parse_args(sys.argv[2:])
        try:
            print(json.dumps(inspect_turn(x.turn, x.box)))
        except (ValueError, OSError) as error:
            ap.error(str(error))
        return
    if cmd == "play":
        from contextlib import closing
        from common import RecordedSource
        from attention import inspect_past, inspect_turn
        from unmix import LayerReader, describe_layer
        from play import play
        from joint import recent_joint
        import shlex
        from live_speech import SpeechReader
        from hearing import SoundReader
        ap = argparse.ArgumentParser(prog="reel play")
        ap.add_argument("source", help="film or existing reel")
        ap.add_argument("--out", required=True, help="new directory for this viewing")
        ap.add_argument("--tracks", choices=("both", "picture", "sound"), default="both",
                        help="present the selected tracks on the original film clock")
        ap.add_argument("--until", type=float, help="stop before this time on the source clock")
        ap.add_argument("--pause-on-sound-change", action="store_true", help="also pause when recorded frequency bands change")
        ap.add_argument("--speech", action="store_true", help="include recognized words automatically at each pause")
        ap.add_argument("--lang", help="spoken language code (default: detect from the recording)")
        ap.add_argument("--joint", action="store_true", help="also read the pictures and their soundtrack in one local model")
        ap.add_argument("--describe-sounds", action="store_true", help="include local audio-only impressions")
        ap.add_argument("--identify-sounds", action="store_true", help="compare possible sound sources with two waveform encoders")
        ap.add_argument("--verify-sounds", action="store_true", help="compare generated sound descriptions with the recorded waveform using CLAP")
        ap.add_argument("--media", action="store_true", help="also deliver a synchronized playable excerpt")
        ap.add_argument("--gaze", action="store_true", help="show recent fixation crops and their times alongside the full picture")
        ap.add_argument("--step", type=float, default=2., help="maximum seconds before waiting (default 2)")
        ap.add_argument("--max-frames", type=int, help="pause after at most this many displayed pictures (1 to 12)")
        ap.add_argument("--continuous", action="store_true", help="present the full step through changes in the picture and sound")
        x = ap.parse_args(sys.argv[2:])
        replay_options = argparse.ArgumentParser(add_help=False, exit_on_error=False, allow_abbrev=False)
        observation_options(replay_options)
        from identify_sound import IdentityReader
        identity_reader = IdentityReader()
        sound_reader = SoundReader()
        waveform = sound_reader if x.describe_sounds or x.joint else None
        playback = play(x.source, x.out, until=x.until, tracks=x.tracks, step=x.step, describe_sounds=x.describe_sounds,
                        verify_sounds=x.verify_sounds, identify_sounds=x.identify_sounds, identity_reader=identity_reader,
                        media=x.media, gaze=x.gaze, speech=x.speech, lang=x.lang, sound_reader=waveform, joint=x.joint,
                        pause_on_sound_change=x.pause_on_sound_change, continuous=x.continuous, max_frames=x.max_frames)
        try:
            original = RecordedSource(x.source)
            with (sound_reader, identity_reader, SpeechReader(waveform) as speech_reader, LayerReader() as layers,
                  closing(playback) as packets):
                with original.reading():
                    path = next(packets, None)
                if path is not None:
                    print(path.resolve(), flush=True)
                while path is not None:
                    line = sys.stdin.readline()
                    command = line.lower().split()
                    if not line or command == ["quit"]:
                        break
                    if command[:1] == ["layer"]:
                        try:
                            fields = shlex.split(line)
                            if len(fields) not in (4, 5):
                                raise ValueError('use layer START END RANK|"SOUND DESCRIPTION" [CHANNEL]')
                            channel = int(fields[4]) if len(fields) == 5 else 1
                            try:
                                rank = int(fields[3])
                            except ValueError:
                                rank = None
                            result = layers.inspect(path.parent, float(fields[1]), float(fields[2]), channel,
                                                    rank, fields[3] if rank is None else None)
                            with sound_reader.for_replay():
                                describe_layer(sound_reader, result)
                            print(json.dumps(result, ensure_ascii=False), flush=True)
                        except (ValueError, OSError, subprocess.CalledProcessError) as error:
                            print(json.dumps({"error": str(error)}), flush=True)
                        continue
                    if command[:1] == ["compare-heard"]:
                        from compare_heard import describe_recordings, recordings, sound_similarity
                        try:
                            compare_terms = command[1:2] == ["--alternatives"]
                            similarity = compare_terms or command[1:2] == ["--similarity"]
                            # Split only numeric positions; preserve the reader's question verbatim.
                            fields = line.strip().split(maxsplit=6 if similarity else 5)[2 if similarity else 1:]
                            if len(fields) < 4:
                                raise ValueError("give reference START END and target START END after compare-heard")
                            times = [float(t) for t in fields[:4]]
                            clips = recordings(path.parent, times[:2], times[2:])
                            report = dict(clips)
                            if compare_terms:
                                if len(fields) != 5:
                                    raise ValueError("supply sound descriptions after the four times, quoting each phrase")
                                report["sound_similarity"] = sound_similarity(clips, alternatives=shlex.split(fields[4]))
                            else:
                                with sound_reader.for_replay():
                                    report["independent_readings"] = describe_recordings(
                                        sound_reader, clips, fields[4] if len(fields) == 5 else None)
                                if similarity:
                                    report["sound_similarity"] = sound_similarity(clips)
                            print(json.dumps(report, ensure_ascii=False), flush=True)
                        except (ValueError, OSError, subprocess.CalledProcessError) as error:
                            print(json.dumps({"error": str(error)}), flush=True)
                        continue
                    if command[:1] == ["match"]:
                        try:
                            fields = shlex.split(line)
                            if len(fields) < 3:
                                raise ValueError('use match START END [--per-second] ["sound description" ...]')
                            per_second = fields[3:4] == ["--per-second"]
                            descriptions = fields[4:] if per_second else fields[3:]
                            if fields[3:4] == ["--identify"]:
                                from compare_sounds import match_excerpt
                                wav, start, end, first = match_excerpt(path.parent, float(fields[1]), float(fields[2]))
                                with identity_reader.for_replay() as identity:
                                    result = identity.read(wav, start, end, first, fields[4:])
                            elif fields[3:4] == ["--locate"]:
                                from compare_sounds import match_excerpt
                                from localize_sound import locate
                                wav, start, end, first = match_excerpt(path.parent, float(fields[1]), float(fields[2]))
                                result = locate(wav, start, end, fields[4:], first)
                            else:
                                with layers.matcher.for_replay():
                                    result = layers.matcher.inspect(path.parent, float(fields[1]), float(fields[2]),
                                                                    descriptions, per_second=per_second, identity_reader=identity_reader)
                            print(json.dumps(result, ensure_ascii=False), flush=True)
                        except (ValueError, OSError, subprocess.CalledProcessError) as error:
                            print(json.dumps({"error": str(error)}), flush=True)
                        continue
                    if command[:1] == ["joint"]:
                        try:
                            fields = line.split(maxsplit=2)
                            if len(fields) < 2:
                                raise ValueError("use joint SECONDS [QUESTION]")
                            question = fields[2] if len(fields) == 3 else None
                            with sound_reader.for_replay():
                                result = recent_joint(sound_reader, path.parent, float(fields[1]), x.source,
                                                      question=question)
                            print(json.dumps(result, ensure_ascii=False), flush=True)
                        except (ValueError, OSError, subprocess.CalledProcessError) as error:
                            print(json.dumps({"error": str(error)}), flush=True)
                        continue
                    if command[:1] == ["observe"]:
                        from observe import observe
                        try:
                            # Everything after --question belongs to the listener, preserving case and punctuation.
                            parts = re.split(r"\s+--question(?:\s+|$)", line.strip(), maxsplit=1)
                            fields = parts[0].split()
                            options, extra = replay_options.parse_known_args(fields[3:])
                            if len(fields) < 3 or extra:
                                raise ValueError("use observe START END [--joint] [--focus LEFT TOP RIGHT BOTTOM] "
                                                 "[--motion-point X Y] [--question TEXT]")
                            question = parts[1] if len(parts) == 2 else None
                            with original.reading(), sound_reader.for_replay():
                                result = observe(path.parent, float(fields[1]), float(fields[2]), focus=options.focus,
                                                 playback_source=x.source, question=question, sound_reader=sound_reader,
                                                 joint=options.joint, motion_point=options.motion_point)
                            print(json.dumps(result, ensure_ascii=False), flush=True)
                        except (argparse.ArgumentError, ValueError, OSError, subprocess.CalledProcessError) as error:
                            print(json.dumps({"error": str(error)}), flush=True)
                        continue
                    if command[:1] == ["listen"]:
                        question = line.strip().split(maxsplit=1)[1] if len(command) > 1 else None
                        try:
                            component, gain_db = "mix", 0.
                            while question and question.split(maxsplit=1)[0].lower() in ("--component", "--gain-db"):
                                fields = question.split(maxsplit=2)
                                if len(fields) < 2:
                                    raise ValueError(f"give a value after {fields[0]}")
                                if fields[0].lower() == "--component":
                                    component = fields[1].lower()
                                else:
                                    gain_db = float(fields[1])
                                question = fields[2] if len(fields) > 2 else None
                            with sound_reader.for_replay():
                                result = sound_reader.inspect(path.parent, question, component=component, gain_db=gain_db)
                            print(json.dumps(result, ensure_ascii=False), flush=True)
                        except (ValueError, OSError, subprocess.CalledProcessError) as error:
                            print(json.dumps({"error": str(error)}), flush=True)
                        continue
                    if command[:1] == ["speech"]:
                        try:
                            if len(command) > 4:
                                raise ValueError("use speech [LANG] or speech START END [LANG]")
                            interval = {}
                            lang = x.lang
                            if len(command) >= 3:
                                interval = {"start_s": float(command[1]), "end_s": float(command[2])}
                            if len(command) in (2, 4):
                                lang = command[-1]
                            with sound_reader.for_replay():
                                result = speech_reader.inspect(path.parent, lang, **interval)
                            print(json.dumps(result, ensure_ascii=False), flush=True)
                        except (ValueError, OSError) as error:
                            print(json.dumps({"error": str(error)}), flush=True)
                        continue
                    if command[:1] == ["look"]:
                        try:
                            if command[1:2] == ["--at"]:
                                if len(command) != 7:
                                    raise ValueError("use look --at SECONDS LEFT TOP RIGHT BOTTOM")
                                detail = inspect_past(path.parent, original, float(command[2]), [float(v) for v in command[3:]])
                            else:
                                detail = inspect_turn(path.parent, [float(v) for v in command[1:]])
                            print(detail["file"], flush=True)
                        except (ValueError, OSError, subprocess.CalledProcessError) as error:
                            print(json.dumps({"error": str(error)}), flush=True)
                        continue
                    if not command or command[0] != "next" or len(command) > 2:
                        ap.error("enter next, next SECONDS, joint SECONDS, observe START END, look [--at SECONDS] LEFT TOP RIGHT BOTTOM, "
                                 "listen [--component mix|harmonic|percussive|mid|side] [--gain-db DB] [QUESTION], match START END DESCRIPTION, compare-heard [--similarity] START END START END, layer START END RANK [CHANNEL], speech [LANG], or quit")
                    duration = float(command[1]) if len(command) == 2 else None
                    try:
                        with original.reading():
                            path = packets.send(duration)
                        print(path.resolve(), flush=True)
                    except StopIteration:
                        break
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            ap.error(str(error))
        return
    if cmd in ("listen", "observe"):
        ap = argparse.ArgumentParser(prog=f"reel {cmd}")
        ap.add_argument("out")
        ap.add_argument("--t0", type=float, required=cmd == "observe")
        ap.add_argument("--t1", type=float, required=cmd == "observe")
        if cmd == "listen":
            from audio_focus import COMPONENTS
            ap.add_argument("--mark-sound", type=float, nargs=2, metavar=("START", "END"),
                            help="mark these film seconds on the original recording inside --t0 and --t1")
            ap.add_argument("--gain-db", type=float, default=0., help="adjust replay volume equally across channels")
            ap.add_argument("--component", choices=COMPONENTS, default="mix", help="listen to the mix or a filtered component")
            ap.add_argument("--describe-sounds", action="store_true", help="describe the bounded waveform locally")
            ap.add_argument("--verify-sounds", action="store_true", help="compare generated sound descriptions with the recorded waveform using CLAP")
            ap.add_argument("--question", help="ask the local waveform model about the recorded sound")
            ap.add_argument("--compare-sounds", action="store_true", help="also rank sound descriptions with local CLAP")
        else:
            observation_options(ap)
            ap.add_argument("--question", help="ask the local audio reader about exactly this interval")
            ap.add_argument("--media", action="store_true", help="also deliver a synchronized playable excerpt")
            ap.add_argument("--sync-region", action="append", type=float, nargs=4, default=[],
                            metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                            help="compare sound timing with this screen region (0 to 1, repeatable)")
        x = ap.parse_args(sys.argv[2:])
        try:
            if cmd == "observe":
                from observe import observe
                data = observe(x.out, x.t0, x.t1, sync_regions=x.sync_region, media=x.media,
                               focus=x.focus, question=x.question, joint=x.joint, motion_point=x.motion_point)
            else:
                from listen import inspect_sound
                data = inspect_sound(x.out, x.t0, x.t1, compare_sounds=x.compare_sounds, describe_sounds=x.describe_sounds,
                                     question=x.question, component=x.component, mark_sound=x.mark_sound, gain_db=x.gain_db,
                                     verify_sounds=x.verify_sounds)
            print(json.dumps(data, ensure_ascii=False, indent=2))
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            ap.error(str(error))
        return
    perceive = importlib.import_module("perceive")
    if cmd == "frame":
        from frames import frame_sequence
        ap = argparse.ArgumentParser(prog="reel frame")
        ap.add_argument("out")
        mode = ap.add_mutually_exclusive_group(required=True)
        mode.add_argument("--at", type=float)
        mode.add_argument("--t0", type=float)
        ap.add_argument("--t1", type=float)
        x = ap.parse_args(sys.argv[2:])
        if (x.t0 is None) != (x.t1 is None):
            ap.error("use --at S or both --t0 S and --t1 S")
        try:
            if x.at is not None:
                print(perceive.frame_at(x.out, x.at))
            else:
                print(json.dumps(frame_sequence(x.out, x.t0, x.t1), indent=2))
        except ValueError as error:
            ap.error(str(error))
        return
    if cmd == "zoom":
        ap = argparse.ArgumentParser(prog="reel zoom"); ap.add_argument("out")
        ap.add_argument("--t0", type=float, required=True); ap.add_argument("--t1", type=float, required=True)
        x = ap.parse_args(sys.argv[2:])
        try:
            perceive.zoom(x.out, x.t0, x.t1)
        except ValueError as error:
            ap.error(str(error))
        return
    if cmd == "watch":
        ap = argparse.ArgumentParser(prog="reel watch"); ap.add_argument("film"); ap.add_argument("--out", required=True)
        ap.add_argument("--page", type=float, default=30.0); ap.add_argument("--lang", default="ko")
        ap.add_argument("--until", type=float, help="analyse only the first SECONDS, in a new or empty output directory")
        ap.add_argument("--title"); ap.add_argument("--subject"); ap.add_argument("--genre")
        x = ap.parse_args(sys.argv[2:])
        try:
            perceive.build(x.film, pathlib.Path(x.out), x.page, x.lang, x.title, x.subject, x.genre, x.until)
        except ValueError as error:
            ap.error(str(error))
        return
    print(__doc__)


if __name__ == "__main__":
    main()
