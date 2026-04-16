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


def detect_code_specialty(instructions_text: str) -> Tuple[str, str]:
    """Infer a likely code language specialty for code-route assignments."""
    text = (instructions_text or "").lower()

    if re.search(r"\bc#\b|\bwinforms\b|\bwindows forms\b|\.cs\b", text):
        return "csharp", "Detected C#/WinForms indicators"
    if re.search(r"\bpython\b|\.py\b|\bpandas\b|\bnumpy\b", text):
        return "python", "Detected Python indicators"
    if re.search(r"\bjavascript\b|\bnode\b|\.js\b|\breact\b", text):
        return "javascript", "Detected JavaScript indicators"

    # Keep SQL detection specific enough to avoid false positives from generic
    # words like "select" in non-SQL instructions.
    if re.search(
        r"\bsql\b|\bquery\b|\bjoin\b|\bgroup\s+by\b|\border\s+by\b|\bcreate\s+table\b|\binsert\s+into\b|\bupdate\s+\w+\s+set\b|\bdelete\s+from\b|\bselect\b\s+.+\s+\bfrom\b",
        text,
        flags=re.IGNORECASE,
    ):
        return "sql", "Detected SQL indicators"

    return "csharp", "No strong language indicator; defaulting to C# profile"


def map_route_to_evaluator(route_type: str, code_specialty: str = "csharp") -> str:
    # Non-code routes currently fall back to programming while specialized evaluators are added incrementally.
    if route_type == "code":
        specialty_map = {
            "csharp": "code-csharp",
            "python": "code-python",
            "javascript": "code-javascript",
            "sql": "code-sql",
        }
        return specialty_map.get(code_specialty, "programming")
    return "programming"
