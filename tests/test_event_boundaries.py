import asyncio
from importlib.util import find_spec
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from examples.check_event_boundaries import agreement, score


class BoundaryTests(unittest.TestCase):
    def test_match_each_viewer_separately_and_count_a_boundary_once(self):
        self.assertAlmostEqual(agreement([1, 1.1], [1.05], .5), 2 / 3)
        self.assertEqual(agreement([1, 1.6], [1.49, 1.8], .5), 1)
        film = {"id": "clip", "interval": [.3, 8], "human": [[.05, 1], [1.1], [1.2, 8]],
                "readers": {"observer": [1, 4, 8]}}
        measured = score([film])
        row = measured["by_film"][0]
        relative = measured["summary"]["readers"]["observer"]["fraction_of_human_f1"]
        self.assertAlmostEqual(relative["mean"], 2 / 3)
        self.assertAlmostEqual(relative["film_bootstrap_95"][0], 2 / 3)
        self.assertEqual(row["human_f1"], 1)
        reader = row["readers"]["observer"]
        self.assertAlmostEqual(reader["f1"], 2 / 3)
        self.assertEqual(reader["marks_near_majority"], 1)
        self.assertLess(reader["rotated_f1"], reader["f1"])
        film["human"] = [[1], [4]]
        self.assertEqual(score([film])["summary"]["readers"]["observer"]["fraction_of_human_f1"],
                         {"mean": None, "film_bootstrap_95": None})

    @unittest.skipUnless(find_spec("mcp"), "install the MCP extra for the live viewing test")
    def test_commit_before_viewing_and_retry_final_receipt(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def run(folder):
            source = folder / "film.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x48:r=4:d=2",
                            "-c:v", "ffv1", str(source)], check=True)
            root = Path(__file__).resolve().parents[1]
            params = StdioServerParameters(command=sys.executable, args=[
                str(root / "examples/watch_event_boundaries.py"), str(source), "--until", "1", "--seconds", ".5"],
                env={**os.environ, "PYTHONPATH": str(root)})
            async with stdio_client(params) as pipes, ClientSession(*pipes) as session:
                await session.initialize()

                async def view(turn, changes):
                    return await session.call_tool("advance", {"turn": turn, "changes": changes})

                self.assertTrue((await view(1, [.1])).isError)
                first = await view(1, [])
                self.assertEqual(json.loads(first.content[0].text)["end_s"], .5)
                self.assertEqual((await view(1, [])).content, first.content)
                self.assertTrue((await view(2, [.5])).isError)
                second = await view(2, [.25])
                self.assertEqual(json.loads(second.content[0].text)["end_s"], 1)
                self.assertTrue((await view(2, [])).isError)
                self.assertTrue((await view(3, [.25])).isError)
                end = await view(3, [.75])
                receipt = json.loads(end.content[0].text)
                self.assertEqual(receipt["changes_s"], [.25, .75])
                self.assertEqual([w["images"] for w in receipt["windows"]], [len(q.content)-1 for q in (first, second)])
                self.assertEqual((await view(3, [.75])).content, end.content)
                self.assertTrue((await view(4, [])).isError)

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(run(Path(directory)))
