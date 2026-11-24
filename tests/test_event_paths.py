import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf

from reel.event_memory import EventMemory
from reel.event_stream import EventStream, PictureTracks, SpectralTracks
from reel.event_timing import TimingHistory, alignment
from reel.recognition import RATE, recognize
from reel.voices import VoiceHistory


class EventPaths(unittest.TestCase):
    def test_anonymous_bands_cross_the_pause_and_change_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            f = d / "input.wav"
            grid = np.arange(RATE) / RATE
            a, b = 0.3 * np.sin(2 * np.pi * 440 * grid), 0.1 * np.sin(2 * np.pi * 810 * grid)
            tracks = SpectralTracks()
            one = []
            for i, pcm in enumerate((np.stack((a, b), 1), np.stack((b, a), 1))):
                sf.write(f, pcm, RATE, subtype="FLOAT")
                rows = tracks.read(f, i * RATE)
                one.append({round(np.mean(r["hz"]) / 100): r for r in rows})
            self.assertEqual(one[0][4]["track"], one[1][4]["track"])
            self.assertEqual(one[0][8]["track"], one[1][8]["track"])
            self.assertGreater(one[0][4]["channel_peak_dbfs"][0], -15)
            self.assertGreater(one[1][4]["channel_peak_dbfs"][1], -15)
            sf.write(f, np.zeros((RATE, 2)), RATE)
            self.assertFalse(tracks.read(f, 4 * RATE))

    def test_spectral_components_record_an_unheard_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            t = np.arange(RATE) / RATE
            signal = np.sin(2 * np.pi * 440 * t) * 0.2
            signal[(t >= 0.34) & (t < 0.52)] = 0
            path = Path(directory) / "sound.wav"
            sf.write(path, signal, RATE)
            rows = SpectralTracks().read(path, 0)
            band = next(row for row in rows if row["hz"][0] < 450 and row["hz"][1] > 430)
            self.assertGreaterEqual(len(band["detected_spans"]), 2)
            self.assertFalse(any(a <= 0.43 <= b for a, b in band["detected_spans"]))

    def test_texture_survives_a_pause_and_preserves_a_gap(self):
        gray = np.zeros((96, 128, 3), np.uint8)
        rng = np.random.default_rng(924)
        tile = rng.integers(40, 255, (20, 20, 3), dtype=np.uint8)

        def exposure(pts, x):
            f = gray.copy()
            if x:
                f[35:55, x : x + 20] = tile
            return (pts, None, f.tobytes())

        tr = PictureTracks()
        held = exposure(0, 32)
        first = tr.read([held], (128, 96), 0, 0.1)
        self.assertTrue(first)
        still = tr.read([held], (128, 96), 0.1, 0.2)
        self.assertEqual(still[0]["track"], first[0]["track"])
        self.assertEqual(still[0]["visible_spans"], [[0.1, 0.2]])
        rows = tr.read([held, exposure(250000, 0), exposure(500000, 36)], (128, 96), 0.2, 0.6)
        recovered = next(e for e in rows if e["track"] == first[0]["track"])
        self.assertEqual(recovered["visible_spans"], [[0.2, 0.25], [0.5, 0.6]])
        self.assertGreater(recovered["reacquisitions"], 0)

    def test_waveform_vs_absent_or_unknown(self):
        rng = np.random.default_rng(140)
        ref = rng.normal(0, 0.1, RATE // 4)
        bed = rng.normal(0, 0.02, RATE * 2)
        self.assertFalse(any(x["strong_waveform_match"] for x in recognize(ref, bed)["matches"]))
        bed[4623 : 4623 + len(ref)] -= ref
        result = recognize(ref, bed, 3)["matches"][0]
        self.assertAlmostEqual(result["start_s"], 3 + 4623 / RATE)
        self.assertTrue(result["strong_waveform_match"])
        self.assertEqual(recognize(ref, np.zeros(RATE))["status"], "no_varying_signal")
        self.assertEqual(recognize(ref[:100], bed)["status"], "too_short")

    def test_picture_sound_controls_and_pulse(self):
        motion = np.zeros((125, 12))
        levels = np.zeros((125, 2))
        hits = [20, 31, 47, 74, 81, 101]
        motion[hits, 7] = 4
        levels[hits, 1] = 0.8
        (hit,) = alignment(motion, levels)
        self.assertEqual(hit["channels"], [2])
        self.assertEqual(hit["box"], [0.75, 1 / 3, 1, 2 / 3])
        self.assertFalse(alignment(motion, np.roll(levels, 9, axis=0)))
        self.assertFalse(alignment(np.repeat(motion[:, 7:8], 12, axis=1), levels))
        self.assertFalse(alignment(motion, np.zeros_like(levels)))
        motion[:, 6] = motion[:, 7]
        self.assertFalse(alignment(motion, levels))
        history = TimingHistory()
        rows = history.pulses([dict(time_s=t, channels=[2]) for t in [0.25, 0.75, 1.25, 1.75, 2.25]], 2.5)
        self.assertEqual(rows[0]["bpm"], 120)
        self.assertFalse(history.pulses([dict(time_s=2.46, channels=[2])], 2.6))

    def test_binding_enters_normal_event_stream_across_split_bins(self):
        rng = np.random.default_rng(33)
        gray = np.zeros((96, 128, 3), np.uint8)
        ticks = [20, 31, 47, 74, 81, 101]
        frames = []
        signal = np.zeros(5 * RATE, np.float32)
        for tick in range(125):
            if tick in ticks:
                gray[32:64, 64:96] = 255 - gray[32:64, 64:96]
                a = tick * RATE // 25
                signal[a : a + 640] = rng.normal(0, 0.1, 640)
            frames.append((tick * 40000, None, gray.tobytes()))
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            timing = TimingHistory()
            for first, end in ((0, 1.023), (1.023, 3.31), (3.31, 5)):
                a, b = round(first * RATE), round(end * RATE)
                sf.write(d / "sound.wav", signal[a:b], RATE)
                view = [f for f in frames if first - 0.04 < f[0] / 1e6 < end]
                rows = timing.binding(view, (128, 96), first, end, d / "sound.wav", a)
            self.assertTrue(rows)
            self.assertEqual(rows[0]["end_s"], 5)
            self.assertEqual(rows[0]["box"], [0.5, 1 / 3, 0.75, 2 / 3])

    def test_publication_names_words_only_when_present(self):
        packet = {
            "audio": {
                "sound_events": {
                    "model": "example",
                    "candidates": [dict(sound="Music", start_s=0.1, end_s=0.5, channels=[1])],
                }
            },
            "speech": {
                "windows": [{"channels": [{"channels": [2], "words": [{"w": "go", "t": 0.25, "e": 0.4}]}]}]
            },
        }
        rows = EventStream.publish([], packet, 3)
        self.assertEqual(
            [(r["kind"], r["id"]) for r in rows], [("sound_candidate", "3.1"), ("supported_word", "3.2")]
        )
        self.assertEqual(EventStream.publish([], {}, 1), [])

    def test_memory_as_of_turn_and_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)

            def packet(i, items):
                f = p / f"{i}.json"
                data = dict(start_s=i - 1, end_s=i, pages=[])
                if items is not None:
                    data["observations"] = items
                f.write_text(json.dumps(data))
                return f

            event = dict(id="1.1", kind="acoustic_attack", start_s=0.1, end_s=0.2, track="a")
            paths = [packet(1, [event])]
            mem = EventMemory(paths)
            mem.remember(1, "a click", "unidentified", "1.1", confidence=0.6)
            mem.remember(1, "might be a latch", "prediction", "1.1")
            paths.append(packet(2, None))
            with self.assertRaises(ValueError):
                mem.remember(1, "future", "identified", "1.1")
            row = mem.remember(
                2, "door moves", "detected", interval=[1, 2], related_id="1.1", relation="supports_prediction"
            )
            self.assertEqual(row["reader_link"]["event_id"], "1.1")
            rev = mem.remember(2, "still uncertain", "uncertain", "1.1", revises=1)
            self.assertEqual(rev["phase"], "directed_or_replayed")
            self.assertEqual(mem.recall(1)["revision"], 2)
            self.assertEqual(mem.recall(2, revision=1)["reader_reports"][0]["claim"], "a click")
            self.assertEqual(mem.recall(2, limit=1)["next_after_revision"], 1)
            self.assertEqual(mem.recall(2, limit=1, after_revision=1)["reader_reports"][0]["revision"], 2)
            self.assertEqual(mem.recall(2)["unindexed_turns_on_this_page"], [2])
            for kwargs in (
                {"event_id": "3.1"},
                {"interval": [0.2, 3]},
                {"interval": [0.2, 1], "confidence": float("nan")},
                {"event_id": "1.1", "revises": 1},
            ):
                with self.assertRaises(ValueError):
                    mem.remember(2, "invalid", "detected", **kwargs)

    def test_voice_assignment_abstains_on_ambiguous_overlap(self):
        history = VoiceHistory(None)
        a, b = np.eye(2)
        self.assertEqual(history.match(a)["track"], "voice-1")
        self.assertEqual(history.match(b)["track"], "voice-2")
        self.assertEqual(history.match((a + b) / np.sqrt(2))["status"], "unresolved")
        self.assertEqual(history.match(a)["status"], "similar_voice")


