from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .codex_runner import CodexRunError, run_codex


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
MAX_PROMPT_LENGTH = 20_000


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(slots=True)
class Job:
    id: str
    prompt_preview: str
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: str | None = None
    error: str | None = None


class CreateJobRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)


class CreateJobResponse(BaseModel):
    accepted: Literal[True]
    job_id: str
    status: JobStatus


class JobResponse(BaseModel):
    id: str
    prompt_preview: str
    status: JobStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    result: str | None
    error: str | None


app = FastAPI(title="Codex Prompt Runner")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

jobs: dict[str, Job] = {}
jobs_lock = asyncio.Lock()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def preview_prompt(prompt: str) -> str:
    normalized = " ".join(prompt.split())
    return normalized[:140] + ("..." if len(normalized) > 140 else "")


async def execute_job(job_id: str, prompt: str) -> None:
    async with jobs_lock:
        job = jobs[job_id]
        job.status = JobStatus.RUNNING
        job.started_at = utc_now()

    result: str | None = None
    error: str | None = None
    try:
        result = await run_codex(prompt)
    except CodexRunError as exc:
        status = JobStatus.FAILED
        result = exc.output or None
        error = str(exc)
    else:
        status = JobStatus.SUCCEEDED

    async with jobs_lock:
        job = jobs[job_id]
        job.status = status
        job.finished_at = utc_now()
        job.result = result
        job.error = error


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/jobs", response_model=CreateJobResponse, status_code=202)
async def create_job(payload: CreateJobRequest) -> CreateJobResponse:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="Prompt cannot be empty.")

    job_id = uuid4().hex
    job = Job(
        id=job_id,
        prompt_preview=preview_prompt(prompt),
        status=JobStatus.QUEUED,
        created_at=utc_now(),
    )

    async with jobs_lock:
        jobs[job_id] = job

    asyncio.create_task(execute_job(job_id, prompt))

    return CreateJobResponse(accepted=True, job_id=job_id, status=job.status)


@app.get("/api/jobs", response_model=list[JobResponse])
async def list_jobs() -> list[JobResponse]:
    async with jobs_lock:
        newest_first = sorted(jobs.values(), key=lambda item: item.created_at, reverse=True)
        return [JobResponse(**asdict(job)) for job in newest_first]


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    async with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return JobResponse(**asdict(job))
