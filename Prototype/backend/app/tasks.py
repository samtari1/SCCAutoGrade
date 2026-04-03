from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Dict, Any

from rq import get_current_job

from .grading import register_default_evaluators
from .grading.service import run_grading_pipeline


def _update_job_meta(status: str, message: str = "") -> None:
    job = get_current_job()
    if not job:
        return
    job.meta["status"] = status
    if message:
        job.meta["message"] = message
    job.save_meta()


def run_grading_job(
    job_id: str,
    main_zip_path: str,
    instructions_html_path: str,
    output_dir: str,
    evaluator_key: str = "programming",
    route_type: str = "code",
    routing_reason: str = "",
    code_specialty: str = "csharp",
    multi_agent_grading: bool = True,
    multi_agent_disagreement_threshold: float = 5.0,
    multi_agent_part_disagreement_threshold: float = 10.0,
) -> Dict[str, Any]:
    """RQ task that runs backend grading pipeline for one grading run."""
    _update_job_meta("started", "Preparing grading run")
    _update_job_meta("started", "Running grading")

    register_default_evaluators()

    stream_log_path = Path(output_dir).parent / "stream.log"
    stream_log_path.parent.mkdir(parents=True, exist_ok=True)

    with stream_log_path.open("a", encoding="utf-8") as stream_log:
        stream_log.write(
            f"[router] evaluator={evaluator_key}, route_type={route_type}, reason={routing_reason}\n"
        )
        stream_log.flush()
        with redirect_stdout(stream_log), redirect_stderr(stream_log):
            result = run_grading_pipeline(
                job_id=job_id,
                evaluator_key=evaluator_key,
                main_zip_path=main_zip_path,
                instructions_html_path=instructions_html_path,
                output_dir=output_dir,
                metadata={
                    "code_specialty": code_specialty,
                    "multi_agent_grading": multi_agent_grading,
                    "multi_agent_disagreement_threshold": multi_agent_disagreement_threshold,
                    "multi_agent_part_disagreement_threshold": multi_agent_part_disagreement_threshold,
                },
            )

    _update_job_meta("finished", "Grading completed")
    return {
        "job_id": job_id,
        "status": result.status,
        "output_dir": str(result.details.get("output_dir", output_dir)),
        "artifact_files": result.artifact_files,
        "evaluator_key": result.evaluator_key,
        "route_type": route_type,
        "routing_reason": routing_reason,
        "confidence": {
            "value": result.confidence.value,
            "source": result.confidence.source,
            "reasons": result.confidence.reasons,
        },
    }
