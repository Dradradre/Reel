"""Score a reader's three answers per film against the published human annotations.

Usage: python examples/check_perception_agreement.py answers.json
Supply a JSON report with a by_film list. Each row contains video_id and answers,
e.g. {"video_id": "video_1775", "answers": ["C", "?", "N"]}. The letters and
their order are specific to each film. Give the reader only the questions and media.
"""
import argparse
import json
from pathlib import Path


PANEL = Path(__file__).resolve().parents[1] / "docs/benchmarks/perception-agreement-v1.json"
QUESTIONS = {"picture": "ABC", "sound": "AB", "visible_source": "YN"}


def score(films, responses):
    references = {film["video_id"]: film["human_reference"] for film in films}
    if not references or len(references) != len(films):
        raise ValueError("the panel needs one reference per distinct film")
    by_question = {q: dict(correct=0, wrong=0, abstained=0) for q in QUESTIONS}
    counted = set()
    all_correct = 0
    for row in responses:
        film = row["video_id"]
        if film in counted or film not in references:
            raise ValueError(f"duplicate or unknown film: {film}")
        # The first published measurement stored responses under gpt_answers.
        answers = row.get("answers", row.get("gpt_answers"))
        if not isinstance(answers, list) or len(answers) != len(QUESTIONS):
            raise ValueError(f"{film} needs a list of three answers")
        for (question, choices), answer, reference in zip(QUESTIONS.items(), answers, references[film], strict=True):
            if answer not in [*choices, "?"] or reference not in list(choices):
                raise ValueError(f"{film} has an invalid {question} letter")
            if answer == reference:
                by_question[question]["correct"] += 1
            elif answer == "?":
                by_question[question]["abstained"] += 1
            else:
                by_question[question]["wrong"] += 1
        counted.add(film)
        all_correct += answers == references[film]
    if missing := references.keys() - counted:
        raise ValueError(f"missing films: {', '.join(sorted(missing))}")
    totals = {key: sum(q[key] for q in by_question.values()) for key in ("correct", "wrong", "abstained")}
    total = sum(totals.values())
    return {**totals, "total": total, "fraction": totals["correct"] / total,
            "films": len(films), "all_three_correct": all_correct, "by_question": by_question}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--panel", type=Path, default=PANEL)
    args = parser.parse_args()
    try:
        result = score(json.loads(args.panel.read_text())["films"], json.loads(args.report.read_text())["by_film"])
    except (KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))
