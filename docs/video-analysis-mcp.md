# Video and audio analysis with Codex and an MCP server

Reel lets an AI agent inspect a local video in chronological order. Its Model Context Protocol (MCP) server provides timestamped pictures and audio evidence together. The agent can advance through a film, look closer at a frame and replay an interval it has already seen.

This guide uses Codex CLI on an Apple Silicon Mac. It also explains what an image-and-text MCP client receives when it cannot accept audio directly.

## Install Reel on macOS

Install FFmpeg (`brew install ffmpeg`) and Python 3.11 or later, then install Reel from PyPI:

```bash
pipx install reel-mcp
```

To work from a clone instead, run `python -m pip install -e .` in a virtual environment.

Place a video in the working directory as `film.mp4`, or substitute its actual path. To check playback without an MCP client, run:

```bash
reel play film.mp4 --out reels/first-view --until 8.5
```

Type `next` to advance or `quit` to finish. Choose a fresh output directory for a new viewing.

## Give Codex a video to watch

With Codex CLI signed in, register the local MCP server:

```bash
codex mcp add reel -- reel-mcp "$PWD/film.mp4" --until 8.5 --image-client
```

Start a new Codex chat and ask:

> Watch the film with Reel. What do you see, and what does the audio evidence suggest? Give timestamps for the events you describe.

Reel exposes only the first 8.5 seconds in this example. Choose a later cutoff, or omit the cutoff, if the task requires more of the film. For another stdio MCP client, use the absolute path to the installed `reel` executable and the same `mcp` arguments.

## Inspect a scene and replay it

| MCP tool | Example | What it provides |
|---|---|---|
| `view` | `view(turn=1)` | The next interval of playback |
| `look` | `look(turn=1, at_s=0.5, box=[0.25,0.25,0.75,0.75])` | The original pixels of a selected region in an already released frame, if that time is inside the turn |
| `observe` | `observe(start_s=4, end_s=6)` | Frame sheets and the corresponding audio from a previously released interval |

Advance the film far enough to include the requested interval before using replay tools. A replay cannot reveal pictures or sounds beyond the released interval. See the [detailed reading guide](READING.md) for the other tools, playback limits and interpretation of the results.

## Check video quality: frozen frames, repeated sounds and subtitles

In the generated Reel demo, the picture holds between 38 and 41 seconds while sound continues. The picture-change and audio traces share a time axis.

After the agent has reached 41 seconds, ask:

> Find any interval where the picture stops while the sound continues. Give the start and end times, then replay the interval to show the evidence.

The demo can be inspected in the [project README](https://github.com/Dradradre/Reel#try-it-on-an-edit). Its [generation script](https://github.com/Dradradre/Reel/blob/main/examples/make_demo.py) records the planted events; keep that script away from the model if you want a blind test.

Other useful questions:

- Does a sound recur at a different time? Compare the two recorded excerpts.
- When does an on-screen subtitle appear relative to the spoken words? Compare the timestamps and available speech readings.
- What changes between two shots? Review the transition and its accompanying sound.

Reel supplies the evidence; the agent makes the judgment. A still shot may be intentional, repeated sound need not be a fault, and speech or sound recognition can miss events.

## What does the model actually receive?

A client with image and text input receives frames, acoustic measurements and, when the optional local readers are installed, descriptions of the recording and speech. Audio-capable clients can also receive the original channels as a WAV. An optional local waveform reader requires about 39 GB of model weights. See [playback options](https://github.com/Dradradre/Reel#playback-options) for setup and client differences.

In the [door experiment](door-trial.md), two clips share the same 204 frames but have different recordings. The local GPT client mentioned a brief rattle in one clip and did not identify the faint sound in the other. This tests whether audio evidence changes a model's answer when the pictures stay the same.

For measured results, methods and misses, see [Reel's benchmarks](https://github.com/Dradradre/Reel#does-it-work) and [event-path experiments](event-paths.md).

[Reel on GitHub](https://github.com/Dradradre/Reel) · [MIT license](https://github.com/Dradradre/Reel/blob/main/LICENSE)
