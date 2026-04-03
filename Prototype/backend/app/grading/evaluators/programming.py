from __future__ import annotations

from typing import List

from ..adapters.autograde_adapter import AutoGradeAdapter, AutoGradeRunConfig
from ..contracts import BaseEvaluator, ConfidenceReport, GradingRequest, GradingResult, QuestionType


class ProgrammingEvaluator(BaseEvaluator):
    supported_types = [QuestionType.CODE, QuestionType.COMPOSITE]

    def __init__(self, key: str = "programming", language_hint: str = "csharp") -> None:
        self.key = key
        self.language_hint = language_hint

    def grade(self, request: GradingRequest) -> GradingResult:
        metadata = request.metadata or {}
        language_hint = str(metadata.get("code_specialty") or self.language_hint)
        adapter = AutoGradeAdapter(
            AutoGradeRunConfig(
                evaluator_key=self.key,
                route_type="code",
                language_hint=language_hint,
            )
        )
        run_output = adapter.run(
            submission_archive_path=request.submission_archive_path,
            instructions_path=request.instructions_path,
            output_dir=request.output_dir,
            metadata=metadata,
        )

        artifacts: List[str] = run_output.get("artifact_files", [])
        confidence_data = run_output.get("confidence", {})

        confidence = ConfidenceReport(
            value=float(confidence_data.get("value", 0.85)),
            source=str(confidence_data.get("source", "heuristic")),
            reasons=[str(item) for item in confidence_data.get("reasons", [])],
        )

        return GradingResult(
            job_id=request.job_id,
            evaluator_key=self.key,
            status="finished",
            artifact_files=artifacts,
            confidence=confidence,
            details={
                "output_dir": str(request.output_dir),
            },
        )
