from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
import shutil
import uuid
from typing import Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from rq.command import send_stop_job_command
from rq.exceptions import InvalidJobOperation

from .local_jobs import cancel_local_job, get_local_job, submit_local_job
from .queueing import get_queue
from .schemas import CreateJobResponse, JobStatusResponse
from .settings import JOBS_DIR, USE_INMEMORY_QUEUE, ensure_directories
from .tasks import run_grading_job
from .grading import register_default_evaluators
from .grading.registry import get_evaluator, list_evaluators
from .grading.routing import detect_code_specialty, detect_route_type, map_route_to_evaluator


app = FastAPI(title="AutoGrade API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    ensure_directories()
    register_default_evaluators()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/evaluators")
def get_evaluators() -> dict:
    evaluators = list_evaluators()
    return {
        "evaluators": [
            {
                "key": key,
                "supported_types": [qt.value for qt in evaluator.supported_types],
            }
            for key, evaluator in sorted(evaluators.items(), key=lambda item: item[0])
        ]
    }


@app.post("/api/jobs", response_model=CreateJobResponse)
async def create_job(
    main_zip: Optional[UploadFile] = File(None),
    instructions_html: Optional[UploadFile] = File(None),
    reuse_job_id: str = Form(""),
    evaluator_key: str = Form("auto"),
    multi_agent_grading: bool = Form(True),
    multi_agent_disagreement_threshold: float = Form(5.0),
    multi_agent_part_disagreement_threshold: float = Form(10.0),
    grading_context: str = Form(""),
) -> CreateJobResponse:
    if multi_agent_disagreement_threshold < 0:
        raise HTTPException(status_code=400, detail="multi_agent_disagreement_threshold must be >= 0")
    if multi_agent_part_disagreement_threshold < 0:
        raise HTTPException(status_code=400, detail="multi_agent_part_disagreement_threshold must be >= 0")

    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    zip_path = input_dir / "submissions.zip"
    instructions_path = input_dir / "instructions.html"
    input_info_path = input_dir / "input_info.json"

    input_info: Dict[str, Optional[str]] = {
        "main_zip_name": None,
        "instructions_html_name": None,
        "reused_from_job_id": None,
    }

    reuse_job_id = (reuse_job_id or "").strip()
    if reuse_job_id:
        source_input_dir = JOBS_DIR / reuse_job_id / "input"
        source_zip = source_input_dir / "submissions.zip"
        source_instructions = source_input_dir / "instructions.html"
        if not source_zip.exists() or not source_instructions.exists():
            raise HTTPException(status_code=400, detail="Stored files for reuse were not found")

        shutil.copy2(source_zip, zip_path)
        shutil.copy2(source_instructions, instructions_path)

        input_info["reused_from_job_id"] = reuse_job_id
        source_info_path = source_input_dir / "input_info.json"
        if source_info_path.exists():
            try:
                source_info = json.loads(source_info_path.read_text(encoding="utf-8"))
                input_info["main_zip_name"] = source_info.get("main_zip_name")
                input_info["instructions_html_name"] = source_info.get("instructions_html_name")
            except (OSError, json.JSONDecodeError):
                pass
        input_info["main_zip_name"] = input_info["main_zip_name"] or "submissions.zip"
        input_info["instructions_html_name"] = input_info["instructions_html_name"] or "instructions.html"
    else:
        if not main_zip or not main_zip.filename or not main_zip.filename.lower().endswith(".zip"):
            raise HTTPException(status_code=400, detail="main_zip must be a .zip file")
        if not instructions_html or not instructions_html.filename or not instructions_html.filename.lower().endswith(".html"):
            raise HTTPException(status_code=400, detail="instructions_html must be a .html file")

        with zip_path.open("wb") as f:
            shutil.copyfileobj(main_zip.file, f)
        with instructions_path.open("wb") as f:
            shutil.copyfileobj(instructions_html.file, f)

        input_info["main_zip_name"] = main_zip.filename
        input_info["instructions_html_name"] = instructions_html.filename

    input_info_path.write_text(json.dumps(input_info), encoding="utf-8")

    instructions_text = instructions_path.read_text(encoding="utf-8", errors="ignore")
    route_type, routing_reason = detect_route_type(instructions_text)
    code_specialty = "csharp"
    if route_type == "code":
        code_specialty, specialty_reason = detect_code_specialty(instructions_text)
        routing_reason = f"{routing_reason}; specialty={code_specialty} ({specialty_reason})"

    selected_evaluator = evaluator_key.strip().lower()
    if selected_evaluator == "auto":
        selected_evaluator = map_route_to_evaluator(route_type, code_specialty=code_specialty)

    try:
        get_evaluator(selected_evaluator)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stream_log = job_dir / "stream.log"
    stream_log.write_text(
        (
            f"[router] route_type={route_type}; evaluator={selected_evaluator}; reason={routing_reason}; "
            f"multi_agent={str(multi_agent_grading).lower()}; "
            f"disagreement_threshold={multi_agent_disagreement_threshold}; "
            f"part_disagreement_threshold={multi_agent_part_disagreement_threshold}; "
            f"grading_context={'provided' if grading_context.strip() else 'none'}\n"
        ),
        encoding="utf-8",
    )

    if USE_INMEMORY_QUEUE:
        submit_local_job(
            job_id,
            str(zip_path),
            str(instructions_path),
            str(output_dir),
            evaluator_key=selected_evaluator,
            route_type=route_type,
            routing_reason=routing_reason,
            code_specialty=code_specialty,
            multi_agent_grading=multi_agent_grading,
            multi_agent_disagreement_threshold=multi_agent_disagreement_threshold,
            multi_agent_part_disagreement_threshold=multi_agent_part_disagreement_threshold,
            grading_context=grading_context,
        )
    else:
        queue = get_queue()
        queue.enqueue(
            run_grading_job,
            job_id,
            str(zip_path),
            str(instructions_path),
            str(output_dir),
            selected_evaluator,
            route_type,
            routing_reason,
            code_specialty,
            multi_agent_grading,
            multi_agent_disagreement_threshold,
            multi_agent_part_disagreement_threshold,
            grading_context,
            job_id=job_id,
            job_timeout="2h",
        )

    return CreateJobResponse(
        job_id=job_id,
        status="queued",
        evaluator_key=selected_evaluator,
        route_type=route_type,
        routing_reason=routing_reason,
    )


@app.get("/api/jobs/{job_id}/input-info")
def get_job_input_info(job_id: str) -> dict:
    input_dir = JOBS_DIR / job_id / "input"
    zip_path = input_dir / "submissions.zip"
    instructions_path = input_dir / "instructions.html"
    if not zip_path.exists() or not instructions_path.exists():
        raise HTTPException(status_code=404, detail="Job input files not found")

    info = {
        "job_id": job_id,
        "main_zip_name": "submissions.zip",
        "instructions_html_name": "instructions.html",
        "reused_from_job_id": None,
    }
    input_info_path = input_dir / "input_info.json"
    if input_info_path.exists():
        try:
            stored = json.loads(input_info_path.read_text(encoding="utf-8"))
            info.update({
                "main_zip_name": stored.get("main_zip_name") or info["main_zip_name"],
                "instructions_html_name": stored.get("instructions_html_name") or info["instructions_html_name"],
                "reused_from_job_id": stored.get("reused_from_job_id"),
            })
        except (OSError, json.JSONDecodeError):
            pass

    return info


def _build_job_status(job_id: str) -> JobStatusResponse:
    output_dir = JOBS_DIR / job_id / "output"
    current_artifacts: List[str] = []
    if output_dir.exists():
        current_artifacts = sorted([p.name for p in output_dir.glob("*") if p.is_file()])

    if USE_INMEMORY_QUEUE:
        job_data = get_local_job(job_id)
        if not job_data:
            raise HTTPException(status_code=404, detail="Job not found")

        artifact_files = sorted(
            set(job_data.get("artifact_files", [])) | set(current_artifacts)
        )

        return JobStatusResponse(
            job_id=job_id,
            status=job_data.get("status", "unknown"),
            message=job_data.get("message"),
            error=job_data.get("error"),
            evaluator_key=job_data.get("evaluator_key"),
            route_type=job_data.get("route_type"),
            routing_reason=job_data.get("routing_reason"),
            artifact_files=artifact_files,
            confidence=job_data.get("confidence"),
        )

    queue = get_queue()
    job = queue.fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    message = job.meta.get("message") if job.meta else None

    if job.is_finished:
        result = job.result or {}
        artifact_files = sorted(
            set(result.get("artifact_files", [])) | set(current_artifacts)
        )
        return JobStatusResponse(
            job_id=job_id,
            status="finished",
            message=message,
            evaluator_key=result.get("evaluator_key"),
            route_type=result.get("route_type"),
            routing_reason=result.get("routing_reason"),
            artifact_files=artifact_files,
            confidence=result.get("confidence"),
        )

    if job.is_failed:
        error_text = str(job.exc_info or "Job failed")
        return JobStatusResponse(job_id=job_id, status="failed", error=error_text)

    status = job.get_status(refresh=True)
    route_file = JOBS_DIR / job_id / "stream.log"
    route_type = None
    routing_reason = None
    if route_file.exists():
        first_line = route_file.read_text(encoding="utf-8", errors="ignore").splitlines()[:1]
        if first_line:
            text = first_line[0]
            if "route_type=" in text:
                parts = text.replace("[router]", "").split(";")
                for part in parts:
                    chunk = part.strip()
                    if chunk.startswith("route_type="):
                        route_type = chunk.split("=", 1)[1]
                    elif chunk.startswith("reason="):
                        routing_reason = chunk.split("=", 1)[1]

    return JobStatusResponse(
        job_id=job_id,
        status=status,
        message=message,
        route_type=route_type,
        routing_reason=routing_reason,
            artifact_files=current_artifacts,
    )


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str) -> JobStatusResponse:
    return _build_job_status(job_id)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    """Cancel a queued job or request stop for a running job."""
    if USE_INMEMORY_QUEUE:
        result = cancel_local_job(job_id)
        if not result:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    queue = get_queue()
    job = queue.fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    status = str(job.get_status(refresh=True))
    if status in {"finished", "failed", "canceled", "stopped"}:
        return {
            "job_id": job_id,
            "status": status,
            "message": "Job is already in a terminal state",
        }

    if status in {"queued", "deferred", "scheduled"}:
        try:
            job.cancel()
        except InvalidJobOperation as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "job_id": job_id,
            "status": "canceled",
            "message": "Job canceled",
        }

    if status == "started":
        try:
            send_stop_job_command(queue.connection, job_id)
        except InvalidJobOperation as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        # Best-effort UX hint while worker transitions state.
        job.meta["message"] = "Stop requested"
        job.save_meta()
        return {
            "job_id": job_id,
            "status": "stopping",
            "message": "Stop requested",
        }

    return {
        "job_id": job_id,
        "status": status,
        "message": f"Cannot cancel job in current state: {status}",
    }


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str) -> dict:
    """Resume a stopped or failed grading job, skipping already-completed students."""
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    
    if not input_dir.exists() or not (input_dir / "submissions.zip").exists():
        raise HTTPException(status_code=400, detail="Job submission files not found - cannot resume")
    
    # Extract completed student names from existing reports
    completed_students = []
    if output_dir.exists():
        for report_file in output_dir.glob("*_grade_report.html"):
            # Extract student name from filename by removing the suffix
            student_name = report_file.name.replace("_grade_report.html", "")
            completed_students.append(student_name)
    
    # Read original job config from stream.log or create minimal config
    stream_log = job_dir / "stream.log"
    evaluator_key = "programming"
    route_type = "code"
    routing_reason = "Resuming interrupted job"
    code_specialty = "csharp"
    
    if stream_log.exists():
        first_line = stream_log.read_text(encoding="utf-8", errors="ignore").splitlines()[:1]
        if first_line:
            text = first_line[0]
            if "evaluator=" in text:
                parts = text.replace("[router]", "").split(";")
                for part in parts:
                    chunk = part.strip()
                    if chunk.startswith("evaluator="):
                        evaluator_key = chunk.split("=", 1)[1]
                    elif chunk.startswith("reason="):
                        routing_reason = chunk.split("=", 1)[1]
                    elif chunk.startswith("specialty="):
                        code_specialty = chunk.split("=", 1)[1].split()[0]
    
    # Check current job state and allow resume for stopped/failed jobs
    if USE_INMEMORY_QUEUE:
        job_info = get_local_job(job_id)
        if job_info and job_info.get("status") not in {"stopped", "canceled", "failed"}:
            return {
                "job_id": job_id,
                "status": job_info.get("status"),
                "message": f"Cannot resume job in state '{job_info.get('status')}'. Job must be stopped, canceled, or failed.",
            }
        # Submit resume with completed students list
        submit_local_job(
            job_id,
            str(input_dir / "submissions.zip"),
            str(input_dir / "instructions.html"),
            str(output_dir),
            evaluator_key=evaluator_key,
            route_type=route_type,
            routing_reason=f"{routing_reason} ({len(completed_students)} previously completed)",
            code_specialty=code_specialty,
            multi_agent_grading=True,
            multi_agent_disagreement_threshold=5.0,
            multi_agent_part_disagreement_threshold=10.0,
            grading_context="",
            completed_students=completed_students,
        )
    else:
        queue = get_queue()
        job = queue.fetch_job(job_id)
        if job:
            status = str(job.get_status(refresh=True))
            if status not in {"finished", "failed", "stopped", "canceled"}:
                return {
                    "job_id": job_id,
                    "status": status,
                    "message": f"Cannot resume job in state '{status}'. Job must be stopped, canceled, or failed.",
                }
        
        queue.enqueue(
            run_grading_job,
            job_id,
            str(input_dir / "submissions.zip"),
            str(input_dir / "instructions.html"),
            str(output_dir),
            evaluator_key,
            route_type,
            f"{routing_reason} ({len(completed_students)} previously completed)",
            code_specialty,
            True,
            5.0,
            10.0,
            "",
            completed_students,
        )
    
    return {
        "job_id": job_id,
        "status": "queued",
        "message": f"Resume queued - {len(completed_students)} students already completed, remaining students will be graded",
        "completed_students_count": len(completed_students),
    }


