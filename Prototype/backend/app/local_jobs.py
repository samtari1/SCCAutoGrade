from __future__ import annotations

from threading import Lock, Thread
from typing import Any, Dict, Optional

from .tasks import run_grading_job


_LOCK = Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}


def submit_local_job(
    job_id: str,
    main_zip_path: str,
    instructions_html_path: str,
    output_dir: str,
    evaluator_key: str,
    route_type: str,
    routing_reason: str,
    code_specialty: str,
    multi_agent_grading: bool,
    multi_agent_disagreement_threshold: float,
    multi_agent_part_disagreement_threshold: float,
    grading_context: str,
) -> None:
    with _LOCK:
        _JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "message": "Job queued (in-memory mode)",
            "error": None,
            "cancel_requested": False,
            "evaluator_key": evaluator_key,
            "route_type": route_type,
            "routing_reason": routing_reason,
            "confidence": None,
            "artifact_files": [],
        }

    def _runner() -> None:
        with _LOCK:
            if _JOBS[job_id].get("cancel_requested"):
                _JOBS[job_id]["status"] = "canceled"
                _JOBS[job_id]["message"] = "Job canceled before execution"
                return

        with _LOCK:
            _JOBS[job_id]["status"] = "started"
            _JOBS[job_id]["message"] = "Running grading"

        try:
            result = run_grading_job(
                job_id,
                main_zip_path,
                instructions_html_path,
                output_dir,
                evaluator_key=evaluator_key,
                route_type=route_type,
                routing_reason=routing_reason,
                code_specialty=code_specialty,
                multi_agent_grading=multi_agent_grading,
                multi_agent_disagreement_threshold=multi_agent_disagreement_threshold,
                multi_agent_part_disagreement_threshold=multi_agent_part_disagreement_threshold,
                grading_context=grading_context,
            )
            with _LOCK:
                _JOBS[job_id]["status"] = result.get("status", "finished")
                _JOBS[job_id]["message"] = "Grading completed"
                _JOBS[job_id]["artifact_files"] = result.get("artifact_files", [])
                _JOBS[job_id]["evaluator_key"] = result.get("evaluator_key", evaluator_key)
                _JOBS[job_id]["route_type"] = result.get("route_type", route_type)
                _JOBS[job_id]["routing_reason"] = result.get("routing_reason", routing_reason)
                _JOBS[job_id]["confidence"] = result.get("confidence")
        except Exception as exc:  # pragma: no cover - defensive runtime path
            with _LOCK:
                _JOBS[job_id]["status"] = "failed"
                _JOBS[job_id]["error"] = str(exc)

    thread = Thread(target=_runner, daemon=True)
    thread.start()


def get_local_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        return dict(job)


def cancel_local_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Best-effort cancellation for in-memory mode.

    Queued jobs are canceled immediately. Running jobs are marked as
    "stopping" but cannot be force-terminated safely from this thread model.
    """
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None

        status = str(job.get("status", "unknown"))
        if status in {"finished", "failed", "canceled", "stopped"}:
            return {
                "job_id": job_id,
                "status": status,
                "message": "Job is already in a terminal state",
            }

        if status in {"queued", "deferred", "scheduled"}:
            job["cancel_requested"] = True
            job["status"] = "canceled"
            job["message"] = "Job canceled"
            return {
                "job_id": job_id,
                "status": "canceled",
                "message": job["message"],
            }

        # In-memory execution runs in a thread and cannot be forcibly killed safely.
        job["cancel_requested"] = True
        job["status"] = "stopping"
        job["message"] = "Stop requested (in-memory mode cannot force-stop an active run)"
        return {
            "job_id": job_id,
            "status": "stopping",
            "message": job["message"],
        }
