import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from examples.check_first_impressions import compare, relative_separation
from examples.check_soundtrack_pairs import compare_pairs
from examples.watch_first_impression import logged_view_report, read_with_codex


class CaptionComparisonTests(unittest.TestCase):
    def test_reads_complete_text_before_truncated_logged_media(self):
        report = {"turn": 1, "status": "view", "remark": "braces } and quotes \" in text"}
        first = json.dumps({"type": "text", "text": json.dumps(report)})
        partial = '{"content": [' + first + ', {"type": "image", "data": "abc'
        self.assertEqual(logged_view_report(partial), report)
        self.assertEqual(logged_view_report(json.dumps(report)), report)
        with self.assertRaises(ValueError):
            logged_view_report('{"content": [' + first[:-4])

    def test_human_reference_compares_same_minus_other_on_matched_films(self):
        # Different foreign-film similarities and identical retrieval ranks must not cancel the gap.
        rows = {"a": {"human": [.75, .25, 1], "reader": [.75, .125, 1]},
                "b": {"human": [.5, .25, 1], "reader": [.625, .3125, 1]}}
        result = relative_separation(rows, "reader")
        self.assertEqual(result["fraction_of_human"], 1.25)
        self.assertEqual(result["film_bootstrap_95"], [1.25, 1.25])
        rows["b"]["human"] = [.25, .5, 0]
        self.assertIsNone(relative_separation(rows, "reader")["film_bootstrap_95"])
        rows["a"]["human"] = [.25, .25, 0]
        self.assertIsNone(relative_separation(rows, "reader")["fraction_of_human"])

    def test_sound_effect_controls_for_the_same_picture_and_other_films(self):
        a, b, picture = np.eye(3)
        vectors = {"a": a, "b": b, "pictured": picture}
        films = [{"id": k, "human": [k, "pictured"], "panels": {"sound": [k], "picture": ["pictured"]},
                  "readers": {"muted": "pictured", "with_sound": k, "same": "pictured"}} for k in ("a", "b")]
        change = compare(films, vectors, sound_effect=("muted", "with_sound"))["sound_effect"]
        self.assertEqual(change["mean"], {"human": .5, "reader": 1.})
        self.assertEqual(compare(films, vectors, sound_effect=("muted", "same"))["sound_effect"]["mean"]["reader"], 0)
        films[1]["panels"]["sound"] = ["a"]
        self.assertEqual(compare(films, vectors, sound_effect=("muted", "with_sound"))["sound_effect"]["mean"],
                         {"human": 0., "reader": 0.})

    def test_picture_priors_and_soundtrack_changes_get_distinct_scores(self):
        a, b = np.eye(2)
        def reading(sound, picture):
            return dict(caption=sound, heard=sound, seen=picture)
        follow = [[reading("a", "a"), reading("b", "a")], [reading("a", "b"), reading("b", "b")]]
        prior = [[reading("a", "a")] * 2, [reading("b", "b")] * 2]
        pair = {"id": "p", "sound": [["a"], ["b"]], "picture": [["a"], ["b"]],
                "readers": {"follows": follow, "prior": prior}}
        scores = compare_pairs([pair], {"a": a, "b": b})["summary"]
        ears = scores["follows"]["heard_vs_sound"]
        self.assertEqual((ears["pictures_toward"], ears["original_retrieval"], ears["exchanged_retrieval"]), (2, 1, 1))
        self.assertEqual(scores["follows"]["seen_vs_picture"]["exchanged_retrieval"], 1)
        fixed = scores["prior"]["caption_vs_sound"]
        self.assertEqual((fixed["original_retrieval"], fixed["exchanged_retrieval"], fixed["pictures_tied"]), (1, 0, 2))

    def test_recall_accepts_a_committed_caption_and_rejects_a_replay(self):
        def call(tool, args, report):
            return {"item": {"type": "mcp_tool_call", "status": "completed", "tool": tool, "arguments": args,
                             "result": {"content": [{"type": "text", "text": json.dumps(report)}]}}}

        view = call("view", {"turn": 1}, {"turn": 1, "status": "view"})
        events = [view, call("view", {"turn": 2}, {"last_turn": 1, "status": "end_of_view"}),
                  call("submit", {"caption": "A child rides on a swing."}, "Receipt")]
        with TemporaryDirectory() as tmp:
            work = Path(tmp) / "viewer"
            work.mkdir()
            def run(cmd, **kwargs):
                self.assertNotIn("hearing", kwargs["input"])
                for event in events:
                    kwargs["stdout"].write(json.dumps(event) + "\n")
                Path(cmd[cmd.index("-o") + 1]).write_text('{"seen":"A swing.","heard":"A singing voice."}')
            with patch("examples.watch_first_impression.subprocess.run", side_effect=run):
                result = read_with_codex(work, {}, None, True)
                self.assertEqual((result["turns"], result["caption"], result["heard"]),
                                 (1, "A child rides on a swing.", "A singing voice."))
                events.append(view)
                with self.assertRaises(ValueError):
                    read_with_codex(work, {}, None, True)

    def test_each_human_is_held_out_and_each_film_counts_once(self):
        vectors = {"a": np.array([1., 0]), "b": np.array([0., 1.]), "c": np.array([-1., 0])}
        films = [{"id": "first", "human": ["a", "b"], "readers": {"model": "a"}},
                 {"id": "second", "human": ["c", "c", "c"], "readers": {"model": "c"}}]
        result = compare(films, vectors)
        self.assertEqual(result["human_captions"], 5)
        self.assertEqual(result["summary"]["human"]["same_film_cosine"], .5)
        self.assertEqual(result["summary"]["model"]["same_film_cosine"], .75)
        self.assertEqual(result["by_film"]["first"]["human"][:2], [0., -.5])

    def test_independent_listeners_are_compared_to_all_audiovisual_reports(self):
        x, y = np.eye(2)
        films = [{"id": "x", "human": ["x", "y"], "readers": {"model": "x"}, "panels": {"audio": ["x"]}},
                 {"id": "y", "human": ["y", "y"], "readers": {"model": "x"}, "panels": {"audio": ["y"]}}]
        panel = compare(films, {"x": x, "y": y})["independent_panels"]["audio"]
        self.assertEqual(panel["captions"], 2)
        self.assertEqual(panel["summary"]["human"]["same_film_cosine"], .75)
        self.assertEqual(panel["summary"]["model"]["same_minus_other"], 0.)

    def test_paired_change_counts_films_once_and_keeps_the_reference_panels_distinct(self):
        a, b = np.eye(2)
        films = [{"id": "one", "human": ["a"] * 4, "panels": {"sound": ["b"]},
                  "readers": {"previous": "b", "current": "a"}},
                 {"id": "two", "human": ["b", "b"], "panels": {"sound": ["b"] * 3},
                  "readers": {"previous": "b", "current": "b"}}]
        report = compare(films, {"a": a, "b": b}, compare_readers=("previous", "current"))
        change = report["paired_readers"]
        self.assertEqual(change["primary_panel"], {"mean_change": .5, "bootstrap_95_percent": [0., 1.],
                         "increased": 1, "decreased": 0, "tied": 1, "by_film": {"one": 1., "two": 0.}})
        sound = change["independent_panels"]["sound"]
        self.assertEqual((sound["mean_change"], sound["bootstrap_95_percent"], sound["decreased"]), (-.5, [-1., 0.], 1))
        with self.assertRaises(ValueError):
            compare(films, {"a": a, "b": b}, compare_readers=("previous", "missing"))

    def test_indistinguishable_films_share_top_rank(self):
        films = [{"id": str(i), "human": ["scene", "scene"], "readers": {"model": "scene"}}
                 for i in range(3)]
        stats = compare(films, {"scene": np.array([1., 0])})["summary"]
        for row in stats.values():
            self.assertEqual(row["same_minus_other"], 0.)
            self.assertAlmostEqual(row["top_one_film"], 1/3)