@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    stream_path = JOBS_DIR / job_id / "stream.log"

    async def event_generator():
        offset = 0
        last_status_json = ""
        settled_ticks = 0

        while True:
            emitted = False

            if stream_path.exists():
                with stream_path.open("r", encoding="utf-8", errors="ignore") as f:
                    f.seek(offset)
                    chunk = f.read()
                    offset = f.tell()

                if chunk:
                    for line in chunk.splitlines():
                        safe_line = line.replace("\r", "")
                        yield f"event: log\ndata: {safe_line}\n\n"
                    emitted = True

            try:
                snapshot = _build_job_status(job_id)
            except HTTPException as exc:
                payload = json.dumps({"detail": exc.detail})
                yield f"event: error\ndata: {payload}\n\n"
                break

            status_json = snapshot.model_dump_json()
            if status_json != last_status_json:
                yield f"event: status\ndata: {status_json}\n\n"
                last_status_json = status_json
                emitted = True

            if snapshot.status in {"finished", "failed", "stopped", "canceled"}:
                settled_ticks += 1
                if settled_ticks >= 2:
                    break
            else:
                settled_ticks = 0

            if not emitted:
                yield ": keepalive\n\n"

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/api/jobs/{job_id}/artifacts")
def list_artifacts(job_id: str) -> dict:
    output_dir = JOBS_DIR / job_id / "output"
    if not output_dir.exists():
        raise HTTPException(status_code=404, detail="Job output not found")

    # Sort by modification time (newest first)
    files: List[str] = sorted(
        [p.name for p in output_dir.glob("*") if p.is_file()],
        key=lambda f: (output_dir / f).stat().st_mtime,
        reverse=True
    )
    return {"job_id": job_id, "files": files}


