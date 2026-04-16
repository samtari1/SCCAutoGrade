from __future__ import annotations

from threading import Lock, Thread
from typing import Any, Dict, List, Optional

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
    completed_students: Optional[List[str]] = None,
    selected_students: Optional[List[str]] = None,
) -> None:
    existing_job = get_local_job(job_id) or {}
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
            "artifact_files": list(existing_job.get("artifact_files", [])),
            "completed_students": list(completed_students or existing_job.get("completed_students", [])),
        }

    def _cancel_check() -> bool:
        with _LOCK:
            return bool(_JOBS.get(job_id, {}).get("cancel_requested"))

    def _progress_callback(student_name: str, artifact_files: Optional[List[str]] = None) -> None:
        with _LOCK:
            job = _JOBS.get(job_id)
            if not job:
                return
            if student_name and student_name not in job["completed_students"]:
                job["completed_students"].append(student_name)
            if artifact_files:
                job["artifact_files"] = sorted(
                    set(job.get("artifact_files", [])) | set(artifact_files)
                )
            if student_name:
                job["message"] = f"Completed {len(job['completed_students'])} submission(s); current stop will apply before the next student"

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
                completed_students=completed_students,
                selected_students=selected_students,
                cancel_check=_cancel_check,
                progress_callback=_progress_callback,
            )
            with _LOCK:
                final_status = result.get("status", "finished")
                _JOBS[job_id]["status"] = final_status
                if final_status == "stopped":
                    _JOBS[job_id]["message"] = "Grading stopped after the current student. Resume is available."
                elif final_status == "canceled":
                    _JOBS[job_id]["message"] = "Job canceled"
                else:
                    _JOBS[job_id]["message"] = "Grading completed"
                _JOBS[job_id]["artifact_files"] = sorted(
                    set(_JOBS[job_id].get("artifact_files", [])) | set(result.get("artifact_files", []))
                )
                _JOBS[job_id]["evaluator_key"] = result.get("evaluator_key", evaluator_key)
                _JOBS[job_id]["route_type"] = result.get("route_type", route_type)
                _JOBS[job_id]["routing_reason"] = result.get("routing_reason", routing_reason)
                _JOBS[job_id]["confidence"] = result.get("confidence")
                _JOBS[job_id]["cancel_requested"] = False
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

        # In-memory execution runs in a thread, so stop is cooperative and
        # takes effect before the next student begins grading.
        job["cancel_requested"] = True
        job["status"] = "stopping"
        job["message"] = "Stop requested. In-memory mode will stop after the current student finishes."
        return {
            "job_id": job_id,
            "status": "stopping",
            "message": job["message"],
        }
