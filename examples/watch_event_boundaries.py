"""Serve a film to an MCP reader that marks changes before seeing the next interval.

Run with the Python from an installation of reel[mcp]. Configure this script as
the reader's only MCP server. Call advance(1, []) first. Subsequent calls mark
the interval just seen. The end receipt includes every committed mark.
"""
import argparse
import asyncio
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP


async def serve(source, until, seconds, tracks, motion_point=None):
    args = ["-m", "reel.cli", "mcp", str(source.resolve(strict=True)), "--image-client", "--tracks", tracks]
    if until is not None:
        args.extend(["--until", str(until)])
    params = StdioServerParameters(command=sys.executable, args=args, env=os.environ.copy())
    async with stdio_client(params) as pipes, ClientSession(*pipes) as player:
        await player.initialize()
        server = FastMCP("screening")
        lock = asyncio.Lock()
        last_turn = 0
        interval = None
        marks = []
        windows = []
        receipt = None
        last_input = None
        finished = False

        @server.tool(structured_output=False, annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False))
        async def advance(turn: int, changes: list[float]):
            """Commit the film times of changes seen in the preceding interval, then watch the next one.
            Start at turn 1 with []. Use [] again whenever no change was apparent. Times are seconds from
            the start of the film. Once a new interval is released, marks on earlier intervals are final.
            """
            nonlocal last_turn, interval, receipt, last_input, finished
            async with lock:
                if turn == last_turn and changes == last_input:
                    return receipt
                if finished or turn != last_turn + 1:
                    raise ValueError("request the next consecutive turn")
                if changes and (interval is None or any(not interval[0] <= t < interval[1] for t in changes)):
                    raise ValueError("mark only the interval just viewed, including its start and excluding its end")
                if changes != sorted(set(changes)):
                    raise ValueError("list distinct times in order")
                options = {"turn": turn, "continuous_seconds": seconds, "native_audio": False}
                if motion_point is not None:
                    options["motion_point"] = motion_point
                result = await player.call_tool("view", options,
                                                read_timeout_seconds=timedelta(seconds=180))
                if result.isError:
                    raise ValueError(result.content[0].text)
                report = json.loads(result.content[0].text)
                if report["status"] == "end_of_view" and report["last_turn"] == last_turn:
                    finished = True
                    receipt = [types.TextContent(type="text", text=json.dumps(
                        {"status": "end_of_view", "last_turn": last_turn, "changes_s": marks + changes, "windows": windows}))]
                elif (report["status"] == "view" and report["turn"] == turn
                      and report["start_s"] == (interval[1] if interval else 0) and report["end_s"] > report["start_s"]):
                    images = sum(block.type == "image" for block in result.content)
                    if images != len(report["images_in_order"]) or any(block.type == "audio" for block in result.content):
                        raise ValueError("the media blocks differ from the player’s description")
                    interval = (report["start_s"], report["end_s"])
                    windows.append({"start_s": interval[0], "end_s": interval[1], "images": images})
                    receipt = result.content
                else:
                    raise ValueError("the player returned a discontinuous interval")
                marks.extend(changes)
                last_turn, last_input = turn, changes
                return receipt

        await server.run_stdio_async()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--until", type=float)
    parser.add_argument("--seconds", type=float, default=2)
    parser.add_argument("--tracks", choices=("both", "picture", "sound"), default="both")
    parser.add_argument("--motion-point", type=float, nargs=2, metavar=("X", "Y"))
    args = parser.parse_args()
    if not 0 < args.seconds <= 8 or (args.until is not None and not (math.isfinite(args.until) and args.until > 0)):
        parser.error("use intervals up to eight seconds and a positive finite cutoff")
    asyncio.run(serve(args.source, args.until, args.seconds, args.tracks, args.motion_point))
