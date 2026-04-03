from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .contracts import GradingRequest, GradingResult
from .registry import get_evaluator


def run_grading_pipeline(
    job_id: str,
    evaluator_key: str,
    main_zip_path: str,
    instructions_html_path: str,
    output_dir: str,
    metadata: Dict[str, Any] | None = None,
) -> GradingResult:
    request = GradingRequest(
        job_id=job_id,
        evaluator_key=evaluator_key,
        submission_archive_path=Path(main_zip_path),
        instructions_path=Path(instructions_html_path),
        output_dir=Path(output_dir),
        metadata=metadata or {},
    )
    evaluator = get_evaluator(evaluator_key)
    return evaluator.grade(request)
