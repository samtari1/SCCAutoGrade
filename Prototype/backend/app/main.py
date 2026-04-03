from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
import shutil
import uuid
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse

from .local_jobs import get_local_job, submit_local_job
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
    main_zip: UploadFile = File(...),
    instructions_html: UploadFile = File(...),
    evaluator_key: str = Form("auto"),
    multi_agent_grading: bool = Form(True),
    multi_agent_disagreement_threshold: float = Form(5.0),
    multi_agent_part_disagreement_threshold: float = Form(10.0),
) -> CreateJobResponse:
    if not main_zip.filename or not main_zip.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="main_zip must be a .zip file")

    if not instructions_html.filename or not instructions_html.filename.lower().endswith(".html"):
        raise HTTPException(status_code=400, detail="instructions_html must be a .html file")
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

    with zip_path.open("wb") as f:
        shutil.copyfileobj(main_zip.file, f)
    with instructions_path.open("wb") as f:
        shutil.copyfileobj(instructions_html.file, f)

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
            f"part_disagreement_threshold={multi_agent_part_disagreement_threshold}\n"
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

            if snapshot.status in {"finished", "failed"}:
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

    files: List[str] = sorted([p.name for p in output_dir.glob("*") if p.is_file()])
    return {"job_id": job_id, "files": files}


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
