from __future__ import annotations

import asyncio
import html
import json
import mimetypes
from pathlib import Path
import re
import shutil
import tempfile
import uuid
from typing import Dict, List, Optional

import os
import re as _re

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.responses import StreamingResponse
from rq.command import send_stop_job_command
from rq.exceptions import InvalidJobOperation
from rq.job import Job, NoSuchJobError

from .local_jobs import cancel_local_job, get_local_job, submit_local_job
from .queueing import get_queue, get_redis
from .schemas import CreateJobResponse, JobStatusResponse
from .settings import DATA_DIR, DEFAULT_QUEUE_NAME, JOBS_DIR, PARALLEL_QUEUE_NAMES, QUEUE_BASE_NAME, USE_INMEMORY_QUEUE, ensure_directories
from .tasks import run_grading_job
from .grading import register_default_evaluators
from .grading.legacy import AutoGrader
from .grading.registry import get_evaluator, list_evaluators
from .grading.routing import detect_code_specialty, detect_route_type, map_route_to_evaluator
from .auth import authenticate_user, create_session, create_user, delete_session, get_user_by_token, init_auth_db


AUTH_DB_PATH = DATA_DIR / "auth.db"

_ALLOWED_INSTRUCTION_EXTENSIONS = {".html", ".htm", ".txt", ".pdf", ".docx", ".doc"}


def _convert_instructions_to_html(file_bytes: bytes, filename: str) -> str:
    """Convert an uploaded instruction file to an HTML string for storage."""
    ext = Path(filename).suffix.lower()
    if ext in (".html", ".htm"):
        return file_bytes.decode("utf-8", errors="ignore")
    if ext == ".txt":
        content = file_bytes.decode("utf-8", errors="ignore")
        escaped = html.escape(content)
        return f"<html><body><pre>{escaped}</pre></body></html>"
    if ext == ".pdf":
        try:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(file_bytes))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            escaped = html.escape(text)
            return f"<html><body><pre>{escaped}</pre></body></html>"
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {exc}") from exc
    if ext in (".docx", ".doc"):
        try:
            import io
            from docx import Document
            doc = Document(io.BytesIO(file_bytes))
            text = "\n".join(p.text for p in doc.paragraphs)
            escaped = html.escape(text)
            return f"<html><body><pre>{escaped}</pre></body></html>"
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to parse Word document: {exc}") from exc
    raise HTTPException(status_code=400, detail=f"Unsupported instructions file type: {ext}")


def _parse_selected_students(raw_value: str) -> Optional[List[str]]:
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="selected_students must be valid JSON") from exc

    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise HTTPException(status_code=400, detail="selected_students must be a JSON array of strings")

    cleaned = [item.strip() for item in parsed if item.strip()]
    return list(dict.fromkeys(cleaned))


def _derive_assignment_name(job_dir: Path) -> Optional[str]:
    input_dir = job_dir / "input"
    info_path = input_dir / "input_info.json"

    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            if isinstance(info.get("assignment_name"), str) and info.get("assignment_name").strip():
                return info.get("assignment_name").strip()
        except (OSError, json.JSONDecodeError):
            pass

    instructions_path = input_dir / "instructions.html"
    if instructions_path.exists():
        try:
            instructions_html = instructions_path.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r"<h1[^>]*>(.*?)</h1>", instructions_html, flags=re.IGNORECASE | re.DOTALL)
            if match:
                title = re.sub(r"<[^>]+>", "", match.group(1))
                title = html.unescape(title).strip()
                if title:
                    return title
        except OSError:
            pass

    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            zip_name = str(info.get("main_zip_name") or "").strip()
            if zip_name:
                stem = Path(zip_name).stem.replace("_", " ").replace("-", " ").strip()
                if stem:
                    return stem
        except (OSError, json.JSONDecodeError):
            pass

    return None


def _derive_assignment_name_from_html(instructions_html: str) -> Optional[str]:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", instructions_html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    title = re.sub(r"<[^>]+>", "", match.group(1))
    title = html.unescape(title).strip()
    return title or None