class ReaderDelivery(unittest.TestCase):
    def test_original_estimate_native_transport_and_reader_history(self):
        import base64
        import io
        from reel.mcp_server import Viewing

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            t = np.arange(3 * RATE) / RATE
            bed = 0.12 * np.sin(2 * np.pi * 330 * t)
            sound = 0.09 * np.sin(2 * np.pi * 1800 * t) * (t >= 1)
            wav = d / "sound.wav"
            sf.write(wav, np.column_stack([bed, bed + sound]), RATE, subtype="FLOAT")
            p = d / "view.json"
            event = dict(id="1.1", kind="acoustic_attack", start_s=1.4, end_s=1.65, channels=[2])
            p.write_text(
                json.dumps(
                    dict(
                        schema="reel.play.v1",
                        start_s=0,
                        end_s=3,
                        pages=[],
                        observations=[event],
                        audio=dict(
                            wav=wav.name, channels=2, rate_hz=RATE, first_sample_on_film=0, sample_count=len(t)
                        ),
                    )
                )
            )
            v = Viewing(iter([]), None, None, None)
            v.paths.append(p)
            initial = json.loads(v.remember_event(1, "there was a sound", "unidentified", "1.1")[0].text)
            self.assertEqual(initial["phase"], "first_pass")
            blocks = v.deliver(v.hear_change, 1, [0, 1], [1, 2], 2, True, False)
            self.assertEqual([b.type for b in blocks], ["text"] + ["image"] * 3 + ["audio"] * 3)
            original = sf.read(io.BytesIO(base64.b64decode(blocks[-2].data)))[0]
            altered = sf.read(io.BytesIO(base64.b64decode(blocks[-1].data)))[0]
            np.testing.assert_allclose(original, (bed + sound)[RATE : 2 * RATE], atol=1e-7)
            self.assertLess(
                np.mean((altered - sound[RATE : 2 * RATE]) ** 2),
                np.mean((original - sound[RATE : 2 * RATE]) ** 2) * 0.15,
            )
            row = json.loads(v.remember_event(1, "two tones", "identified", "1.1", None, None, None, 1)[0].text)
            self.assertEqual(row["phase"], "directed_or_replayed")
            self.assertTrue(row["replay_or_directed_access"])
            with self.assertRaises(ValueError):
                v.hear_change(1, [0, 1], [1, 3.1])
            with self.assertRaises(ValueError):
                v.hear_change(1, [0, 1.8], [1, 2])
            match = json.loads(v.find_sound(1, [1, 1.25], [1.5, 2], 2, 2)[0].text)
            self.assertTrue(match["matches"][0]["strong_waveform_match"])


