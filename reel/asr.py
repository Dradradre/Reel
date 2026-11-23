#!/usr/bin/env python3
"""asr.py - the reel's ears for words. Runs Whisper large-v3-turbo on the Apple GPU (mlx-whisper) (falls back to faster-whisper).

  python asr.py speech VOCALS.wav MIX.wav --out words.json [--lang ko]
  python asr.py chant CROWD.wav --windows "36:46,204:212" --names "티원,젠지,..." --out chants.json

speech: accepts words supported by voice activity on the separated track and checks the full mix for
        speech in uncovered gaps of at least 2 s.
chant:  for each window where the crowd pulses at a steady beat, transcribes the crowd alone and takes the word that
        repeats. Chants are rarely heard exactly, by people either; the heard word is matched by sound to names the
        film shows on screen (and the genre's usual names), the way a listener fills in a chant from names they know.
"""
from __future__ import annotations
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import argparse, json, re, sys
from bisect import bisect_right
from itertools import groupby
import unicodedata
import numpy as np
from common import read_audio

MODEL = "mlx-community/whisper-large-v3-turbo"
SR = 16000


def load(path, ss=None, t=None):
    return read_audio(path, SR, ss=ss, t=t, channels=1)[:, 0]


def _fw(y, lang, options):
    """faster-whisper fallback (CPU) with the same output shape as mlx-whisper."""
    from faster_whisper import WhisperModel
    global _FW
    if "_FW" not in globals():
        try:
            _FW = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
        except Exception:
            _FW = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = _FW.transcribe(y, language=lang, word_timestamps=True, condition_on_previous_text=False, **options)
    return {"segments": [dict(no_speech_prob=s.no_speech_prob, compression_ratio=s.compression_ratio,
                              words=[dict(start=w.start, end=w.end, word=w.word, probability=w.probability) for w in (s.words or [])])
                         for s in segs]}


def transcribe(y, lang, offset=0.0, *, deterministic=False):
    options = {"temperature": 0.0} if deterministic else {}
    try:
        import mlx_whisper
        r = mlx_whisper.transcribe(y, path_or_hf_repo=MODEL, language=lang, word_timestamps=True,
                                   condition_on_previous_text=False, no_speech_threshold=0.6, **options)
    except ImportError:
        r = _fw(y, lang, options)
    out = []
    for s in r["segments"]:
        if s.get("no_speech_prob", 0) > 0.8 or s.get("compression_ratio", 1) > 2.6:      # silence or a hallucinated loop
            continue
        words = [dict(t=round(offset + w["start"], 2), e=round(offset + w["end"], 2), w=w["word"],
                      p=round(float(w.get("probability", 1.0)), 2)) for w in s.get("words", [])]
        out.extend(join_spelling_marks(words))
    return out


def join_spelling_marks(words):
    """Whisper can timestamp the vowel mark on a letter as a separate piece."""
    joined = []
    for word in words:
        text = word["w"]
        prior = joined[-1] if joined else None
        if (prior and text and unicodedata.category(text[0]).startswith("M")
                and prior["w"] and (prior["w"][-1].isalnum() or unicodedata.category(prior["w"][-1]).startswith("M"))
                and prior["t"] <= word["t"] <= prior["e"]):
            prior["w"] += text
            prior["e"] = max(prior["e"], word["e"])
            prior["p"] = min(prior["p"], word["p"])
        else:
            joined.append(dict(word))
    for word in joined:
        word["w"] = word["w"].strip()
    return joined


def word_tokens(text):
    """Keep the letters, numbers, joiners and spelling marks of each lexical span."""
    normalized = unicodedata.normalize("NFKC", text).casefold()

    def lexical(char):
        return char.isalnum() or unicodedata.category(char).startswith("M") or char in "\u200c\u200d"

    spans = ("".join(group) for keep, group in groupby(normalized, lexical) if keep)
    return [span for span in spans if any(char.isalnum() for char in span)]


def speech_regions(y, *, pad_ms=120):
    from faster_whisper.vad import get_speech_timestamps
    if not len(y):
        return []
    regions = get_speech_timestamps(y, min_silence_duration_ms=350, speech_pad_ms=pad_ms)
    return [(int(r["start"]), int(r["end"])) for r in regions]


def matching_words(first, second):
    """Compare chronological readings where the words actually overlap on the recording."""
    j = 0
    spellings = [word_tokens(w["w"]) for w in second]
    for word in first:
        spelling = word_tokens(word["w"])
        if not spelling or word["e"] <= word["t"]:
            continue
        while j < len(second):
            other = second[j]
            if other["t"] >= word["e"]:
                break
            if max(word["t"], other["t"]) < min(word["e"], other["e"]) and spelling == spellings[j]:
                yield word
                j += 1
                break
            if other["e"] > word["e"]:
                break
            j += 1