def _select_assignment_lane(assignment_name: Optional[str], instructions_html: str) -> str:
    text = f"{assignment_name or ''}\n{instructions_html}".lower()
    if re.search(r"\benum(s)?\b", text):
        return "enum"
    if re.search(r"\barray(s)?\b", text):
        return "array"
    if re.search(r"\bvariable(s)?\b", text):
        return "variables"
    return "general"


def _queue_name_for_lane(lane: str) -> str:
    candidate = f"{QUEUE_BASE_NAME}-{lane}"
    return candidate if candidate in PARALLEL_QUEUE_NAMES else DEFAULT_QUEUE_NAME


def _fetch_rq_job(job_id: str) -> Optional[Job]:
    try:
        return Job.fetch(job_id, connection=get_redis())
    except NoSuchJobError:
        return None


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "").strip()
    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = str(request.query_params.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return token


def _require_user(request: Request) -> dict:
    token = _extract_bearer_token(request)
    user = get_user_by_token(AUTH_DB_PATH, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


def _job_owner_user_id(job_id: str) -> Optional[str]:
    info_path = JOBS_DIR / job_id / "input" / "input_info.json"
    if not info_path.exists():
        return None
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    owner = info.get("owner_user_id")
    if isinstance(owner, str) and owner.strip():
        return owner.strip()
    return None


def _assert_job_access(job_id: str, user: dict) -> None:
    owner_user_id = _job_owner_user_id(job_id)
    if not owner_user_id or owner_user_id != user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to access this job")


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
    init_auth_db(AUTH_DB_PATH)
    register_default_evaluators()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/auth/register")
def register(payload: dict = Body(...)) -> dict:
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    try:
        user = create_user(AUTH_DB_PATH, email=email, password=password)
    except Exception as exc:
        # sqlite unique constraint violation on duplicate email
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(status_code=409, detail="Email already registered") from exc
        raise

    token = create_session(AUTH_DB_PATH, user["id"])
    return {"token": token, "user": user}


@app.post("/api/auth/login")
def login(payload: dict = Body(...)) -> dict:
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    user = authenticate_user(AUTH_DB_PATH, email=email, password=password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_session(AUTH_DB_PATH, user["id"])
    return {"token": token, "user": user}


@app.get("/api/auth/me")
def me(request: Request) -> dict:
    user = _require_user(request)
    return {"user": user}


@app.post("/api/auth/logout")
def logout(request: Request) -> dict:
    token = _extract_bearer_token(request)
    delete_session(AUTH_DB_PATH, token)
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
    request: Request,
    main_zip: Optional[UploadFile] = File(None),
    instructions_html: Optional[UploadFile] = File(None),
    reuse_job_id: str = Form(""),
    selected_students: str = Form(""),
    evaluator_key: str = Form("auto"),
    multi_agent_grading: bool = Form(True),
    multi_agent_disagreement_threshold: float = Form(5.0),
    multi_agent_part_disagreement_threshold: float = Form(10.0),
    grading_context: str = Form(""),
) -> CreateJobResponse:
    user = _require_user(request)
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
        "assignment_name": None,
        "queue_name": DEFAULT_QUEUE_NAME,
        "owner_user_id": user["id"],
        "owner_email": user["email"],
    }
    parsed_selected_students = _parse_selected_students(selected_students)

    reuse_job_id = (reuse_job_id or "").strip()
    if reuse_job_id:
        _assert_job_access(reuse_job_id, user)
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
                if parsed_selected_students is None:
                    parsed_selected_students = source_info.get("selected_students")
            except (OSError, json.JSONDecodeError):
                pass
        input_info["main_zip_name"] = input_info["main_zip_name"] or "submissions.zip"
        input_info["instructions_html_name"] = input_info["instructions_html_name"] or "instructions.html"
    else:
        if not main_zip or not main_zip.filename or not main_zip.filename.lower().endswith(".zip"):
            raise HTTPException(status_code=400, detail="main_zip must be a .zip file")
        if not instructions_html or not instructions_html.filename:
            raise HTTPException(status_code=400, detail="instructions_html is required")
        instr_ext = Path(instructions_html.filename).suffix.lower()
        if instr_ext not in _ALLOWED_INSTRUCTION_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported instructions file type '{instr_ext}'. Allowed: {', '.join(sorted(_ALLOWED_INSTRUCTION_EXTENSIONS))}"
            )

        with zip_path.open("wb") as f:
            shutil.copyfileobj(main_zip.file, f)

        instr_bytes = await instructions_html.read()
        instr_html_content = _convert_instructions_to_html(instr_bytes, instructions_html.filename)
        instructions_path.write_text(instr_html_content, encoding="utf-8")

        input_info["main_zip_name"] = main_zip.filename
        input_info["instructions_html_name"] = instructions_html.filename

    if parsed_selected_students is not None and not parsed_selected_students:
        raise HTTPException(status_code=400, detail="Select at least one student submission")

    input_info["selected_students"] = parsed_selected_students

    input_info_path.write_text(json.dumps(input_info), encoding="utf-8")

    instructions_text = instructions_path.read_text(encoding="utf-8", errors="ignore")
    assignment_name = _derive_assignment_name_from_html(instructions_text)
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

    assignment_lane = _select_assignment_lane(assignment_name, instructions_text)
    queue_name = _queue_name_for_lane(assignment_lane)
    input_info["assignment_name"] = assignment_name
    input_info["queue_name"] = queue_name
    input_info_path.write_text(json.dumps(input_info), encoding="utf-8")

    stream_log = job_dir / "stream.log"
    stream_log.write_text(
        (
            f"[router] route_type={route_type}; evaluator={selected_evaluator}; reason={routing_reason}; "
            f"assignment_lane={assignment_lane}; queue={queue_name}; "
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
            selected_students=parsed_selected_students,
        )
    else:
        queue = get_queue(queue_name)
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
            None,
            parsed_selected_students,
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
def get_job_input_info(job_id: str, request: Request) -> dict:
    user = _require_user(request)
    _assert_job_access(job_id, user)
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
        "selected_students": None,
    }
    input_info_path = input_dir / "input_info.json"
    if input_info_path.exists():
        try:
            stored = json.loads(input_info_path.read_text(encoding="utf-8"))
            info.update({
                "main_zip_name": stored.get("main_zip_name") or info["main_zip_name"],
                "instructions_html_name": stored.get("instructions_html_name") or info["instructions_html_name"],
                "reused_from_job_id": stored.get("reused_from_job_id"),
                "selected_students": stored.get("selected_students"),
            })
        except (OSError, json.JSONDecodeError):
            pass

    return info