class StudyScoring(unittest.TestCase):
    def test_independent_people_exposure_matching_and_abstentions(self):
        from examples.listener_study import connect, record, score

        db = connect(":memory:")
        self.addCleanup(db.close)
        common = dict(
            panel="p", trial="a", phase="first_pass", tracks="sound", evidence="same", question="audible?"
        )
        for reader, answer in (("a", "yes"), ("b", "yes"), ("c", "no")):
            record(db, dict(common, reader=reader, kind="human", answer=answer))
        record(db, dict(common, reader="a", kind="human", answer="no", trial="b", evidence="changed"))
        record(db, dict(common, reader="g", kind="model", answer="unknown", prob_yes=0.6))
        record(db, dict(common, reader="g", kind="model", answer="yes", trial="b"))
        (data,) = score(db)
        self.assertEqual(data["items"], 1)
        self.assertEqual(data["unanswered"], 1)
        self.assertAlmostEqual(data["human_pair_agreement"], 1 / 3)
        self.assertAlmostEqual(data["individual_vote_brier"], (0.4**2 * 2 + 0.6**2) / 3)

    def test_unobserved_prediction_cannot_be_marked_successful_after_the_fact(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "view.json"
            p.write_text(json.dumps(dict(start_s=0, end_s=1, pages=[], observations=[])))
            mem = EventMemory([p])
            ref = mem.remember(1, "expect motion", "prediction", interval=[0, 0.1])
            with self.assertRaises(ValueError):
                mem.remember(
                    1,
                    "motion",
                    "detected",
                    interval=[0.6, 0.9],
                    related_id=ref["event"]["id"],
                    relation="supports_prediction",
                )


class JournalTests(unittest.TestCase):
    def test_archived_readings_keep_cutoff_revision_and_viewer(self):
        from reel.event_journal import EventJournal, read

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            file = d / "notes.sqlite"
            one, two = EventJournal(file), EventJournal(file)
            paths = []
            memory = EventMemory(paths, one)
            for turn in (1, 2):
                path = d / f"{turn}.json"
                path.write_text(json.dumps(dict(start_s=turn - 1, end_s=turn, pages=[], observations=[])))
                paths.append(path)
                row = memory.remember(turn, "sound", "detected", interval=[turn - 0.5, turn])
                self.assertEqual(row["journal_session"], one.session)
            other = EventMemory(paths, two)
            other.remember(2, "uncertain", "uncertain", interval=[0.25, 0.5])
            self.assertCountEqual(read(file)["sessions"], [one.session, two.session])
            self.assertEqual([r["revision"] for r in read(file, one.session, through=1)["reports"]], [1])
            self.assertEqual(read(file, one.session, limit=1)["next_after"], 1)
            rows = read(file, one.session)["reports"]
            self.assertNotEqual(rows[0]["published_evidence_sha256"], rows[1]["published_evidence_sha256"])
            self.assertEqual(read(file, two.session)["reports"][0]["revision"], 1)


class BindingEvidence(unittest.TestCase):
    def test_timing_cue_stays_searchable_and_first_pass_stays_neutral(self):
        from reel.event_memory import attention

        cue = dict(kind="motion_sound_timing", box=[0, 0, 0.25, 1 / 3], start_s=0, end_s=3, id="1.1")
        event = dict(kind="acoustic_attack", at_s=1, start_s=0.9, end_s=1.1, id="1.2", channels=[1])
        self.assertEqual([row["id"] for row in attention([cue, event])], ["1.2"])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "view.json"
            p.write_text(json.dumps(dict(start_s=0, end_s=3, pages=[], observations=[cue, event])))
            self.assertEqual(EventMemory([p]).event(1, "1.1")["box"], cue["box"])

    def test_binding_replays_only_available_channels_and_pictures(self):
        from unittest.mock import patch
        from reel.mcp_server import Viewing, text_block

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "view.json"
            event = dict(kind="acoustic_attack", start_s=2.8, end_s=3, id="1.1")
            v = Viewing(iter([]), None, None, None)
            v.paths.append(p)
            for pages in ([], [dict(file="f", pts_us=[2000000])]):
                p.write_text(json.dumps(dict(start_s=0, end_s=3, pages=pages, observations=[event])))
                with (
                    patch.object(v, "listen", return_value=[text_block({"status": "available"})]) as listen,
                    patch.object(v, "look", return_value=[text_block({"pts_us": 2000000})]) as look,
                ):
                    report = json.loads(v.inspect_event(1, "1.1", "binding")[0].text)
                    listen.assert_called_once_with(1, None, 0.0, 3, False)
                    self.assertEqual(look.call_count, 1 if pages else 0)
                    if pages:
                        self.assertLess(look.call_args.args[-1], 3)
                    self.assertEqual(report["picture"] is not None, bool(pages))


class DelayedRecall(unittest.TestCase):
    def test_recall_uses_the_first_exposure_without_replaying(self):
        import hashlib
        from unittest.mock import patch
        from examples.listener_study import collect, connect

        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            clip = d / "c.wav"
            clip.write_bytes(b"trial")
            trial = dict(
                id="c",
                media=clip.name,
                sha256=hashlib.sha256(b"trial").hexdigest(),
                tracks="sound",
                question="a sound?",
                choices=["yes", "no"],
            )
            study = d / "study.json"
            study.write_text(
                json.dumps(dict(panel="test", trials=[dict(trial, id="r", phase="recall", from_trial="c"), trial]))
            )
            db = connect(":memory:")
            self.addCleanup(db.close)
            with (
                patch("builtins.input", side_effect=["", "yes", "click", "yes", "short click"]),
                patch("builtins.print"),
                patch("subprocess.run") as play,
            ):
                collect(study, db, "one", 7)
                play.assert_called_once()
            rows = [json.loads(v[0]) for v in db.execute("SELECT payload FROM reports ORDER BY rowid")]
            self.assertEqual([r["phase"] for r in rows], ["first_pass", "recall"])
            self.assertEqual(rows[0]["exposure_ended_unix"], rows[1]["exposure_ended_unix"])
            self.assertIsNone(rows[1]["response_seconds_after_playback"])
