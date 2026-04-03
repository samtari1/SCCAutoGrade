from __future__ import annotations

import re
from typing import Tuple


def detect_route_type(instructions_text: str) -> Tuple[str, str]:
    """Infer a broad route category from assignment instructions."""
    text = (instructions_text or "").lower()

    code_hits = [
        r"\bc#\b",
        r"\bjava\b",
        r"\bpython\b",
        r"\bc\+\+\b",
        r"\bprogram\b",
        r"\bcompile\b",
        r"\bfunction\b",
        r"\bwindows forms\b",
        r"\bsql\b",
        r"\bquery\b",
    ]
    essay_hits = [
        r"\bessay\b",
        r"\bthesis\b",
        r"\bargument\b",
        r"\bcitation\b",
        r"\breflection\b",
        r"\bshort answer\b",
        r"\bhistory\b",
        r"\bliterature\b",
    ]
    numeric_hits = [
        r"\bsolve\b",
        r"\bequation\b",
        r"\bderivative\b",
        r"\bintegral\b",
        r"\bnumeric\b",
        r"\bcalculus\b",
        r"\bphysics\b",
        r"\bformula\b",
    ]
    mcq_hits = [
        r"\bmultiple choice\b",
        r"\bmcq\b",
        r"\bselect one\b",
        r"\boption\s+[abcd]\b",
    ]

    def score(patterns):
        return sum(1 for p in patterns if re.search(p, text))

    scores = {
        "code": score(code_hits),
        "essay": score(essay_hits),
        "numeric": score(numeric_hits),
        "mcq": score(mcq_hits),
    }

    best_type = max(scores, key=scores.get)
    best_score = scores[best_type]

    if best_score == 0:
        return "code", "Fallback to code route (no clear signals found)"

    if best_type == "code":
        return "code", "Detected programming/SQL keywords in assignment"
    if best_type == "essay":
        return "essay", "Detected essay/short-answer style rubric keywords"
    if best_type == "numeric":
        return "numeric", "Detected numeric/math/science solving keywords"
    return "mcq", "Detected multiple-choice assessment keywords"


def map_route_to_evaluator(route_type: str) -> str:
    # Current implementation supports programming evaluator only.
    if route_type == "code":
        return "programming"
    return "programming"
