from __future__ import annotations

from pathlib import Path
from typing import List

from ..contracts import BaseEvaluator, ConfidenceReport, GradingRequest, GradingResult, QuestionType


class ProgrammingEvaluator(BaseEvaluator):
    key = "programming"
    supported_types = [QuestionType.CODE, QuestionType.COMPOSITE]

    def grade(self, request: GradingRequest) -> GradingResult:
        # Import lazily to keep startup fast and isolate grader dependencies.
        from AutoGrade import AutoGrader  # pylint: disable=import-outside-toplevel

        grader = AutoGrader()
        grader.grade_all_assignments(
            str(request.submission_archive_path),
            str(request.instructions_path),
            str(request.output_dir),
        )

        artifacts: List[str] = sorted([p.name for p in Path(request.output_dir).glob("*") if p.is_file()])

        confidence = ConfidenceReport(
            value=0.85,
            source="heuristic",
            reasons=[
                "Programming evaluator runs deterministic file extraction and structure checks.",
                "Final rubric grading currently uses LLM reasoning for part-level assessment.",
            ],
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
