"""Ask a fresh local Codex session to watch a film through Reel and give its first description."""
import argparse
import asyncio
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def watch(source, until=None, joint=False, model=None, recall=False, tracks="both", max_frames=None, native_audio=False):
    local = Path(sys.executable).with_name("reel")
    reel = str(local) if local.is_file() else shutil.which("reel")
    if not reel:
        raise ValueError("install Reel with its mcp extra in this environment")
    with tempfile.TemporaryDirectory(prefix="reel-impression-") as tmp:
        root = Path(tmp)
        work = root / "viewer"
        work.mkdir()
        # Only the presenter receives the filename. The viewer has no shell or reference captions.
        film = root / ("film" + source.suffix)
        film.symlink_to(source.resolve(strict=True))
        args = ["mcp", str(film), "--tracks", tracks]
        if not native_audio:
            args.append("--image-client")
        if joint:
            args.append("--joint")
        if until is not None:
            args.extend(["--until", str(until)])
        presenter = [str(Path(__file__).resolve()), "--present", json.dumps(max_frames), reel, *args]
        options = {"command": sys.executable, "args": presenter, "enabled_tools": ["view"], "tool_timeout_sec": 300,
                   "default_tools_approval_mode": "writes"}
        return read_with_codex(work, options, model, recall, max_frames=max_frames)


def read_with_codex(work, options, model, recall, *, max_frames=None):
    answer = work.parent / "answer.txt"
    cmd = ["codex", "exec", "--ephemeral", "--json", "--ignore-user-config", "--skip-git-repo-check",
           "--disable", "plugins", "--disable", "shell_tool", "-s", "read-only", "-C", str(work)]
    if model:
        cmd.extend(["-m", model])
    for key, value in options.items():
        cmd.extend(["-c", "mcp_servers.reel." + key + "=" + json.dumps(value)])
    if recall:
        recorder = {"command": sys.executable, "args": [str(Path(__file__).resolve()), "--receive-caption"],
                    "enabled_tools": ["submit"], "default_tools_approval_mode": "writes"}
        for key, value in recorder.items():
            cmd.extend(["-c", "mcp_servers.response." + key + "=" + json.dumps(value)])
    cmd.extend(["-o", str(answer), "-"])
    prompt = ("Watch the clip through end_of_view, calling view with consecutive turn numbers "
              "starting at 1. Describe the clip in one natural English "
              "sentence of at least five words for someone who has not seen it. ")
    prompt += "Submit your sentence with submit and follow its receipt." if recall else "Give just your sentence."
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    with tempfile.TemporaryFile(mode="w+") as trace:
        subprocess.run(cmd, cwd=work, env=env, input=prompt, text=True, stdout=trace, check=True)
        trace.seek(0)
        turns = []
        evidence = []
        finished = False
        submitted = None
        for line in trace:
            item = json.loads(line).get("item", {})
            if item.get("type") != "mcp_tool_call" or item.get("status") != "completed":
                continue
            if item.get("error") or (item.get("result") or {}).get("isError"):
                raise ValueError("a viewing tool failed")
            if recall and item.get("tool") == "submit":
                if not finished or submitted is not None:
                    raise ValueError("submit exactly one first impression after the film ends")
                submitted = item["arguments"]["caption"].strip()
                continue
            if item.get("tool") != "view" or finished:
                raise ValueError("the reader made an unsuccessful or unexpected tool call")
            turn = item["arguments"]["turn"]
            report = json.loads(item["result"]["content"][0]["text"])
            # Some Codex CLI media results wrap the typed blocks in another JSON object.
            if "content" in report:
                report = json.loads(report["content"][0]["text"])
            if report.get("status") == "end_of_view":
                if report.get("last_turn") != len(turns) or turn != len(turns) + 1:
                    raise ValueError("the end marker did not match the released turns")
                finished = True
            else:
                if report.get("status") != "view" or report.get("turn") != turn or turn != len(turns) + 1:
                    raise ValueError("the reader did not watch the turns in order")
                if max_frames is not None:
                    if report["frame_pages"]:
                        images = report["images_in_order"]
                        times = images[0].get("successive_picture_pts_us", []) if images and isinstance(images[0], dict) else []
                        if not 1 <= len(times) <= max_frames:
                            raise ValueError("the frame sequence was missing from the viewing")
                turns.append(turn)
                if recall:
                    audio = report.get("audio") or {}
                    evidence.append({"turn": turn, "signal": audio.get("signal", "no_audio"),
                                     "wav_attached": audio.get("wav_attached", False),
                                     **{key: report[key].get("status") for key in
                                        ("sound_descriptions", "joint", "speech") if key in report}})
                    if recording := report.get("joint", {}).get("recording"):
                        evidence[-1]["recording"] = recording["status"]
    if not finished or not turns:
        raise ValueError("the reader answered before viewing the whole clip")
    caption = answer.read_text().strip()
    if not caption:
        raise ValueError("the reader returned no caption")
    if recall:
        if not submitted:
            raise ValueError("no first impression was submitted")
        response = json.loads(caption)
        if not isinstance(response, dict) or not all(isinstance(response.get(k), str) and response[k].strip()
                                                      for k in ("seen", "heard")):
            raise ValueError("the reader did not answer both recall questions")
        return {"caption": submitted, "turns": len(turns), "seen": response["seen"], "heard": response["heard"],
                "evidence": evidence}
    return {"caption": caption, "turns": len(turns)}


