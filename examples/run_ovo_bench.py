"""Evaluate Reel's timestamped picture sheets on OVO-Bench with the signed-in Codex CLI.

Use the authors' checkout and unpacked source videos. With --pilot, select two
questions per task and one checkpoint per forward question before reading answers.
Store answers separately so an interrupted run can resume in the same directory.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from reel.common import probe, sample_boundary
from reel.frames import frame_sheets, native_frames, sheet_size

BACKWARD = {"ASI", "HLD", "EPM"}
SALT = "reel-ovo-baseline-v1/"


def ranked(values, key):
    return sorted(values, key=lambda item: hashlib.sha256((SALT + str(key(item))).encode()).digest())


def checkpoints(annotations, pilot):
    if pilot:
        tasks = dict.fromkeys(row["task"] for row in annotations)
        annotations = [row for task in tasks
                       for row in ranked([r for r in annotations if r["task"] == task], lambda r: r["id"])[:2]]
    for row in annotations:
        if "test_info" not in row:
            yield row, None, row["realtime"]
            continue
        indices = range(len(row["test_info"]))
        if pilot:
            indices = ranked(indices, lambda j: (row["id"], j))[:1]
        for j in indices:
            yield row, j, row["test_info"][j]["realtime"]


def prefix_pictures(source, cutoff, budget):
    info = probe(source)
    stop = min(sample_boundary(cutoff, 1_000_000), sample_boundary(info["dur"], 1_000_000))
    if stop <= 0:
        raise ValueError(f"no visible prefix ending at {cutoff}")
    targets = [(stop - 1) * k // (budget - 1) for k in range(budget)]
    size = sheet_size(info)
    selected = []
    previous = None
    i = 0
    for frame in native_frames(source, *size, stop_us=stop):
        while i < len(targets) and targets[i] < frame[0]:
            if previous is not None and (not selected or selected[-1][0] != previous[0]):
                selected.append(previous)
            i += 1
        previous = frame
    if previous is not None and i < len(targets) and (not selected or selected[-1][0] != previous[0]):
        selected.append(previous)
    if not selected or selected[-1][0] >= stop:
        raise ValueError("no displayed frame strictly inside the prefix")
    return size, selected, stop


def read_item(source, row, index, cutoff, prompt, output, model):
    key = str(row["id"]) if index is None else f"{row['id']}_{index}"
    dest = output / f"{key}.json"
    if dest.exists():
        return json.loads(dest.read_text())
    size, frames, stop = prefix_pictures(source, cutoff, 24)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        pages = frame_sheets(work, *size, frames)
        cmd = ["codex", "exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
               "--disable", "plugins", "--disable", "shell_tool", "-c", 'web_search="disabled"',
               "-c", 'model_reasoning_effort="low"', "-s", "read-only", "-C", tmp, "-m", model]
        for i, page in enumerate(pages, 1):
            path = work / f"view-{i}.png"
            Path(page["file"]).replace(path)
            cmd.extend(["-i", str(path)])
        answer = work / "response.txt"
        env = os.environ.copy()
        env.pop("OPENAI_API_KEY", None)
        subprocess.run([*cmd, "-o", str(answer), "-"], input=prompt, text=True, env=env,
                       stdout=subprocess.DEVNULL, check=True, timeout=180)
        result = dict(id=row["id"], index=index, task=row["task"], until_us=stop,
                      pts=[f[0] for f in frames], response=answer.read_text().strip(), requested_model=model)
    with tempfile.NamedTemporaryFile(mode="w", dir=output, delete=False) as saved:
        json.dump(result, saved)
    Path(saved.name).replace(dest)
    return result


def official_score(items, official):
    data = {"backward": [], "realtime": [], "forward": []}
    for row, index, result in items:
        task = row["task"]
        if index is None:
            group = "backward" if task in BACKWARD else "realtime"
            data[group].append(dict(task=task, ground_truth=chr(65 + row["gt"]), response=result["response"]))
        else:
            check = dict(row["test_info"][index], response=result["response"])
            data["forward"].append(dict(task=task, test_info=[check]))
    with redirect_stdout(io.StringIO()) as text:
        official(SimpleNamespace(model="Reel + local Codex"), data).score()
    return text.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True, help="OVO-Bench repository")
    parser.add_argument("--videos", type=Path, required=True, help="unpacked src_videos directory")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6.1-terra")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--pilot", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.checkout.resolve()))
    from utils.OVOBench import OVOBenchOffline
    from utils.OVOBenchScore import OVOBenchOfflineScore

    source = args.checkout / "data/ovo_bench_new.json"
    args.out.mkdir(exist_ok=True, parents=True)
    config = dict(model=args.model, frames=24, protocol="ovo-picture-prefix-v1",
                  annotation_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  source_root=str(args.videos.resolve()))
    marker = args.out / "configuration.json"
    if marker.exists() and json.loads(marker.read_text()) != config:
        parser.error("existing answers use a different configuration, choose a separate --out")
    marker.write_text(json.dumps(config))
    prompt_builder = OVOBenchOffline(None)

    def run(item):
        row, index, cutoff = item
        prompt = prompt_builder.build_prompt(row["task"], row.get("question"), row.get("options"), row, index)
        result = read_item(args.videos / row["video"], row, index, cutoff, prompt, args.out, args.model)
        return row, index, result

    items = list(checkpoints(json.loads(source.read_text()), args.pilot))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        report = official_score(pool.map(run, items), OVOBenchOfflineScore)
    print(report)
    (args.out / ("pilot-score.txt" if args.pilot else "score.txt")).write_text(report)


if __name__ == "__main__":
    main()