@app.post("/api/submissions/preview")
async def preview_submissions(
    request: Request,
    main_zip: Optional[UploadFile] = File(None),
    reuse_job_id: str = Form(""),
) -> dict:
    user = _require_user(request)
    reuse_job_id = (reuse_job_id or "").strip()
    selected_students = None

    if reuse_job_id:
        _assert_job_access(reuse_job_id, user)
        source_input_dir = JOBS_DIR / reuse_job_id / "input"
        zip_path = source_input_dir / "submissions.zip"
        if not zip_path.exists():
            raise HTTPException(status_code=404, detail="Stored submissions zip not found")

        source_info_path = source_input_dir / "input_info.json"
        if source_info_path.exists():
            try:
                stored = json.loads(source_info_path.read_text(encoding="utf-8"))
                selected_students = stored.get("selected_students")
            except (OSError, json.JSONDecodeError):
                selected_students = None

        grader = AutoGrader()
        submissions = sorted(grader.list_submission_students(str(zip_path)))
        return {
            "submissions": submissions,
            "selected_students": [s for s in (selected_students or submissions) if s in submissions],
        }

    if not main_zip or not main_zip.filename or not main_zip.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="main_zip must be a .zip file")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_zip = Path(temp_dir) / "preview.zip"
        with temp_zip.open("wb") as f:
            shutil.copyfileobj(main_zip.file, f)

        grader = AutoGrader()
        submissions = sorted(grader.list_submission_students(str(temp_zip)))
        return {
            "submissions": submissions,
            "selected_students": submissions,
        }