@app.get("/api/jobs/{job_id}/artifacts/{filename}")
def download_artifact(job_id: str, filename: str) -> FileResponse:
    # Prevent path traversal: strip any directory components from the filename.
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    output_dir = JOBS_DIR / job_id / "output"
    artifact_path = (output_dir / safe_name).resolve()

    # Ensure the resolved path stays within the intended output directory.
    try:
        artifact_path.relative_to(output_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    media_type, _ = mimetypes.guess_type(str(artifact_path))
    return FileResponse(str(artifact_path), media_type=media_type or "application/octet-stream")


@app.get("/api/jobs")
def list_jobs() -> dict:
    """Return a summary list of all jobs found on disk, newest first."""
    if not JOBS_DIR.exists():
        return {"jobs": []}

    jobs = []
    for job_dir in JOBS_DIR.iterdir():
        if not job_dir.is_dir():
            continue

        job_id_str = job_dir.name
        try:
            uuid.UUID(job_id_str)
        except ValueError:
            continue  # Skip non-UUID directories.

        created_at = job_dir.stat().st_mtime

        # Parse routing metadata from the first two lines of stream.log.
        stream_log = job_dir / "stream.log"
        evaluator_key = None
        route_type = None
        if stream_log.exists():
            try:
                for line in stream_log.read_text(encoding="utf-8", errors="ignore").splitlines()[:2]:
                    # Normalize both `;` and `,` delimiters used by different writers.
                    for part in line.replace("[router]", "").replace(",", ";").split(";"):
                        chunk = part.strip()
                        if chunk.startswith("evaluator="):
                            evaluator_key = chunk.split("=", 1)[1].strip()
                        elif chunk.startswith("route_type="):
                            route_type = chunk.split("=", 1)[1].strip()
            except OSError:
                pass

        output_dir = job_dir / "output"
        artifact_count = 0
        if output_dir.exists():
            try:
                artifact_count = sum(1 for p in output_dir.glob("*") if p.is_file())
            except OSError:
                pass

        # Determine job status: prefer live queue data, fall back to disk inference.
        status = "unknown"
        if USE_INMEMORY_QUEUE:
            job_data = get_local_job(job_id_str)
            if job_data:
                status = job_data.get("status", "unknown")
            elif artifact_count > 0:
                status = "finished"
        else:
            try:
                queue = get_queue()
                rq_job = queue.fetch_job(job_id_str)
                if rq_job is not None:
                    if rq_job.is_finished:
                        status = "finished"
                    elif rq_job.is_failed:
                        status = "failed"
                    else:
                        status = str(rq_job.get_status(refresh=True))
                elif artifact_count > 0:
                    status = "finished"
            except Exception:
                if artifact_count > 0:
                    status = "finished"

        jobs.append({
            "job_id": job_id_str,
            "status": status,
            "created_at": created_at,
            "evaluator_key": evaluator_key,
            "route_type": route_type,
            "artifact_count": artifact_count,
        })

    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}/artifacts/{filename}")
def download_artifact(job_id: str, filename: str):
    output_dir = JOBS_DIR / job_id / "output"
    file_path = output_dir / filename
    resolved_output = output_dir.resolve()
    resolved_file = file_path.resolve()

    if resolved_output not in resolved_file.parents:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not resolved_file.exists() or not resolved_file.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    guessed_media_type, _ = mimetypes.guess_type(str(resolved_file))
    media_type = guessed_media_type or "application/octet-stream"

    return FileResponse(
        path=resolved_file,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{resolved_file.name}"'},
    )
