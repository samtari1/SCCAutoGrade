from .evaluators.programming import ProgrammingEvaluator
from .registry import register_evaluator


def register_default_evaluators() -> None:
    register_evaluator(ProgrammingEvaluator(key="programming", language_hint="csharp"))
    register_evaluator(ProgrammingEvaluator(key="code-csharp", language_hint="csharp"))
    register_evaluator(ProgrammingEvaluator(key="code-python", language_hint="python"))
    register_evaluator(ProgrammingEvaluator(key="code-javascript", language_hint="javascript"))
    register_evaluator(ProgrammingEvaluator(key="code-sql", language_hint="sql"))