def _build_job_status(job_id: str) -> JobStatusResponse:
    output_dir = JOBS_DIR / job_id / "output"
    def sort_artifacts_by_mtime(artifact_names: List[str]) -> List[str]:
        # Keep newest files first so frontend "Latest first" aligns with actual artifact times.
        unique_names = list(dict.fromkeys(artifact_names))

        def sort_key(name: str) -> tuple[float, str]:
            path = output_dir / name
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            return (mtime, name.lower())

        return sorted(unique_names, key=sort_key, reverse=True)

    current_artifacts: List[str] = []
    if output_dir.exists():
        current_artifacts = sort_artifacts_by_mtime(
            [p.name for p in output_dir.glob("*") if p.is_file()]
        )

    if USE_INMEMORY_QUEUE:
        job_data = get_local_job(job_id)
        if not job_data:
            raise HTTPException(status_code=404, detail="Job not found")

        artifact_files = sort_artifacts_by_mtime(
            [*job_data.get("artifact_files", []), *current_artifacts]
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

    job = _fetch_rq_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    message = job.meta.get("message") if job.meta else None

    if job.is_finished:
        result = job.result or {}
        artifact_files = sort_artifacts_by_mtime(
            [*result.get("artifact_files", []), *current_artifacts]
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
def get_job_status(job_id: str, request: Request) -> JobStatusResponse:
    user = _require_user(request)
    _assert_job_access(job_id, user)
    return _build_job_status(job_id)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    """Cancel a queued job or request stop for a running job."""
    user = _require_user(request)
    _assert_job_access(job_id, user)
    if USE_INMEMORY_QUEUE:
        result = cancel_local_job(job_id)
        if not result:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    job = _fetch_rq_job(job_id)
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
            send_stop_job_command(get_redis(), job_id)
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
def resume_job(job_id: str, request: Request) -> dict:
    """Resume a stopped or failed grading job, skipping already-completed students."""
    user = _require_user(request)
    _assert_job_access(job_id, user)
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    
    if not input_dir.exists() or not (input_dir / "submissions.zip").exists():
        raise HTTPException(status_code=400, detail="Job submission files not found - cannot resume")
    
    # Extract completed student names from existing reports
    completed_students = []
    selected_students = None
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

    input_info_path = input_dir / "input_info.json"
    if input_info_path.exists():
        try:
            stored = json.loads(input_info_path.read_text(encoding="utf-8"))
            selected_students = stored.get("selected_students")
            stored_queue_name = str(stored.get("queue_name") or "").strip()
            queue_name = stored_queue_name if stored_queue_name in PARALLEL_QUEUE_NAMES else DEFAULT_QUEUE_NAME
        except (OSError, json.JSONDecodeError):
            selected_students = None
            queue_name = DEFAULT_QUEUE_NAME
    else:
        queue_name = DEFAULT_QUEUE_NAME
    
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
            selected_students=selected_students,
        )
    else:
        queue = get_queue(queue_name)
        job = _fetch_rq_job(job_id)
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
            selected_students,
        )
    
    return {
        "job_id": job_id,
        "status": "queued",
        "message": f"Resume queued - {len(completed_students)} students already completed, remaining students will be graded",
        "completed_students_count": len(completed_students),
    }


