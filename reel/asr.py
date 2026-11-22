#!/usr/bin/env python3
"""asr.py - the reel's ears for words. Runs Whisper large-v3-turbo on the Apple GPU (mlx-whisper) (falls back to faster-whisper).

  python asr.py speech VOCALS.wav MIX.wav --out words.json [--lang ko]
  python asr.py chant CROWD.wav --windows "36:46,204:212" --names "티원,젠지,..." --out chants.json

speech: transcribes the separated voices; wherever the voices give nothing for 2 s or more but there is sound, the
        full mix is transcribed for that gap too (shouted commentary often survives better in the mix).
chant:  for each window where the crowd pulses at a steady beat, transcribes the crowd alone and takes the word that
        repeats. Chants are rarely heard exactly, by people either; the heard word is matched by sound to names the
        film shows on screen (and the genre's usual names), the way a listener fills in a chant from names they know.
"""
from __future__ import annotations
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import argparse, json, re, sys
import numpy as np

MODEL = "mlx-community/whisper-large-v3-turbo"
SR = 16000


def load(path, ss=None, t=None):
    import subprocess
    cmd = ["ffmpeg", "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    cmd += ["-i", str(path)]
    if t is not None:
        cmd += ["-t", f"{t:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.float32).copy()


def _fw(y, lang):
    """faster-whisper fallback (CPU) with the same output shape as mlx-whisper."""
    from faster_whisper import WhisperModel
    global _FW
    if "_FW" not in globals():
        try:
            _FW = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
        except Exception:
            _FW = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = _FW.transcribe(y, language=lang, word_timestamps=True, condition_on_previous_text=False)
    return {"segments": [dict(no_speech_prob=s.no_speech_prob, compression_ratio=s.compression_ratio,
                              words=[dict(start=w.start, end=w.end, word=w.word, probability=w.probability) for w in (s.words or [])])
                         for s in segs]}


def transcribe(y, lang, offset=0.0):
    try:
        import mlx_whisper
        r = mlx_whisper.transcribe(y, path_or_hf_repo=MODEL, language=lang, word_timestamps=True,
                                   condition_on_previous_text=False, no_speech_threshold=0.6)
    except ImportError:
        r = _fw(y, lang)
    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []

    out = []
    for s in r["segments"]:
        if s.get("no_speech_prob", 0) > 0.8 or s.get("compression_ratio", 1) > 2.6:      # silence or a hallucinated loop
            continue
        for w in s.get("words", []):
            out.append(dict(t=round(offset + w["start"], 2), e=round(offset + w["end"], 2), w=w["word"].strip(),
                            p=round(float(w.get("probability", 1.0)), 2)))
    return out


def speech(vocals, mix, lang):
    yv = load(vocals)
    words = [w for w in transcribe(yv, lang) if w["p"] >= 0.3]
    ym = load(mix)
    n = len(ym) / SR
    # gaps where the separated voices are clearly present but gave no words: try the full mix there.
    # (Gated on the voices, not the mix, so music alone is never transcribed into invented words.)
    k = int(0.1 * SR)
    lvl = 20 * np.log10(np.sqrt((yv[:len(yv) // k * k].reshape(-1, k) ** 2).mean(1)) + 1e-9)
    have = np.zeros(int(n * 10) + 1, bool)
    for w in words:
        have[int(w["t"] * 10):int(w["e"] * 10) + 1] = True
    i, extra = 0, []
    while i < len(have):
        if not have[i]:
            j = i
            while j < len(have) and not have[j]:
                j += 1
            a, b = i / 10, j / 10
            if b - a >= 2.0 and np.mean(lvl[i:min(j, len(lvl))] > -32) > 0.5:
                extra += [dict(w, src="mix") for w in transcribe(ym[int(a * SR):int(b * SR)], lang, a) if w["p"] >= 0.5]
            i = j
        else:
            i += 1
    return sorted(words + extra, key=lambda w: w["t"])


# ---------------------------------------------------------------- hearing a chant

CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONG = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ",
        "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
# consonant classes by place of articulation; vowels by rough position
CLS = {**{c: "velar" for c in "ㄱㄲㅋ"}, **{c: "alv" for c in "ㄷㄸㅌ"}, **{c: "lab" for c in "ㅂㅃㅍ"},
       **{c: "sib" for c in "ㅅㅆㅈㅉㅊ"}, **{c: "nas" for c in "ㄴㅁ"}, "ㄹ": "liq", "ㅎ": "glot", "ㅇ": "ng"}
VOW = {"ㅏ": (0, 2), "ㅐ": (1, 1), "ㅑ": (0, 2), "ㅒ": (1, 1), "ㅓ": (1, 2), "ㅔ": (1, 1), "ㅕ": (1, 2), "ㅖ": (1, 1),
       "ㅗ": (2, 2), "ㅘ": (0, 2), "ㅙ": (1, 1), "ㅚ": (1, 1), "ㅛ": (2, 2), "ㅜ": (2, 3), "ㅝ": (1, 2), "ㅞ": (1, 1),
       "ㅟ": (2, 0), "ㅠ": (2, 3), "ㅡ": (2, 3), "ㅢ": (2, 0), "ㅣ": (2, 0)}
LATIN = {"T1": "티원", "GEN": "젠", "GENG": "젠지", "DRX": "디알엑스", "HLE": "에이치엘이", "KT": "케이티", "DK": "디케이",
         "BLG": "비엘지", "IG": "아이지", "SKT": "에스케이티"}


def jamo(word):
    out = []
    for ch in word:
        o = ord(ch) - 0xAC00
        if 0 <= o < 11172:
            c, v, j = o // 588, (o % 588) // 28, o % 28
            if CHO[c] != "ㅇ":
                out.append(CHO[c])                 # a silent initial ㅇ is not heard
            out.append(JUNG[v])
            if JONG[j]:
                out.append(JONG[j][0])
    return out


def sub_cost(a, b):
    if a == b:
        return 0.0
    if a in VOW and b in VOW:
        (h1, b1), (h2, b2) = VOW[a], VOW[b]
        return min(1.0, 0.25 * (abs(h1 - h2) + abs(b1 - b2)))
    if a in VOW or b in VOW:
        return 1.0
    return 0.5 if CLS.get(a) == CLS.get(b) or {CLS.get(a), CLS.get(b)} <= {"velar", "alv", "lab"} else 0.9


def distance(x, y):
    """Weighted edit distance between two jamo sequences, divided by the longer length."""
    a, b = jamo(x), jamo(y)
    if not a or not b:
        return 1.0
    D = np.zeros((len(a) + 1, len(b) + 1))
    D[:, 0] = np.arange(len(a) + 1) * 0.8
    D[0, :] = np.arange(len(b) + 1) * 0.8
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            D[i, j] = min(D[i - 1, j] + 0.8, D[i, j - 1] + 0.8, D[i - 1, j - 1] + sub_cost(a[i - 1], b[j - 1]))
    return D[-1, -1] / max(len(a), len(b))


def chant(crowd, windows, names, lang):
    """Listen to each chant three times with slightly different edges (people re-hear a chant too), keep the word
    that repeats, and match every heard word by sound to the candidate names. Votes are added up over the whole film."""
    cands = []
    for n in names:
        n = n.strip()
        if n:
            cands.append((n, LATIN.get(re.sub(r"[^A-Za-z0-9]", "", n).upper(), n)))
    res = []
    for a, b in windows:
        toks = []
        for da, db in ((0, 0), (-1.5, 1.5), (1.0, -0.5)):
            lo, hi = max(a + da, 0), b + db
            if hi - lo < 2:
                continue
            text = " ".join(w["w"] for w in transcribe(load(crowd, lo, hi - lo), lang, lo))
            words = re.findall(r"[가-힣]{2,4}", text)
            if words:
                top = max(set(words), key=words.count)
                if words.count(top) >= 3:                       # a chant repeats; a stray word does not
                    toks += [top] * words.count(top)
        heard = max(set(toks), key=toks.count) if toks else ""
        score = {}
        for tk in set(toks):
            for shown, kr in cands:
                syl = abs(len(re.findall(r"[가-힣]", tk)) - len(re.findall(r"[가-힣]", kr)))   # a chant keeps its beat count
                score[shown] = score.get(shown, 0) + toks.count(tk) * max(0.0, 1 - distance(tk, kr) - 0.3 * syl)
        best = sorted(score.items(), key=lambda kv: -kv[1])[:3]
        res.append(dict(t0=a, t1=b, heard=heard, repeats=toks.count(heard) if heard else 0, variants=sorted(set(toks)),
                        closest=[dict(name=nm, dist=round(1 - sc / max(len(toks), 1), 2)) for nm, sc in best]))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["speech", "chant"])
    ap.add_argument("audio"); ap.add_argument("mix", nargs="?")
    ap.add_argument("--out", required=True); ap.add_argument("--lang", default="ko")
    ap.add_argument("--windows", default=""); ap.add_argument("--names", default="")
    x = ap.parse_args()
    if x.cmd == "speech":
        out = speech(x.audio, x.mix or x.audio, x.lang)
    else:
        win = [tuple(float(v) for v in w.split(":")) for w in x.windows.split(",") if w]
        out = chant(x.audio, win, x.names.split(","), x.lang)
    json.dump(out, open(x.out, "w"), ensure_ascii=False)
    print(len(out))
