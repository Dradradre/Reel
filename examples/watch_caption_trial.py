"""Repeat an audio-only blind recall trial with local Codex and the original Clotho evaluation WAVs."""
import argparse
import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import soundfile as sf

from check_caption_matches import BENCHMARK
from reel.check_captions import checked_report
from reel.hearing import SoundReader
from reel.mcp_server import Viewing
from reel.play import play


def capture(source, out, verify):
    end = sf.info(source).duration
    with TemporaryDirectory() as tmp, SoundReader() as sound:
        with closing(play(source, Path(tmp) / "turns", step=8, continuous=True, recording_only=True,
                          tracks="sound")) as playback:
            view = Viewing(playback, sound, None, None)
            turn = 1
            while json.loads(view.advance(turn, False, False)[0].text)["status"] != "end_of_view":
                turn += 1
            blocks = view.listen(turn - 1, None, 0, end, describe=True, reference_captions=True)
    report = json.loads(blocks[0].text)
    if report["status"] != "available" or report["caption_matches"]["status"] != "available":
        raise RuntimeError("prepare the waveform reader and caption library before running the trial")
    if verify:
        checked = checked_report(report["caption_matches"], source, 0, end, 0)
        if checked["status"] != "available":
            raise RuntimeError(f"the optional caption verifier is unavailable: {checked}")
        (out / "verified.json").write_text(json.dumps(checked))
    (out / "spectrum.png").write_bytes(base64.b64decode(blocks[1].data))
    (out / "report.json").write_text(json.dumps(report))


def blind_caption(report, image, prompt):
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    with TemporaryDirectory() as tmp:
        dest = Path(tmp) / "answer.json"
        # Neutral paths keep the source filename and panel index away from the client.
        attachment = Path(tmp) / "recording.png"
        attachment.write_bytes(image.read_bytes())
        subprocess.run(["codex", "exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
                        "--disable", "plugins", "--disable", "shell_tool", "-s", "read-only", "-C", tmp,
                        "-m", "gpt-6.1-terra", "-c", 'model_reasoning_effort="low"', "-o", str(dest),
                        "-i", str(attachment), "-"], input=prompt + "\n" + json.dumps(report), text=True,
                       env=env, cwd=tmp, stdout=subprocess.DEVNULL, check=True, timeout=120)
        response = json.loads(dest.read_text().strip().removeprefix("```json").removesuffix("```").strip())
        if not isinstance(response.get("description"), str) or not response["description"].strip():
            raise ValueError("the blind reader returned no description")
        return response["description"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", type=Path, help="directory with the original Clotho-AQA audio_files.zip WAVs")
    parser.add_argument("--panel", choices=["confirmation", "unseen_uploaders"], default="unseen_uploaders")
    parser.add_argument("--out", type=Path, required=True, help="new directory for the evidence and responses")
    args = parser.parse_args()
    study = json.loads(BENCHMARK.read_text())
    rows = study["panels"][args.panel]
    # The original study explicitly used Audio Flamingo. Each call receives released PCM only.
    os.environ["REEL_AUDIO_READER"] = "flamingo"
    args.out.mkdir(parents=True, exist_ok=False)
    directories = []
    for i, row in enumerate(rows):
        source = args.recordings / row["file"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != row["recording_sha256"]:
            raise ValueError(f"unexpected recording in trial {i}")
        directory = args.out / f"c{i:02}"
        directory.mkdir()
        capture(source, directory, "verified" in row["descriptions"])
        directories.append(directory)
    for i, (directory, row) in enumerate(zip(directories, rows, strict=True)):
        answers = {}
        for arm in row["descriptions"]:
            report = json.loads((directory / "report.json").read_text())
            if arm == "before":
                del report["caption_matches"]
            elif arm == "verified":
                report["caption_matches"] = json.loads((directory / "verified.json").read_text())
            elif arm == "other-reference":
                other = directories[(i + len(rows) // 2) % len(rows)]
                wrong = json.loads((other / "report.json").read_text())["caption_matches"]["windows"]
                for n, window in enumerate(report["caption_matches"]["windows"]):
                    src = wrong[n % len(wrong)]["active_channels"]
                    for j, channel in enumerate(window["active_channels"]):
                        channel["matches"] = src[j % len(src)]["matches"] if src else []
            answers[arm] = blind_caption(report, directory / "spectrum.png", study["protocol"]["prompt"])
            (directory / "descriptions.json").write_text(json.dumps(answers, indent=2))
        print(f"{i + 1}/{len(rows)}", flush=True)


if __name__ == "__main__":
    main()
