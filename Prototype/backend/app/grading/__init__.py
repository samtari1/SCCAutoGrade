from .evaluators.programming import ProgrammingEvaluator
from .registry import register_evaluator


def register_default_evaluators() -> None:
    register_evaluator(ProgrammingEvaluator())
