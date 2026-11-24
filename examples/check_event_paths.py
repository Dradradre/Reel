"""Measure anonymous event retrieval, voice continuity and picture landmarks.

Optional datasets are supplied locally. The public result file can be rescored without models.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "docs/benchmarks/event-paths.json"


def source_trials(cases_path, recordings):
    from reel.recognition import RATE, channel_audio, recognize

    results = []
    cases = json.loads(cases_path.read_text())
    for case in cases["mixtures"] if isinstance(cases, dict) else cases:
        if not case["id"].startswith("holdout"):
            continue
        sources = []
        for key in case["ids"]:
            path = recordings / key / "source.wav"
            if not path.exists():
                path = recordings / (key + ".wav")
            sources.append(channel_audio(path, 1))
        for index, key in enumerate(case["ids"]):
            raw, bed = sources[index], sources[1 - index]
            # Loudest half-second of the annotated recording. This selects signal even
            # where an original contains long leading or trailing silence.
            n = min(RATE // 2, len(raw))
            if n < RATE // 10:
                raise ValueError("reference is too short")
            starts = list(range(0, len(raw) - n + 1, RATE // 8)) or [0]
            left = max(starts, key=lambda i: np.mean(raw[i : i + n] ** 2))
            ref = raw[left : left + n]
            bed = np.resize(bed, 4 * RATE)
            bed = bed / max(np.sqrt(np.mean(bed**2)), 1e-9) * 0.04
            gain = 0.055 / max(np.sqrt(np.mean(ref**2)), 1e-9)
            offset = int.from_bytes(hashlib.sha256(key.encode()).digest()[:2], "little") % (len(bed) - n)
            mix = bed.copy()
            mix[offset : offset + n] -= ref * gain
            positive = recognize(ref, mix)["matches"]
            negative = recognize(ref, bed)["matches"]
            results.append(
                dict(
                    case=case["id"],
                    source=key,
                    label=case["labels"][index],
                    expected_s=offset / RATE,
                    positive=positive[:1],
                    negative=negative[:1],
                )
            )
    if not results:
        raise ValueError("no holdout trials")
    return results


def voice_trials(directory):
    from reel.voices import VoiceEncoder, VoiceHistory
    import soundfile as sf

    groups = {}
    index = directory / "rows.json"
    if index.is_file():
        for row in json.loads(index.read_text()):
            groups.setdefault(row["speaker"], []).append(row["id"])
    else:
        for row in json.loads(DATA.read_text())["voices"]:
            groups.setdefault(row["speaker"], []).append(row["utterance"])
    # One enrollment and one independent utterance per speaker. All enrollments
    # are consumed first. Gallery labels never enter VAD, encoder or assignment.
    history = VoiceHistory(VoiceEncoder())
    gallery, rows = {}, []
    first = 0
    for phase in range(2):
        for sp, ids in sorted(groups.items()):
            utterance = ids[phase]
            path = directory / (utterance + ".wav")
            if not path.is_file():
                speaker, chapter, _ = utterance.split("-")
                path = directory / speaker / chapter / (utterance + ".flac")
            # A gap in sample numbers resets audio overlap between unrelated files.
            result = history.read(path, first)
            first += sf.info(path).frames + 1000
            tracks = Counter(w["track"] for w in result["windows"] if "track" in w)
            chosen = tracks.most_common(1)[0][0] if tracks else None
            if phase == 0:
                gallery[sp] = chosen
            rows.append(
                dict(
                    speaker=sp,
                    utterance=ids[phase],
                    enrollment=phase == 0,
                    windows=len(result["windows"]),
                    assigned=dict(tracks),
                    unresolved=len(result["windows"]) - sum(tracks.values()),
                    majority=chosen,
                    gallery_track=gallery[sp],
                )
            )
    return rows


def picture_trials(annotations, videos=None):
    import cv2
    from reel.event_stream import PictureTracks

    rows = []
    for video, clip in json.loads(annotations.read_text()).items():
        path = videos / (video + ".mp4") if videos is not None else Path(clip["path"])
        if not path.is_file():
            continue
        cap = cv2.VideoCapture(str(path))
        try:
            mapped = [dict(zip(t["frame_ids"], zip(t["points"][1], t["points"][0]))) for t in clip["points"]]
            n = clip["metadata"]["num_frames"]
            candidates = [i for i in range(0, n - 29, 30) if any(i in t and i + 29 in t for t in mapped)]
            if not candidates:
                continue
            start = min(candidates, key=lambda i: hashlib.sha256(f"reel-events/{video}/{i}".encode()).digest())
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            tracker = PictureTracks()
            fps = cap.get(cv2.CAP_PROP_FPS)
            anchors = {}
            for frame in range(30):
                ok, image = cap.read()
                if not ok:
                    raise ValueError("missing annotated exposure")
                h, w = image.shape[:2]
                size = (min(384, round(240 * w / h)), min(240, round(384 * h / w)))
                rgb = cv2.cvtColor(cv2.resize(image, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
                pts = round(frame / fps * 1e6)
                seen = tracker.read([(pts, None, rgb.tobytes())], size, pts / 1e6, (frame + 1) / fps)
                if frame == 0:
                    for i, t in enumerate(mapped):
                        if start not in t or start + 29 not in t:
                            continue
                        ranked = sorted(
                            (np.linalg.norm(np.array(e["last_point"]) - t[start]), e["track"], e["last_point"])
                            for e in seen
                        )
                        anchors[i] = ranked[0] if ranked and ranked[0][0] <= 0.08 else None
            final = {e["track"]: e["last_point"] for e in seen}
            for i, anchor in anchors.items():
                t = mapped[i]
                point = final.get(anchor[1]) if anchor else None
                rows.append(
                    dict(
                        video=video,
                        frame=start,
                        annotation=i,
                        eligible=bool(anchor),
                        truth=t[start + 29],
                        fixed=anchor[2] if anchor else None,
                        observed=point,
                    )
                )
        finally:
            cap.release()
    return rows


def summary(data):
    result = {}
    if "sounds" in data:
        rows = data["sounds"]
        result["sounds"] = dict(
            trials=len(rows),
            located=sum(
                bool(r["positive"]) and abs(r["positive"][0]["start_s"] - r["expected_s"]) <= 0.05 for r in rows
            ),
            located_with_strong_match=sum(
                bool(r["positive"])
                and r["positive"][0]["strong_waveform_match"]
                and abs(r["positive"][0]["start_s"] - r["expected_s"]) <= 0.05
                for r in rows
            ),
            false_strong_matches=sum(
                bool(r["negative"]) and r["negative"][0]["strong_waveform_match"] for r in rows
            ),
        )
    if "voices" in data:
        rows = [r for r in data["voices"] if not r["enrollment"]]
        gallery = Counter(r["gallery_track"] for r in rows)
        result["voices"] = dict(
            speakers=len(rows),
            distinct_enrollments=len(set(gallery) - {None}),
            uniquely_reidentified=sum(
                r["majority"] is not None and r["majority"] == r["gallery_track"] and gallery[r["majority"]] == 1
                for r in rows
            ),
            unassigned=sum(r["majority"] is None for r in rows),
        )
    if "pictures" in data:
        rows = [r for r in data["pictures"] if r["eligible"]]

        def hits(key):
            return sum(
                bool(r[key] is not None and np.linalg.norm(np.array(r["truth"]) - r[key]) <= 0.08) for r in rows
            )

        result["pictures"] = dict(
            videos=len({r["video"] for r in data["pictures"]}),
            annotated=len(data["pictures"]),
            initial_anchor=len(rows),
            visible_end=sum(r["observed"] is not None for r in rows),
            tracked_hits=hits("observed"),
            fixed_hits=hits("fixed"),
        )
    if "layers" in data:
        gains = [r["scores"]["selected"] - r["scores"]["mix"] for r in data["layers"]]
        result["other_recording_same_label"] = dict(
            trials=len(gains),
            improved=sum(v > 1e-4 for v in gains),
            degraded=sum(v < -1e-4 for v in gains),
            median_si_sdr_gain_db=float(np.median(gains)),
        )
    if "field" in data:
        result["natural_field"] = dict(
            clips=len(data["field"]),
            with_exact_source=sum(any("E" in g for g in trial["grades"]) for trial in data["field"]),
        )
    if "human_trials" in data:
        if __package__:
            from .listener_study import compare_votes
            from .check_first_impressions import relative_separation
        else:
            from listener_study import compare_votes
            from check_first_impressions import relative_separation

        q = [q for trial in data["human_trials"] for q in trial["questions"]]
        for arm in ("current", "same_prompt_without_events"):
            result[arm] = compare_votes(
                [
                    (row["votes"], value["answer"], value["prob_yes"])
                    for row in q
                    for value in [row if arm == "current" else row[arm]]
                ]
            )
        result["human_relative_free_recall"] = {
            arm: relative_separation(data["human_caption_scores"]["by_film"], arm)
            for arm in ("previous", "current")
        }
    if "depth" in data:
        rows = data["depth"]
        result["depth"] = dict(
            images=len({r["image"] for r in rows}),
            pairs=len(rows),
            correct=sum(r["closer"] == r["choice"] for r in rows),
            unresolved=sum(r["choice"] is None for r in rows),
        )
    if "av_binding" in data:
        result["binding"] = {}
        for arm in ("aligned", "shifted"):
            visible = [r for r in data["av_binding"] if r["visible"]]
            hidden = [r for r in data["av_binding"] if not r["visible"]]

            def hits_box(r):
                for a, b, c, d in r[arm]:
                    x, y = (a + c) / 2, (b + d) / 2
                    if any(
                        left <= x <= right and top <= y <= bottom for _, (left, top, right, bottom) in r["boxes"]
                    ):
                        return True
                return False

            result["binding"][arm] = dict(
                visible=len(visible),
                proposed_visible=sum(bool(r[arm]) for r in visible),
                visible_box_hits=sum(hits_box(r) for r in visible),
                hidden=len(hidden),
                proposed_hidden=sum(bool(r[arm]) for r in hidden),
            )
    if "binding_listeners" in data:
        result["source_in_picture"] = {}
        for arm in ("matched", "other"):
            visible = [r for r in data["binding_listeners"] if r["visible"]]
            hidden = [r for r in data["binding_listeners"] if not r["visible"]]

            def inside(row):
                answer = row[arm]["response"]
                if answer["status"] != "visible":
                    return False
                x, y = answer["point"]
                return any(a <= x <= c and b <= y <= d for _, (a, b, c, d) in row["boxes"])

            result["source_in_picture"][arm] = dict(
                visible=len(visible),
                in_box=sum(inside(r) for r in visible),
                visible_guesses=sum(r[arm]["response"]["status"] == "visible" for r in visible),
                hidden=len(hidden),
                on_hidden=dict(Counter(r[arm]["response"]["status"] for r in hidden)),
            )
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=Path, default=ROOT / "docs/benchmarks/acoustic-layers.json")
    ap.add_argument("--fsd", type=Path)
    ap.add_argument("--speech", type=Path)
    ap.add_argument("--points", type=Path)
    ap.add_argument("--videos", type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    data = {}
    if args.fsd:
        data["sounds"] = source_trials(args.cases, args.fsd)
    if args.speech:
        data["voices"] = voice_trials(args.speech)
    if args.points:
        data["pictures"] = picture_trials(args.points, args.videos)
    if args.output and not data:
        ap.error("supply a dataset to generate measurements")
    if args.output:
        args.output.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(json.dumps(summary(data or json.loads(DATA.read_text())), indent=2))
