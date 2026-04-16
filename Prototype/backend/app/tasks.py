from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from rq import get_current_job

from .grading import register_default_evaluators
from .grading.service import run_grading_pipeline


class _AutoFlushWriter:
    """Proxy writer that flushes on every write for real-time stream logs."""

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def write(self, data):
        written = self._wrapped.write(data)
        self._wrapped.flush()
        return written

    def flush(self):
        self._wrapped.flush()

    def isatty(self):
        return False


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
    grading_context: str = "",
    completed_students: List[str] | None = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[Callable[[str, Optional[List[str]]], None]] = None,
) -> Dict[str, Any]:
    """RQ task that runs backend grading pipeline for one grading run.
    
    If completed_students is provided, grades only students not in that list.
    """
    _update_job_meta("started", "Preparing grading run")
    _update_job_meta("started", "Running grading")

    register_default_evaluators()

    stream_log_path = Path(output_dir).parent / "stream.log"
    stream_log_path.parent.mkdir(parents=True, exist_ok=True)

    with stream_log_path.open("a", encoding="utf-8", buffering=1) as stream_log:
        resume_note = f" (resuming - skipping {len(completed_students or [])} completed)" if completed_students else ""
        stream_log.write(
            f"[router] evaluator={evaluator_key}, route_type={route_type}, reason={routing_reason}{resume_note}\n"
        )
        stream_log.flush()
        live_stream = _AutoFlushWriter(stream_log)
        with redirect_stdout(live_stream), redirect_stderr(live_stream):
            result = run_grading_pipeline(
                job_id=job_id,
                evaluator_key=evaluator_key,
                main_zip_path=main_zip_path,
                instructions_html_path=instructions_html_path,
                output_dir=output_dir,
                metadata={
                    "job_id": job_id,
                    "code_specialty": code_specialty,
                    "multi_agent_grading": multi_agent_grading,
                    "multi_agent_disagreement_threshold": multi_agent_disagreement_threshold,
                    "multi_agent_part_disagreement_threshold": multi_agent_part_disagreement_threshold,
                    "grading_context": grading_context,
                    "completed_students": completed_students or [],
                    "cancel_check": cancel_check,
                    "progress_callback": progress_callback,
                },
            )

    final_status = result.status or "finished"
    if final_status == "stopped":
        _update_job_meta("stopped", "Grading stopped after the current student")
    elif final_status == "canceled":
        _update_job_meta("canceled", "Job canceled")
    else:
        _update_job_meta("finished", "Grading completed")
    return {
        "job_id": job_id,
        "status": final_status,
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