def speech_words(y, regions, lang, min_prob, *, recognize=None):
    """Decode speech in its original time range, without feeding the recognizer long quiet gaps."""
    if recognize is None:
        recognize = transcribe
    words = []
    for start, end in regions:
        close = recognize(y[start:end], lang, start / SR)
        candidates = [w for w in close if w["p"] >= min_prob and w["t"] < min(w["e"], end / SR)]
        if not candidates:
            continue
        # Keep a word if its identity and timing survive a change of listening window.
        # Use real surrounding audio where available, zero-pad only at the file edges.
        margin = SR
        left, right = start - margin, end + margin
        context = y[max(0, left):min(len(y), right)]
        context = np.pad(context, (max(0, -left), max(0, right - len(y))))
        second = [w for w in recognize(context, lang, left / SR)
                  if w["p"] >= min_prob and w["e"] > start / SR and w["t"] < end / SR]
        words.extend(dict(w, e=min(w["e"], round(end / SR, 2))) for w in matching_words(candidates, second))
    return words


def corroborate_stem(words, stem_regions, mix, mix_regions, lang):
    """Check isolated words against the recording when only the separator suggests speech."""
    ends = [end / SR for _, end in mix_regions]
    stem_ends = [end / SR for _, end in stem_regions]
    supported = []
    check = {}
    for word in words:
        i = bisect_right(ends, word["t"])
        if i < len(ends) and mix_regions[i][0] / SR < word["e"]:
            supported.append(word)
        else:
            i = bisect_right(stem_ends, word["t"])
            if i < len(stem_regions):
                check.setdefault(i, []).append(word)

    for i, candidates in check.items():
        start, end = stem_regions[i]
        left = max(0, start - SR)
        real = mix[left:min(end + SR, len(mix))]
        peak = np.max(np.abs(real), initial=0.)
        if not peak:
            continue
        gain = max(1., 10 ** (-3 / 20) / peak)
        heard = [w for w in transcribe(real * gain, lang, left / SR) if w["p"] >= 0.5]
        for word in matching_words(candidates, heard):
            match = dict(word, src="stem+mix")
            if gain > 1.:
                match.update(gain_db=round(float(20 * np.log10(gain)), 1),
                             context_start_s=left / SR, context_end_s=(left + len(real)) / SR)
            supported.append(match)
    return sorted(supported, key=lambda w: w["t"])


def speech(vocals, mix, lang, activity=None):
    yv = load(vocals)
    regions = speech_regions(yv)
    words = speech_words(yv, regions, lang, 0.3)
    if vocals != mix:
        # Separation can lose a voice. Retry speech in the mix where the stem gave no words.
        ym = load(mix)
        mix_regions = speech_regions(ym)
        words = corroborate_stem(words, regions, ym, mix_regions, lang)
        stem_words = iter(words)
        word = next(stem_words, None)
        extra = []
        for start, end in mix_regions:
            # Retry the whole utterance, not the tail of an already recognised word.
            # ASR and VAD boundaries can differ by a few hundred milliseconds.
            left, right = start / SR, end / SR
            cursor = left
            gaps = []
            covered = False
            while word is not None and word["t"] < right:
                if word["e"] > left:
                    covered = True
                    if word["t"] - cursor >= 2:
                        gaps.append((cursor, word["t"]))
                    cursor = max(cursor, word["e"])
                if word["e"] > right:
                    break
                word = next(stem_words, None)
            if not covered or right - cursor >= 2:
                gaps.append((cursor, right))
            if gaps:
                for w in speech_words(ym, [(start, end)], lang, 0.5):
                    if any(a <= (w["t"] + w["e"]) / 2 < b for a, b in gaps):
                        extra.append(dict(w, src="mix"))
        words.extend(extra)
        regions.extend(mix_regions)
        regions.sort()
    if activity is not None:
        for start, end in regions:
            if activity and start / SR <= activity[-1][1]:
                activity[-1][1] = max(activity[-1][1], end / SR)
            else:
                activity.append([start / SR, end / SR])
    return sorted(words, key=lambda w: w["t"])


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
    ap.add_argument("--activity-out", help="also write detected speech intervals in seconds")
    ap.add_argument("--out", required=True); ap.add_argument("--lang", default="ko")
    ap.add_argument("--windows", default=""); ap.add_argument("--names", default="")
    x = ap.parse_args()
    if x.cmd == "speech":
        activity = []
        out = speech(x.audio, x.mix or x.audio, x.lang, activity)
        if x.activity_out:
            with open(x.activity_out, "w") as file:
                json.dump(activity, file)
    else:
        win = [tuple(float(v) for v in w.split(":")) for w in x.windows.split(",") if w]
        out = chant(x.audio, win, x.names.split(","), x.lang)
    json.dump(out, open(x.out, "w"), ensure_ascii=False)
    print(len(out))
