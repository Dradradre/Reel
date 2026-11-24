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


def blind_reply(report, image, prompt):
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    with TemporaryDirectory() as tmp:
        dest = Path(tmp) / "answer.json"
        # Neutral paths keep the source filename and panel index away from the client.
        attachments = []
        if image is not None:
            attachment = Path(tmp) / "recording.png"
            attachment.write_bytes(image.read_bytes())
            attachments = ["-i", str(attachment)]
        subprocess.run(["codex", "exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
                        "--disable", "plugins", "--disable", "shell_tool", "-s", "read-only", "-C", tmp,
                        "-m", "gpt-6.1-terra", "-c", 'model_reasoning_effort="low"', "-o", str(dest),
                        *attachments, "-"], input=prompt + "\n" + json.dumps(report), text=True,
                       env=env, cwd=tmp, stdout=subprocess.DEVNULL, check=True, timeout=120)
        return json.loads(dest.read_text().strip().removeprefix("```json").removesuffix("```").strip())


def ask_questions(directory, items):
    # Keep human votes, recording names and expected responses out of all three sessions.
    questions = [{k: item[k] for k in ("question", "form")} for item in items]
    prompt = ("Answer the questions about the recording in order. For a yes/no question, answer yes, no or "
              "unknown if the audio evidence is insufficient. For an open question, give the shortest supported "
              "English answer; if you cannot identify it, answer unknown. Only verified speech can establish "
              'spoken words. Return JSON {"answers":["...", ...]}.\n' + json.dumps(questions))
    responses = {}
    for arm in ("before", "verified", "no_recording"):
        report, image = {}, None
        if arm != "no_recording":
            image = directory / "spectrum.png"
            report = json.loads((directory / "report.json").read_text())
            if arm == "before":
                del report["caption_matches"]
            else:
                report["caption_matches"] = json.loads((directory / "verified.json").read_text())
        answers = blind_reply(report, image, prompt)["answers"]
        if (not isinstance(answers, list) or len(answers) != len(questions)
                or any(not isinstance(a, str) or not a.strip() or
                       (q["form"] == "yes/no" and a.lower().strip(" .") not in ("yes", "no", "unknown"))
                       for q, a in zip(questions, answers))):
            raise ValueError("the reader did not return one supported response per question")
        responses[arm] = answers
        (directory / "questions.json").write_text(json.dumps({"questions": questions, "responses": responses}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", type=Path, help="directory with the original Clotho-AQA audio_files.zip WAVs")
    parser.add_argument("--panel", choices=["confirmation", "unseen_uploaders"], default="unseen_uploaders")
    parser.add_argument("--out", type=Path, required=True, help="new directory for the evidence and responses")
    parser.add_argument("--questions", action="store_true", help="also run the three Clotho-AQA listening conditions")
    args = parser.parse_args()
    if args.questions and args.panel != "unseen_uploaders":
        parser.error("--questions uses the unseen_uploaders panel")
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
            description = blind_reply(report, directory / "spectrum.png", study["protocol"]["prompt"])["description"]
            if not isinstance(description, str) or not description.strip():
                raise ValueError("the blind reader returned no description")
            answers[arm] = description
            (directory / "descriptions.json").write_text(json.dumps(answers, indent=2))
        if args.questions:
            case = next(c for c in study["listening_questions"] if c["source_id"] == row["source_id"])
            ask_questions(directory, case["items"])
        print(f"{i + 1}/{len(rows)}", flush=True)


if __name__ == "__main__":
    main()
