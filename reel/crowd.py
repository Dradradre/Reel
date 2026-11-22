#!/usr/bin/env python3
"""Pull the crowd/audience out of a mix with the Mel-Roformer crowd model (audio-separator).

    python crowd.py MIX [MIX ...] --out DIR   (one MIX: DIR/crowd.wav, DIR/no_crowd.wav)
    ... --outs DIR1 DIR2 ...   (one output folder per MIX)
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import argparse, pathlib, shutil, sys, tempfile, time
import soundfile as sf

MODEL = "mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt"
HERE = pathlib.Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mix", nargs="+")
    ap.add_argument("--out")
    ap.add_argument("--outs", nargs="*")
    ap.add_argument("--model", default=MODEL)
    a = ap.parse_args()
    outs = a.outs or [a.out] * len(a.mix)
    if len(outs) != len(a.mix) or not outs[0]:
        sys.exit("give --out DIR or one --outs DIR per mix")
    from audio_separator.separator import Separator
    tmp = tempfile.mkdtemp(prefix="crowd_")
    sep = Separator(output_dir=tmp, model_file_dir=str(__import__("config").MODELS), output_format="WAV", log_level=30)
    sep.load_model(model_filename=a.model)
    for mix, out in zip(a.mix, outs):
        out = pathlib.Path(out); out.mkdir(parents=True, exist_ok=True)
        dur = sf.info(mix).duration
        t0 = time.time()
        files = sep.separate(mix, custom_output_names={"crowd": "crowd", "other": "no_crowd"})
        took = time.time() - t0
        for f in files:
            p = pathlib.Path(tmp) / pathlib.Path(f).name
            shutil.move(str(p), str(out / p.name))
        print(f"{out}: {dur:.1f} s in {took:.1f} s ({took / dur * 60:.1f} s per minute) -> {[pathlib.Path(f).name for f in files]}", flush=True)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
