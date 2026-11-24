"""Collect blind listener reports on released clips and compare answers from the same exposure.

python examples/listener_study.py collect study.json responses.sqlite --reader participant-01
python examples/listener_study.py score responses.sqlite
"""

import argparse
from collections import Counter, defaultdict
import hashlib
from itertools import combinations
import json
from pathlib import Path
import random
import sqlite3
import subprocess
import tempfile
import time


def majority(votes):
    counts = Counter(votes).most_common()
    return counts[0][0] if counts and (len(counts) == 1 or counts[0][1] > counts[1][1]) else None


def compare_votes(items):
    """Macro-average each answered item, including model abstention in agreement."""
    decisions = Counter()
    rater_agreement, pair_agreement, briers = [], [], []
    for votes, answer, probability in items:
        if len(votes) < 2:
            continue
        pairs = list(combinations(votes, 2))
        pair_agreement.append(sum(a == b for a, b in pairs) / len(pairs))
        rater_agreement.append(sum(answer == v for v in votes) / len(votes))
        consensus = majority(votes)
        decisions["items"] += 1
        if consensus is None:
            decisions["human_tie"] += 1
        elif answer == "unknown":
            decisions["unanswered"] += 1
        else:
            decisions["correct" if answer == consensus else "wrong"] += 1
        if probability is not None and set(votes) <= {"yes", "no"}:
            if not 0 <= probability <= 1:
                raise ValueError("prob_yes must be in [0,1]")
            briers.append(sum((probability - float(v == "yes")) ** 2 for v in votes) / len(votes))

    def mean(values):
        return sum(values) / len(values) if values else None

    return dict(
        decisions,
        human_pair_agreement=mean(pair_agreement),
        model_human_agreement=mean(rater_agreement),
        individual_vote_brier=mean(briers),
        brier_items=len(briers),
    )


def connect(path):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS reports (
        panel TEXT, trial TEXT, phase TEXT, reader TEXT, kind TEXT, payload TEXT,
        PRIMARY KEY(panel,trial,phase,reader))""")
    return db


def record(db, row):
    with db:
        db.execute(
            "INSERT INTO reports VALUES(?,?,?,?,?,?)",
            (row["panel"], row["trial"], row["phase"], row["reader"], row["kind"], json.dumps(row)),
        )


def collect(study, db, reader, seed):
    manifest = json.loads(study.read_text())
    trials = list(manifest["trials"])
    if len({t["id"] for t in trials}) != len(trials):
        raise ValueError("trial IDs must be unique")
    random.Random(seed).shuffle(trials)
    # Recall follows the presentation block regardless of the randomized clip order.
    trials.sort(key=lambda trial: trial.get("phase") == "recall")
    for trial in trials:
        phase = trial.get("phase", "first_pass")
        if phase not in ("first_pass", "directed", "recall") or trial["tracks"] not in (
            "picture",
            "sound",
            "both",
        ):
            raise ValueError("choose a supported exposure phase and track configuration")
        done = db.execute(
            "SELECT 1 FROM reports WHERE panel=? AND trial=? AND phase=? AND reader=?",
            (manifest["panel"], trial["id"], phase, reader),
        ).fetchone()
        if done:
            continue
        clip = (study.parent / trial["media"]).resolve()
        with clip.open("rb") as recording:
            media_hash = hashlib.file_digest(recording, "sha256").hexdigest()
        if media_hash != trial["sha256"]:
            raise ValueError("released clip bytes differ from study manifest")
        if phase == "recall":
            prior = db.execute(
                """SELECT payload FROM reports WHERE panel=? AND trial=? AND reader=? AND
                                kind='human' AND phase IN ('first_pass','directed')""",
                (manifest["panel"], trial.get("from_trial"), reader),
            ).fetchone()
            if not prior:
                raise ValueError("recall requires a completed presentation for this reader")
            original = json.loads(prior[0])
            if original["evidence"] != media_hash or original["tracks"] != trial["tracks"]:
                raise ValueError("recall must refer to the same released evidence and tracks")
            exposure_end = original.get("exposure_ended_unix")
            print("Answer from memory of the earlier clip", trial["from_trial"])
        else:
            with tempfile.TemporaryDirectory() as td:
                anonymous = Path(td) / ("stimulus" + clip.suffix)
                anonymous.symlink_to(clip)
                cmd = [
                    "ffplay",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-autoexit",
                    "-window_title",
                    "Reel",
                    str(anonymous),
                ]
                if trial.get("tracks") == "sound":
                    cmd.insert(-1, "-nodisp")
                elif trial.get("tracks") == "picture":
                    cmd.insert(-1, "-an")
                print("Clip", trial["id"])
                input("Press Enter to begin this clip. ")
                if phase == "directed":
                    print(trial["question"])
                subprocess.run(cmd, check=True)
            exposure_end = time.time()
        delay = max(0.0, time.time() - exposure_end) if exposure_end is not None else None
        start = time.monotonic()
        print(trial["question"])
        choices = trial["choices"]
        while (answer := input(" / ".join([*choices, "unknown"]) + " > ").strip()) not in [*choices, "unknown"]:
            pass
        latency = time.monotonic() - start
        note = input("What did you notice? ").strip()
        record(
            db,
            dict(
                panel=manifest["panel"],
                trial=trial["id"],
                phase=phase,
                reader=reader,
                kind="human",
                evidence=media_hash,
                tracks=trial["tracks"],
                question=trial["question"],
                answer=answer,
                note=note,
                response_seconds_after_prompt=latency,
                response_seconds_after_playback=latency if phase != "recall" else None,
                exposure_ended_unix=exposure_end,
                delay_since_exposure_s=delay,
                from_trial=trial.get("from_trial"),
            ),
        )


def score(db):
    buckets = defaultdict(lambda: defaultdict(list))
    for (data,) in db.execute("SELECT payload FROM reports"):
        r = json.loads(data)
        key = (r["panel"], r["trial"], r["phase"], r["evidence"], r["tracks"], r["question"])
        buckets[key][r["kind"]].append(r)
    by_reader = defaultdict(list)
    for key, groups in buckets.items():
        votes = [r["answer"] for r in groups["human"]]
        for r in groups["model"]:
            by_reader[r["reader"], key[2]].append((votes, r["answer"], r.get("prob_yes")))
    return [dict(reader=k[0], phase=k[1], **compare_votes(v)) for k, v in by_reader.items()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    c = sub.add_parser("collect")
    c.add_argument("study", type=Path)
    c.add_argument("database", type=Path)
    c.add_argument("--reader", required=True)
    c.add_argument("--seed", type=int, default=0)
    s = sub.add_parser("score")
    s.add_argument("database", type=Path)
    m = sub.add_parser("import-model")
    m.add_argument("reports", type=Path)
    m.add_argument("database", type=Path)
    args = parser.parse_args()
    db = connect(args.database)
    try:
        if args.action == "collect":
            collect(args.study, db, args.reader, args.seed)
        elif args.action == "import-model":
            rows = json.loads(args.reports.read_text())
            with db:
                for r in rows:
                    for key in ("panel", "trial", "phase", "evidence", "tracks", "question", "reader", "answer"):
                        if not isinstance(r.get(key), str) or not r[key].strip():
                            raise ValueError("missing report field " + key)
                    db.execute(
                        "INSERT INTO reports VALUES(?,?,?,?,?,?)",
                        (
                            r["panel"],
                            r["trial"],
                            r["phase"],
                            r["reader"],
                            "model",
                            json.dumps(dict(r, kind="model")),
                        ),
                    )
        else:
            print(json.dumps(score(db), indent=2))
    finally:
        db.close()
