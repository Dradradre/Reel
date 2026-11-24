"""Score published blind counts, or repeat the everyday trial using a local Codex login."""
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import random
import subprocess
import sys
from tempfile import TemporaryDirectory

import numpy as np

if __package__:
    from .check_environment_attacks import mixtures
else:
    from check_environment_attacks import mixtures


def scores(rows):
    totals = {}
    for arm in ("before", "after"):
        error = np.array([r[arm] - r["reference"] for r in rows])
        totals[arm] = dict(exact=int((error == 0).sum()), absolute_error=int(abs(error).sum()),
                           overcount=int(error.clip(min=0).sum()), undercount=int((-error).clip(min=0).sum()))
    change = np.array([abs(r["after"] - r["reference"]) - abs(r["before"] - r["reference"]) for r in rows])
    rng = np.random.default_rng(48129)
    draws = rng.choice(change, size=(10000, len(rows))).mean(axis=1)
    totals["paired"] = dict(better=int((change < 0).sum()), worse=int((change > 0).sum()),
                            tied=int((change == 0).sum()), clips=len(rows),
                            mean_absolute_error_change_95pct_bootstrap=np.percentile(draws, [2.5, 97.5]).round(3).tolist())
    return totals


async def capture(wav, folder):
    import soundfile as sf
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {key: value for key, value in os.environ.items() if key.startswith("REEL_") or
           key in ("PATH", "PYTHONPATH", "HF_HUB_OFFLINE", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS")}
    params = StdioServerParameters(command=sys.executable, args=["-m", "reel.cli", "mcp", str(wav),
                                   "--image-client", "--lang", "en"], env=env)
    async with stdio_client(params) as (rx, tx), ClientSession(rx, tx) as client:
        await client.initialize()
        response = await client.call_tool("view", dict(turn=1, native_audio=False, continuous_seconds=4))
        if response.isError:
            raise ValueError(response.content[0].text)
        report = json.loads(response.content[0].text)
        if abs(report["end_s"] - sf.info(wav).duration) > .001:
            raise ValueError("the view must cover the whole stimulus")
        images = []
        for i, block in enumerate(response.content[1:]):
            if block.type != "image":
                raise ValueError("expected an image-client replay")
            image = folder / f"image-{i}.png"
            image.write_bytes(base64.b64decode(block.data))
            images.append(image)
        return report, images


def ask(model, prompt, report, images):
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    with TemporaryDirectory() as empty:
        output = Path(empty) / "answer"
        cmd = ["codex", "exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
               "--disable", "plugins", "--disable", "shell_tool", "-s", "read-only", "-C", empty,
               "-m", model, "-c", 'model_reasoning_effort="low"', "-o", str(output)]
        for image in images:
            cmd.extend(["-i", str(image)])
        subprocess.run([*cmd, "-"], cwd=empty, env=env, input=prompt + json.dumps(report),
                       text=True, check=True, stdout=subprocess.DEVNULL, timeout=120)
        raw = output.read_text().strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        count = json.loads(raw)["count"]
        if type(count) is not int or count < 0:
            raise ValueError("expected a nonnegative integer")
        return count


def repeat(report, audio_dir):
    import soundfile as sf

    panel = report["everyday_confirmation"]
    cases = [dict(row) for row in panel["rows"]]
    by_mix = {row["mixture"]: i for i, row in enumerate(cases)}
    with TemporaryDirectory() as tmp:
        captures = {}
        for index, (row, samples, rate) in enumerate(mixtures(panel["inputs"], audio_dir)):
            if index not in by_mix:
                continue
            i = by_mix[index]
            if len(row["reference_s"]) != cases[i]["reference"]:
                raise ValueError("stimulus count differs from the published reference")
            folder = Path(tmp) / str(i)
            folder.mkdir()
            wav = folder / "film.wav"
            sf.write(wav, samples, rate, subtype="FLOAT")
            captures[i] = asyncio.run(capture(wav, folder))
        work = [(i, arm) for i in range(len(cases)) for arm in ("before", "after")]
        random.Random(104721).shuffle(work)
        for i, arm in work:
            observation, images = captures[i]
            stimulus = dict(observation, audio=dict(observation["audio"]))
            if arm == "before":
                stimulus["audio"].pop("attacks", None)
            cases[i][arm] = ask(report["reader"], report["everyday_prompt"], stimulus, images)
    return dict(rows=cases, scores=scores(cases))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--repeat-with", type=Path, metavar="ESC_AUDIO_DIR")
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    if args.repeat_with:
        print(json.dumps(repeat(report, args.repeat_with), indent=2))
    else:
        for name in ("music", "everyday_first", "everyday_confirmation"):
            panel = report[name]
            actual = scores(panel["rows"])
            if actual != panel["scores"]:
                raise ValueError(f"{name}: published totals differ from reader responses")
            print(name, json.dumps(actual))