@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request):
    user = _require_user(request)
    _assert_job_access(job_id, user)
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


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, request: Request) -> dict:
    """Permanently delete a job directory from disk."""
    user = _require_user(request)
    # Validate job_id is a UUID to prevent path traversal.
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")

    job_dir = (JOBS_DIR / job_id).resolve()
    try:
        job_dir.relative_to(JOBS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID")

    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    _assert_job_access(job_id, user)

    shutil.rmtree(job_dir)
    return {"deleted": job_id}


@app.delete("/api/jobs")
def delete_jobs_bulk(request: Request, body: dict = Body(...)) -> dict:
    """Permanently delete multiple job directories from disk."""
    user = _require_user(request)
    job_ids = body.get("job_ids", [])
    if not isinstance(job_ids, list) or not job_ids:
        raise HTTPException(status_code=400, detail="job_ids must be a non-empty list")

    deleted = []
    errors = []
    jobs_dir_resolved = JOBS_DIR.resolve()
    for job_id in job_ids:
        try:
            uuid.UUID(str(job_id))
        except (ValueError, AttributeError):
            errors.append({"job_id": job_id, "error": "Invalid job ID"})
            continue

        job_dir = (JOBS_DIR / str(job_id)).resolve()
        try:
            job_dir.relative_to(jobs_dir_resolved)
        except ValueError:
            errors.append({"job_id": job_id, "error": "Invalid job ID"})
            continue

        if not job_dir.exists():
            errors.append({"job_id": job_id, "error": "Not found"})
            continue

        owner_user_id = _job_owner_user_id(str(job_id))
        if not owner_user_id or owner_user_id != user["id"]:
            errors.append({"job_id": job_id, "error": "Forbidden"})
            continue

        shutil.rmtree(job_dir)
        deleted.append(job_id)

    return {"deleted": deleted, "errors": errors}


@app.get("/api/jobs/{job_id}/artifacts")
def list_artifacts(job_id: str, request: Request) -> dict:
    user = _require_user(request)
    _assert_job_access(job_id, user)
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
def download_artifact(job_id: str, filename: str, request: Request):
    user = _require_user(request)
    _assert_job_access(job_id, user)
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
    if artifact_path.suffix.lower() == ".html":
        html_text = artifact_path.read_text(encoding="utf-8", errors="ignore")
        if "id=\"ag-scroll-top-btn\"" not in html_text:
            scroll_top_snippet = """
<style>
#ag-scroll-top-btn {
  position: fixed;
  right: 20px;
  bottom: 20px;
  width: 46px;
  height: 46px;
  border: none;
  border-radius: 999px;
  background: #2563eb;
  color: #ffffff;
  font-size: 22px;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
  box-shadow: 0 12px 24px rgba(15, 23, 42, 0.24);
  opacity: 0;
  pointer-events: none;
  z-index: 9999;
  transition: transform 0.15s ease, background 0.2s ease, opacity 0.2s ease;
}
#ag-scroll-top-btn.ag-visible {
  opacity: 1;
  pointer-events: auto;
}
#ag-scroll-top-btn:hover {
  background: #1d4ed8;
  transform: translateY(-2px);
}
@media (max-width: 900px) {
  #ag-scroll-top-btn { right: 14px; bottom: 14px; width: 42px; height: 42px; font-size: 20px; }
}
</style>
<button id="ag-scroll-top-btn" type="button" aria-label="Scroll to top" title="Scroll to top">↑</button>
<script>
(function () {
  const button = document.getElementById('ag-scroll-top-btn');
  if (!button) return;
  const toggle = () => button.classList.toggle('ag-visible', window.scrollY > 240);
  button.addEventListener('click', function () {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
  window.addEventListener('scroll', toggle, { passive: true });
  toggle();
})();
</script>
"""
            if "</body>" in html_text:
                html_text = html_text.replace("</body>", f"{scroll_top_snippet}\n</body>")
            else:
                html_text += scroll_top_snippet

        # Inject chatbot widget (idempotent)
        if 'id="ag-chatbot-toggle"' not in html_text:
            chatbot_snippet = f"""
<style>
#ag-chatbot-fab {{
  position: fixed;
  right: 20px;
  bottom: 76px;
  width: 46px;
  height: 46px;
  border: none;
  border-radius: 999px;
  background: #16a34a;
  color: #fff;
  font-size: 22px;
  line-height: 1;
  cursor: pointer;
  box-shadow: 0 12px 24px rgba(15,23,42,0.24);
  z-index: 9998;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s ease, transform 0.15s ease;
}}
#ag-chatbot-fab:hover {{ background: #15803d; transform: translateY(-2px); }}
#ag-chatbot-panel {{
  position: fixed;
  right: 20px;
  bottom: 132px;
  width: 370px;
  max-width: calc(100vw - 32px);
  height: 480px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  box-shadow: 0 20px 48px rgba(15,23,42,0.18);
  display: flex;
  flex-direction: column;
  z-index: 9997;
  overflow: hidden;
  transition: opacity 0.2s, transform 0.2s;
}}
#ag-chatbot-panel.ag-hidden {{ opacity: 0; pointer-events: none; transform: translateY(12px); }}
#ag-chat-header {{
  background: #16a34a;
  color: #fff;
  padding: 12px 16px;
  font-weight: 600;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}}
#ag-chat-header span {{ flex: 1; }}
#ag-chat-close {{
  background: none;
  border: none;
  color: #fff;
  font-size: 18px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}}
#ag-chat-messages {{
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 13px;
}}
.ag-msg {{ padding: 8px 12px; border-radius: 12px; max-width: 88%; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }}
.ag-msg-user {{ background: #16a34a; color: #fff; align-self: flex-end; border-bottom-right-radius: 4px; }}
.ag-msg-assistant {{ background: #f1f5f9; color: #0f172a; align-self: flex-start; border-bottom-left-radius: 4px; }}
.ag-msg-typing {{ color: #64748b; font-style: italic; }}
#ag-chat-footer {{
  padding: 10px 12px;
  border-top: 1px solid #e2e8f0;
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}}
#ag-chat-input {{
  flex: 1;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
  resize: none;
  height: 38px;
  outline: none;
  font-family: inherit;
}}
#ag-chat-input:focus {{ border-color: #16a34a; box-shadow: 0 0 0 2px #bbf7d0; }}
#ag-chat-send {{
  background: #16a34a;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0 14px;
  cursor: pointer;
  font-size: 16px;
  transition: background 0.2s;
}}
#ag-chat-send:disabled {{ background: #94a3b8; cursor: not-allowed; }}
#ag-chat-send:not(:disabled):hover {{ background: #15803d; }}
@media (max-width: 900px) {{
  #ag-chatbot-fab {{ right: 14px; bottom: 68px; width: 42px; height: 42px; font-size: 18px; }}
  #ag-chatbot-panel {{ right: 8px; bottom: 120px; width: calc(100vw - 16px); height: 420px; }}
}}
</style>
<button id="ag-chatbot-toggle" id2="ag-chatbot-fab" type="button" aria-label="Open grading assistant" title="Ask the grading assistant">💬</button>
<div id="ag-chatbot-panel" class="ag-hidden" role="dialog" aria-label="Grading assistant chat">
  <div id="ag-chat-header">
    <span>🎓 Grading Assistant</span>
    <button id="ag-chat-close" aria-label="Close chat" title="Close">✕</button>
  </div>
  <div id="ag-chat-messages" aria-live="polite"></div>
  <div id="ag-chat-footer">
    <textarea id="ag-chat-input" placeholder="Ask about this report…" rows="1" aria-label="Your question"></textarea>
    <button id="ag-chat-send" title="Send">➤</button>
  </div>
</div>
<script>
(function () {{
  const toggle = document.getElementById('ag-chatbot-toggle');
  const panel  = document.getElementById('ag-chatbot-panel');
  const closeBtn = document.getElementById('ag-chat-close');
  const messages = document.getElementById('ag-chat-messages');
  const input = document.getElementById('ag-chat-input');
  const sendBtn = document.getElementById('ag-chat-send');

  // Fix: reuse the button for the FAB role (id attr can only appear once; use the button directly)
  toggle.style.cssText = toggle.style.cssText;  // noop flush
  // Copy FAB styles to the toggle button since CSS targets #ag-chatbot-fab
  toggle.id = 'ag-chatbot-fab';

  let history = [];
  let busy = false;

    const token = new URLSearchParams(window.location.search).get('token');
    const tokenSuffix = token ? ('?token=' + encodeURIComponent(token)) : '';
    const CHAT_URL = window.location.origin + '/api/jobs/{job_id}/artifacts/' + encodeURIComponent('{safe_name}') + '/chat' + tokenSuffix;

  function addMsg(role, text) {{
    const div = document.createElement('div');
    div.className = 'ag-msg ' + (role === 'user' ? 'ag-msg-user' : 'ag-msg-assistant');
    div.textContent = text;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }}

  toggle.addEventListener('click', function () {{
    panel.classList.toggle('ag-hidden');
    if (!panel.classList.contains('ag-hidden')) {{
      input.focus();
      if (history.length === 0) {{
        addMsg('assistant', 'Hi! I can answer questions about this grade report — the scores, feedback, or assignment requirements. What would you like to know?');
      }}
    }}
  }});

  closeBtn.addEventListener('click', function () {{
    panel.classList.add('ag-hidden');
  }});

  async function sendMessage() {{
    const text = input.value.trim();
    if (!text || busy) return;
    busy = true;
    sendBtn.disabled = true;
    input.value = '';

    addMsg('user', text);
    history.push({{ role: 'user', content: text }});

    const typingDiv = document.createElement('div');
    typingDiv.className = 'ag-msg ag-msg-assistant ag-msg-typing';
    typingDiv.textContent = 'Thinking…';
    messages.appendChild(typingDiv);
    messages.scrollTop = messages.scrollHeight;

    try {{
      const resp = await fetch(CHAT_URL, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ messages: history }}),
      }});

      if (!resp.ok) {{
        typingDiv.className = 'ag-msg ag-msg-assistant';
        typingDiv.textContent = 'Error: ' + resp.status + ' ' + resp.statusText;
        busy = false;
        sendBtn.disabled = false;
        return;
      }}

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let reply = '';
      typingDiv.className = 'ag-msg ag-msg-assistant';
      typingDiv.textContent = '';

      while (true) {{
        const {{ done, value }} = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, {{ stream: true }});
        reply += chunk;
        typingDiv.textContent = reply;
        messages.scrollTop = messages.scrollHeight;
      }}

      history.push({{ role: 'assistant', content: reply }});
    }} catch (err) {{
      typingDiv.className = 'ag-msg ag-msg-assistant';
      typingDiv.textContent = 'Network error: ' + err.message;
    }}

    busy = false;
    sendBtn.disabled = false;
    input.focus();
  }}

  sendBtn.addEventListener('click', sendMessage);
  input.addEventListener('keydown', function (e) {{
    if (e.key === 'Enter' && !e.shiftKey) {{
      e.preventDefault();
      sendMessage();
    }}
  }});
}})();
</script>
"""
            if "</body>" in html_text:
                html_text = html_text.replace("</body>", f"{chatbot_snippet}\n</body>")
            else:
                html_text += chatbot_snippet

        return HTMLResponse(content=html_text)

    return FileResponse(str(artifact_path), media_type=media_type or "application/octet-stream")


# ---------------------------------------------------------------------------
# Report Chatbot endpoint
# ---------------------------------------------------------------------------

def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities for use as plain-text LLM context."""
    import html as _html_mod
    no_tags = _re.sub(r"<[^>]+>", " ", text)
    return _html_mod.unescape(_re.sub(r" {2,}", " ", no_tags)).strip()


@app.post("/api/jobs/{job_id}/artifacts/{filename}/chat")
async def artifact_chat(
    request: Request,
    job_id: str,
    filename: str,
    payload: dict = Body(...),
):
    """Stream a chat reply using the report HTML as context."""
    user = _require_user(request)
    _assert_job_access(job_id, user)
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    output_dir = JOBS_DIR / job_id / "output"
    artifact_path = (output_dir / safe_name).resolve()
    try:
        artifact_path.relative_to(output_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    html_text = artifact_path.read_text(encoding="utf-8", errors="ignore")
    report_text = _strip_html(html_text)
    # Limit context to avoid excessive token usage (~120 k chars ≈ ~30 k tokens)
    if len(report_text) > 120_000:
        report_text = report_text[:120_000] + "\n[...report truncated for context length...]"

    messages: list = payload.get("messages", [])
    if not messages or not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages array is required")

    # Validate message structure (only allow role/content string pairs)
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") not in {"user", "assistant"} or not isinstance(msg.get("content"), str):
            raise HTTPException(status_code=400, detail="Invalid message format")

    system_prompt = (
        "You are a helpful grading assistant. "
        "The professor or teaching assistant has opened a student grade report and may have questions "
        "about the grading, the feedback, or the assignment requirements. "
        "Answer clearly and concisely based on the report content provided. "
        "If a question cannot be answered from the report, say so honestly.\n\n"
        f"=== GRADE REPORT ===\n{report_text}\n=== END OF REPORT ==="
    )

    model_provider = os.getenv("MODEL_PROVIDER", "openai").strip().lower()
    model_name = os.getenv("MODEL_NAME", "gpt-4o-mini").strip()
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    custom_endpoint = os.getenv("CUSTOM_ENDPOINT", "http://localhost:11434").rstrip("/")

    from openai import AsyncOpenAI, AsyncStream
    from openai.types.chat import ChatCompletionChunk

    if model_provider in {"custom", "ollama", "local"}:
        client = AsyncOpenAI(
            api_key="ollama",
            base_url=f"{custom_endpoint}/v1",
        )
    else:
        if not openai_api_key:
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")
        client = AsyncOpenAI(api_key=openai_api_key)

    openai_messages = [{"role": "system", "content": system_prompt}] + [
        {"role": m["role"], "content": m["content"]} for m in messages
    ]

    async def generate():
        try:
            stream: AsyncStream[ChatCompletionChunk] = await client.chat.completions.create(
                model=model_name,
                messages=openai_messages,
                stream=True,
                max_completion_tokens=1024,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except Exception as exc:  # noqa: BLE001
            yield f"\n\n[Error contacting LLM: {exc}]"

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")


@app.get("/api/jobs")
def list_jobs(request: Request) -> dict:
    """Return a summary list of all jobs found on disk, newest first."""
    user = _require_user(request)
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

        owner_user_id = _job_owner_user_id(job_id_str)
        if not owner_user_id or owner_user_id != user["id"]:
            continue

        created_at = job_dir.stat().st_mtime

        # Parse routing metadata from the first two lines of stream.log.
        stream_log = job_dir / "stream.log"
        evaluator_key = None
        route_type = None
        assignment_name = _derive_assignment_name(job_dir)
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
                artifact_count = sum(
                    1 for p in output_dir.glob("*") if p.is_file() and p.suffix.lower() == ".html"
                )
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
                rq_job = _fetch_rq_job(job_id_str)
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
            "assignment_name": assignment_name,
            "evaluator_key": evaluator_key,
            "route_type": route_type,
            "artifact_count": artifact_count,
        })

    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}/artifacts/{filename}")
def download_artifact(job_id: str, filename: str, request: Request):
    user = _require_user(request)
    _assert_job_access(job_id, user)
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

    if resolved_file.suffix.lower() == ".html":
        html_text = resolved_file.read_text(encoding="utf-8", errors="ignore")
        if "id=\"ag-scroll-top-btn\"" not in html_text:
            scroll_top_snippet = """
<style>
#ag-scroll-top-btn {
    position: fixed;
    right: 20px;
    bottom: 20px;
    width: 46px;
    height: 46px;
    border: none;
    border-radius: 999px;
    background: #2563eb;
    color: #ffffff;
    font-size: 22px;
    font-weight: 700;
    line-height: 1;
    cursor: pointer;
    box-shadow: 0 12px 24px rgba(15, 23, 42, 0.24);
    opacity: 0;
    pointer-events: none;
    z-index: 9999;
    transition: transform 0.15s ease, background 0.2s ease, opacity 0.2s ease;
}
#ag-scroll-top-btn.ag-visible {
    opacity: 1;
    pointer-events: auto;
}
#ag-scroll-top-btn:hover {
    background: #1d4ed8;
    transform: translateY(-2px);
}
@media (max-width: 900px) {
    #ag-scroll-top-btn { right: 14px; bottom: 14px; width: 42px; height: 42px; font-size: 20px; }
}
</style>
<button id="ag-scroll-top-btn" type="button" aria-label="Scroll to top" title="Scroll to top">↑</button>
<script>
(function () {
    const button = document.getElementById('ag-scroll-top-btn');
    if (!button) return;
    const toggle = () => button.classList.toggle('ag-visible', window.scrollY > 240);
    button.addEventListener('click', function () {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    window.addEventListener('scroll', toggle, { passive: true });
    toggle();
})();
</script>
"""
        if "</body>" in html_text:
            html_text = html_text.replace("</body>", f"{scroll_top_snippet}\n</body>")
        else:
            html_text += scroll_top_snippet
    return HTMLResponse(
        content=html_text,
        headers={"Content-Disposition": f'inline; filename="{resolved_file.name}"'},
    )

    return FileResponse(
        path=resolved_file,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{resolved_file.name}"'},
    )
