# Reel

Reel gives a model access to the pictures and sounds a person could have perceived by the current moment in a film.
Playback exposes successive frames and their simultaneous recorded channels through MCP. A reader can look closer or
listen again within the released interval. The 30-second pages offer another way to follow the film. Editing faults are
controlled examples of things a viewer might notice.

When assessing a change to perception, compare the model with people who experienced the same pictures and sound when
those observations exist. Record what people disagreed on, what the model missed, and what it claimed without evidence.
A controlled substitution such as the door trial can isolate one cue. Use unfamiliar scenes to judge whether a change
helps beyond that cue.

## Layout

- `reel/cli.py`: the `reel` command (`watch`, `zoom`, `doctor`, `setup`).
- `reel/perceive.py`: the core. Reads every frame and every 10 ms of sound, runs the helpers, draws the pages
  (`render_page`), writes `stream.md` and `meta.json`. `build()` is the entry point, `load()` reads a finished reel
  back, `zoom()` redraws a short stretch at higher resolution.
- Helpers, each runnable on its own and called as a subprocess:
  `separate.py` (Demucs stems), `crowd.py` (Mel-Band Roformer crowd stem), `asr.py` (Whisper words and chants),
  `gaze.py` (UNISAL saliency), `sync.py` (Synchformer picture-sound offset).
- `reel/config.py`: model and cache locations (`~/.cache/reel`, `REEL_HOME`) and one Python per helper
  (`REEL_SEP_PYTHON`, `REEL_CROWD_PYTHON`, `REEL_ASR_PYTHON`, `REEL_GAZE_PYTHON`, `REEL_SYNC_PYTHON`).
- `reel/common.py`: ffmpeg decode, fonts, drawing helpers.
- `docs/READING.md`: how a reader goes through a reel, lane by lane. It is part of the product, not a side note.
- `examples/make_demo.py`: builds a 70-second demo with six planted faults from nothing copyrighted.
- `assets/`: the README images and the door demo.
- `docs/results.md`, `docs/playback-options.md`, `docs/credits.md` (and their `.ko.md` twins): the full measurements,
  commands and credits. The READMEs carry a short version of each and link here.
- `server.json`: the MCP Registry entry (`io.github.Dradradre/reel`), published with each release.

## Setup and run

macOS on Apple Silicon only (Apple Vision OCR, MLX Whisper, macOS fonts in `common.py`). Needs `ffmpeg`.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
reel setup --sync      # gaze weights + 1.1 GB sync model into ~/.cache/reel
reel doctor            # which senses work on this machine

reel watch film.mp4 --out reels/film --lang ko
reel zoom reels/film --t0 72 --t1 80
```

Run `python -m unittest discover -s tests` for the regression suite. After a change:

1. `python -m compileall -q reel` and `reel doctor`.
2. Build the demo and its reel, then read every page in order and confirm all six planted faults still show:
   `python examples/make_demo.py examples/out/demo.mp4 --music TRACK.wav` then
   `reel watch examples/out/demo.mp4 --out reels/demo --lang en`.
3. If you touched a lane, open a page and look at it. A script that runs is not a page that reads.

A ten-minute film takes about ten minutes on an M4 Pro with sync on. Run long builds in the background.

## Rules for changing Reel

- Gapless, never sampled. Every frame and every 10 ms of sound must leave a mark. Values are max-pooled per pixel
  column (`pooled`), so a one-frame flash or a one-second repeat survives. Do not replace this with frame sampling.
- One time axis. Every lane uses the same x for the same second on a page. A new lane must too.
- Pages show, they do not judge. Labels describe what happened ("hard cut to black", "same sound, suddenly +9 dB"),
  never "error", "bad" or a score. The reader decides.
- Pages run strictly forward and are read in order. Do not add summaries or answer keys that a reader would see before
  the pages. `meta.json` and `stream.md` hold extra data; keep anything that gives away a verdict out of them.
- When a lane, a label or a colour changes, update `docs/READING.md` and the lane table in both READMEs in the same
  change.
- Each helper stays a separate process with its own Python (see `config.py`), so a heavy model can live in its own
  environment. Keep the CLI usable when an optional sense is missing: skip that lane and say so, do not crash.
  `reel doctor` lists every sense; add new ones there.
- New model or dataset: check its licence first and add it to `docs/credits.md` and `docs/credits.ko.md`. Weights download into
  `~/.cache/reel` through `reel setup`; never commit weights.
- Do not commit reels, demo output, stems, videos or caches (`reels/`, `examples/out/`, `*.mp4` are ignored). Use only
  media you are allowed to share in examples.

## Results in the README

The full results live in `docs/results.md` and `docs/results.ko.md`; the "Does it work?" table in each README is a
summary of them and must use the same numbers. The tables are measured with blind readers: a fresh model gets only `docs/READING.md` and the reel,
never the answer key. Keep that protocol for any new numbers:

- Plant faults with a script, keep the key out of the reader's reach, include one untouched video per round.
- Round 5 was run with the code frozen. Do not edit its numbers. New results go in a new round, with what changed
  before it.
- Report misses and false alarms as plainly as hits.

## Writing

- English README and docs: plain sentences, concrete examples, no marketing words.
- `README.ko.md` and any Korean text: natural Korean, no AI-style patterns (middle dots, em dashes, "A가 아니라 B"
  frames, sentence-ending headings). If the korean-anti-slop-writing skill is available, lint with it.
- Keep `README.md` and `README.ko.md` in step: same sections, same numbers. The same goes for each `docs/*.md` page
  and its `.ko.md` twin.

## Releasing

The PyPI package is `reel-mcp` (the name `reel` belongs to another project); it installs the `reel` and `reel-mcp`
commands, and `reel-mcp FILM` is `reel mcp FILM`. To release, bump `version` in `pyproject.toml` and both versions in
`server.json`, then push a `vX.Y.Z` tag. `.github/workflows/release.yml` publishes to PyPI through Trusted Publishing
and then to the MCP Registry through GitHub OIDC; no token is stored. `README.md` must keep the
`mcp-name: io.github.Dradradre/reel` comment, which the registry reads from PyPI to confirm ownership.
