from __future__ import annotations

from typing import Dict

from .contracts import BaseEvaluator


_EVALUATORS: Dict[str, BaseEvaluator] = {}


def register_evaluator(evaluator: BaseEvaluator) -> None:
    _EVALUATORS[evaluator.key] = evaluator


def get_evaluator(evaluator_key: str) -> BaseEvaluator:
    if evaluator_key not in _EVALUATORS:
        available = ", ".join(sorted(_EVALUATORS.keys())) or "none"
        raise KeyError(f"Unknown evaluator '{evaluator_key}'. Available: {available}")
    return _EVALUATORS[evaluator_key]


def list_evaluators() -> Dict[str, BaseEvaluator]:
    return dict(_EVALUATORS)