async def present(command, args, max_frames):
    """Offer advancement only, so the observer cannot accidentally change the experimental inputs."""
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client
    from mcp.server.fastmcp import FastMCP
    from reel.mcp_server import RECORDING_GUIDE

    params = StdioServerParameters(command=command, args=args, env=os.environ.copy())
    async with stdio_client(params) as (read, write), ClientSession(read, write) as reel:
        await reel.initialize()
        server = FastMCP("reel", instructions=RECORDING_GUIDE)
        fixed = {"continuous_seconds": 8, "sound_estimates": True}
        if max_frames is not None:
            fixed["max_frames"] = max_frames

        @server.tool(structured_output=False, annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False))
        async def view(turn: int):
            """Watch the next turn of the recording, starting with turn 1."""
            result = await reel.call_tool("view", {"turn": turn, **fixed}, read_timeout_seconds=timedelta(seconds=300))
            if result.isError:
                raise ValueError(result.content[0].text)
            return result.content

        await server.run_stdio_async()


def receive_caption():
    from mcp import types
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("response")

    @server.tool(annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    def submit(caption: str) -> str:
        """Submit your first description of the clip."""
        return ('Your first description is recorded. Without using any more tools, answer from memory. '
                'What do you remember seeing? What do you remember hearing? Describe each in one sentence. '
                'If you could not tell, say so. Return only a JSON object with string fields "seen" and "heard".')

    server.run(transport="stdio")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--present"]:
        asyncio.run(present(sys.argv[3], sys.argv[4:], json.loads(sys.argv[2])))
        sys.exit(0)
    if sys.argv[1:] == ["--receive-caption"]:
        receive_caption()
        sys.exit(0)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("film", type=Path)
    parser.add_argument("--until", type=float)
    parser.add_argument("--tracks", choices=("both", "picture", "sound"), default="both")
    parser.add_argument("--max-frames", type=int, choices=range(1, 13), metavar="1..12")
    parser.add_argument("--native-audio", action="store_true", help="also attach each turn’s original WAV")
    parser.add_argument("--joint", action="store_true")
    parser.add_argument("--recall", action="store_true", help="ask about picture and sound after the first answer")
    parser.add_argument("--model", help="model available in your signed-in Codex CLI")
    args = parser.parse_args()
    if args.until is not None and not (math.isfinite(args.until) and args.until > 0):
        parser.error("--until must be a finite positive film time")
    if not args.film.is_file():
        parser.error("supply an existing film")
    print(json.dumps(watch(args.film, args.until, args.joint, args.model, args.recall, args.tracks,
                           args.max_frames, args.native_audio), ensure_ascii=False))
