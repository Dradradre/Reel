"""Serve one WAV without a transcript, spectrum, filename or other sound cues.

Configure this script as a stdio MCP server in the client being tested. Give
the server a short WAV with a phrase known to the evaluator. In a fresh session
ask the model to call hear and transcribe the recording. Include a silence
control and inspect the client's tool receipt alongside the model's answer.
"""
import argparse
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from reel.mcp_server import MAX_INLINE_PCM_BYTES, audio_block


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    args = parser.parse_args()
    if args.wav.stat().st_size > MAX_INLINE_PCM_BYTES:
        parser.error("choose a recording within Reel's inline audio size limit")
    block = audio_block(args.wav)
    server = FastMCP("recording")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                                           openWorldHint=False), structured_output=False)
    def hear():
        """Receive the delivered recording as WAV audio."""
        return [block]

    server.run()


if __name__ == "__main__":
    main()
