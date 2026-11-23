"""Compare blind film descriptions with human captions, leaving each human out in turn.

Input is a JSON list of {"id": "film", "human": ["...", "..."], "readers": {"name": "..."}}.
Use the same films and reader names throughout. Optional panels such as {"sound": ["..."]} under
"panels" contain independent single-modality captions. All references must describe the same cut.
Run with python examples/check_first_impressions.py reports.json. Requires torch and transformers.
With "picture" and "sound" human panels, --sound-effect MUTED FULL compares the two reader
conditions on the same pictures. Only FULL should receive the original soundtrack.
"""
import argparse
import json
from pathlib import Path

import numpy as np

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


def embeddings(sentences):
    import torch
    from transformers import AutoModel, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=REVISION)
    encoder = AutoModel.from_pretrained(MODEL, revision=REVISION).eval()
    vectors = {}
    with torch.inference_mode():
        for start in range(0, len(sentences), 32):
            batch = sentences[start:start + 32]
            tokens = tokenizer(batch, padding=True, truncation=True, max_length=256, return_tensors="pt")
            states = encoder(**tokens).last_hidden_state
            mask = tokens.attention_mask.unsqueeze(-1)
            pooled = (states * mask).sum(1) / mask.sum(1)
            unit = torch.nn.functional.normalize(pooled, dim=1).numpy()
            vectors.update(zip(batch, unit, strict=True))
    return vectors


def compare(films, vectors, *, sound_effect=None):
    if len(films) < 2 or len({f["id"] for f in films}) != len(films):
        raise ValueError("supply at least two films with distinct IDs")
    names = list(films[0]["readers"])
    if not names or "human" in names:
        raise ValueError("give each reader a name distinct from human")
    references = []
    for film in films:
        if len(film["human"]) < 2 or set(film["readers"]) != set(names):
            raise ValueError("each film needs two human captions and the same readers")
        references.append(np.stack([vectors[s] for s in film["human"]]))
    panel = np.stack([refs.mean(axis=0) for refs in references])
    by_film = {}
    for i, film in enumerate(films):
        people = references[i]
        scores = {name: [] for name in ["human", *names]}
        # Use exactly the same reference panel for a human and each model on every fold.
        for held in range(len(people)):
            others = np.delete(people, held, axis=0).mean(axis=0)
            responses = {"human": people[held], **{k: vectors[v] for k, v in film["readers"].items()}}
            for name, sentence in responses.items():
                similarities = panel @ sentence
                similarities[i] = others @ sentence
                same = similarities[i]
                other = np.delete(similarities, i)
                # A tie shares the credit equally among the top-scoring films.
                top = float(same == similarities.max()) / np.count_nonzero(similarities == similarities.max())
                scores[name].append([same, other.mean(), top])
        by_film[film["id"]] = {k: np.mean(v, axis=0).tolist() for k, v in scores.items()}
    summary = {}
    for name in ["human", *names]:
        same, other, top = np.mean([r[name] for r in by_film.values()], axis=0)
        summary[name] = {"same_film_cosine": float(same), "other_film_cosine": float(other),
                         "same_minus_other": float(same - other), "top_one_film": float(top)}
    report = {"model": MODEL, "revision": REVISION, "films": len(films),
            "human_captions": sum(map(len, references)), "summary": summary,
            "by_film_columns": ["same_film_cosine", "other_film_cosine", "top_one_film"], "by_film": by_film}
    panel_names = films[0].get("panels", {}).keys()
    if any(film.get("panels", {}).keys() != panel_names for film in films):
        raise ValueError("supply each independent panel for every film")
    if sound_effect is not None:
        muted, full = sound_effect
        if muted == full or not {muted, full}.issubset(names):
            raise ValueError("give distinct picture-only and audiovisual reader names from the reports")
        if not {"picture", "sound"}.issubset(panel_names):
            raise ValueError("sound-effect comparison needs human picture-only and sound-only panels")
        if any(not film["panels"]["picture"] for film in films):
            raise ValueError("each picture-only reference panel needs a caption")
    if panel_names:
        report["independent_panels"] = {}
    for panel_name in panel_names:
        groups = [film["panels"][panel_name] for film in films]
        if not all(groups):
            raise ValueError("a reference panel has no captions")
        bank = np.stack([np.mean([vectors[s] for s in group], axis=0) for group in groups])
        if sound_effect is not None and panel_name == "sound":
            changes = {}
            for i, film in enumerate(films):
                # Subtract the other films so generic mentions of sound get no special credit.
                contrast = bank[i] - np.delete(bank, i, axis=0).mean(axis=0)
                picture_only = np.mean([vectors[s] for s in film["panels"]["picture"]], axis=0)
                human_change = references[i].mean(axis=0) - picture_only
                reader_change = vectors[film["readers"][full]] - vectors[film["readers"][muted]]
                changes[film["id"]] = {"human": float(human_change @ contrast),
                                        "reader": float(reader_change @ contrast)}
            report["sound_effect"] = {
                "picture_only_reader": muted, "audiovisual_reader": full, "by_film": changes,
                "mean": {k: float(np.mean([c[k] for c in changes.values()])) for k in ("human", "reader")}}
        scores = {}
        for name in ["human", *names]:
            per_film = []
            for i, film in enumerate(films):
                captions = film["human"] if name == "human" else [film["readers"][name]]
                similarity = np.mean([bank @ vectors[s] for s in captions], axis=0)
                per_film.append([similarity[i], np.delete(similarity, i).mean()])
            same, other = np.mean(per_film, axis=0)
            scores[name] = {"same_film_cosine": float(same), "other_film_cosine": float(other),
                            "same_minus_other": float(same - other)}
        report["independent_panels"][panel_name] = {"captions": sum(map(len, groups)), "summary": scores}
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path)
    parser.add_argument("--sound-effect", nargs=2, metavar=("MUTED", "FULL"),
                        help="compare first descriptions with and without the soundtrack against human panels")
    args = parser.parse_args()
    films = json.loads(args.reports.read_text())
    captions = []
    for film in films:
        captions.extend([*film["human"], *film["readers"].values()])
        for panel in film.get("panels", {}).values():
            captions.extend(panel)
    if not all(isinstance(s, str) and s.strip() for s in captions):
        parser.error("each response must contain a nonempty caption")
    captions = sorted(set(captions))
    report = compare(films, embeddings(captions), sound_effect=args.sound_effect)
    print(json.dumps(report, ensure_ascii=False, indent=2))
