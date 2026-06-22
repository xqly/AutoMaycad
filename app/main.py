from __future__ import annotations

import asyncio
import json
import os
import re
import textwrap
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
from scripts.generate_maycad_shelf import ShelfSceneBuilder, generate_three_views, safe_name


APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"
TASKS_DIR = Path(os.getenv("TASKS_DIR") or PROJECT_DIR / "tasks").expanduser().resolve()
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
    task_dir: str
    requirement_path: str
    scene_path: str
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: str | None = None
    error: str | None = None
    generated_files: list[str] | None = None


class CreateJobRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)


class CreateJobResponse(BaseModel):
    accepted: Literal[True]
    job_id: str
    task_dir: str
    requirement_path: str
    scene_path: str
    status: JobStatus


class JobResponse(BaseModel):
    id: str
    prompt_preview: str
    task_dir: str
    requirement_path: str
    scene_path: str
    status: JobStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    result: str | None
    error: str | None
    generated_files: list[str] | None


app = FastAPI(title="AutoMaycad Shelf Tasks")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

jobs: dict[str, Job] = {}
jobs_lock = asyncio.Lock()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def preview_prompt(prompt: str) -> str:
    normalized = " ".join(prompt.split())
    return normalized[:140] + ("..." if len(normalized) > 140 else "")


def create_task_files(job_id: str, prompt: str) -> tuple[Path, Path, Path]:
    task_dir = TASKS_DIR / job_id
    requirement_path = task_dir / "shelf_requirements.md"
    scene_path = task_dir / f"{job_id}.scene"

    task_dir.mkdir(parents=True, exist_ok=False)
    requirement_path.write_text(
        textwrap.dedent(
            f"""\
            # Shelf Requirements

            Task ID: {job_id}

            ```text
            {prompt}
            ```
            """
        ),
        encoding="utf-8",
    )

    return task_dir, requirement_path, scene_path


def build_maycad_prompt(
    *,
    job_id: str,
    user_prompt: str,
    task_dir: Path,
    requirement_path: Path,
    scene_path: Path,
) -> str:
    return textwrap.dedent(
        f"""\
        You are working on AutoMaycad task {job_id}.

        The user input is a shelf/rack requirement. Generate a MAYCAD `.scene`
        engineering file for the described shelf.

        Required output:
        - Write the final MAYCAD scene file to exactly this path:
          {scene_path}
        - Keep all generated supporting files inside this task folder:
          {task_dir}
        - Do not write generated project files outside the task folder.
        - The original requirement is saved here:
          {requirement_path}

        MAYCAD modeling instructions:
        - Use the installed MAYCAD workflow if available: normalize the shelf
          requirements, create/check a compact three-view drawing, then generate
          the `.scene`.
        - For supermarket, convenience-store, display, storage, or adjustable
          shelf tasks, prefer this repository helper:
          scripts/generate_maycad_shelf.py
        - Treat dimensions as finished outer dimensions unless the user clearly
          says otherwise.
        - Use X = length, Y = depth, Z = height.
        - If details are missing, make practical assumptions and write them to a
          summary file in the task folder.
        - Default to 4040 aluminum profile and 18 mm MDF/wood panels when the
          user does not specify materials.
        - At the end, report the scene path and the generated files.

        User shelf/rack requirement:
        {user_prompt}
        """
    ).strip()


def generated_files(task_dir: Path) -> list[str]:
    if not task_dir.exists():
        return []

    files: list[str] = []
    for path in task_dir.rglob("*"):
        if path.is_file():
            files.append(path.relative_to(task_dir).as_posix())
    return sorted(files)


def scene_files(task_dir: Path) -> list[str]:
    return [item for item in generated_files(task_dir) if item.lower().endswith(".scene")]


def parse_number_after_label(prompt: str, labels: tuple[str, ...]) -> float | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_pattern})\s*(?:为|约|=|:|：)?\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)?", prompt, re.I)
    return float(match.group(1)) if match else None


def parse_shelf_spec(job_id: str, prompt: str) -> dict | None:
    length = parse_number_after_label(prompt, ("长", "长度", "length", "l"))
    depth = parse_number_after_label(prompt, ("宽", "深", "宽度", "深度", "depth", "width", "d", "w"))
    height = parse_number_after_label(prompt, ("高", "高度", "height", "h"))

    if length is None or depth is None or height is None:
        dimension_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)?\s*[xX×*]\s*"
            r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)?\s*[xX×*]\s*"
            r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)?",
            prompt,
        )
        if dimension_match:
            length = length or float(dimension_match.group(1))
            depth = depth or float(dimension_match.group(2))
            height = height or float(dimension_match.group(3))

    if length is None or depth is None or height is None:
        return None

    shelf_match = re.search(r"(?:共|做|要|有)?\s*(\d+)\s*层", prompt)
    load_match = re.search(r"(\d+(?:\.\d+)?)\s*kg", prompt, re.I)
    shelf_count = int(shelf_match.group(1)) if shelf_match else 5
    load_per_shelf = float(load_match.group(1)) if load_match else 40

    return {
        "project_name": job_id,
        "title": f"AutoMaycad Shelf Task {job_id}",
        "description": "Auto-generated aluminum-profile shelf scene from task prompt.",
        "finished_mm": {
            "length": length,
            "depth": depth,
            "height": height,
        },
        "shelf_count": shelf_count,
        "bay_count": 2 if length >= 900 else 1,
        "load_per_shelf_kg": load_per_shelf,
        "profile_size_mm": 40,
        "panel_thickness_mm": 18,
        "include_diagonal_bracing": True,
    }


def run_local_shelf_generator(job_id: str, prompt: str, task_dir: Path) -> str | None:
    spec = parse_shelf_spec(job_id, prompt)
    if spec is None:
        return None

    project_name = safe_name(spec["project_name"])
    title = spec["title"]
    description = spec["description"]
    spec_path = task_dir / "shelf_spec.json"
    scene_path = task_dir / f"{project_name}.scene"
    html_path = task_dir / f"{project_name}_three_views.html"
    summary_path = task_dir / f"{project_name}_summary.json"

    builder = ShelfSceneBuilder(spec)
    built = builder.build()
    spec["assumptions"] = builder.assumptions

    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    scene_path.write_text(builder.scene_xml(title, description), encoding="utf-8")
    html_path.write_text(generate_three_views(spec, built, title), encoding="utf-8")
    summary = {
        "project_name": project_name,
        "scene": str(scene_path),
        "three_views": str(html_path),
        "objects": len(builder.objects),
        "profiles": builder.profile_count,
        "panels": builder.panel_count,
        "built": built,
        "assumptions": builder.assumptions,
        "generator": "local_fallback",
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"Local MAYCAD shelf generator wrote {scene_path}."


async def execute_job(job_id: str, user_prompt: str, codex_prompt: str) -> None:
    async with jobs_lock:
        job = jobs[job_id]
        job.status = JobStatus.RUNNING
        job.started_at = utc_now()
        task_dir = Path(job.task_dir)

    result: str | None = None
    error: str | None = None
    try:
        result = await run_codex(codex_prompt)
    except CodexRunError as exc:
        status = JobStatus.FAILED
        result = exc.output or None
        error = str(exc)
    else:
        scenes = scene_files(task_dir)
        if scenes:
            status = JobStatus.SUCCEEDED
        else:
            status = JobStatus.FAILED
            error = "Codex completed, but no .scene file was found in the task folder."

    if status == JobStatus.FAILED and not scene_files(task_dir):
        fallback_result = run_local_shelf_generator(job_id, user_prompt, task_dir)
        if fallback_result and scene_files(task_dir):
            status = JobStatus.SUCCEEDED
            result = "\n\n".join(item for item in (result, fallback_result) if item)
            error = None

    async with jobs_lock:
        job = jobs[job_id]
        job.status = status
        job.finished_at = utc_now()
        job.result = result
        job.error = error
        job.generated_files = generated_files(Path(job.task_dir))


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/jobs", response_model=CreateJobResponse, status_code=202)
async def create_job(payload: CreateJobRequest) -> CreateJobResponse:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="Prompt cannot be empty.")

    job_id = uuid4().hex
    try:
        task_dir, requirement_path, scene_path = create_task_files(job_id, prompt)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not create task folder: {exc}") from exc

    codex_prompt = build_maycad_prompt(
        job_id=job_id,
        user_prompt=prompt,
        task_dir=task_dir,
        requirement_path=requirement_path,
        scene_path=scene_path,
    )
    (task_dir / "codex_prompt.md").write_text(codex_prompt, encoding="utf-8")

    job = Job(
        id=job_id,
        prompt_preview=preview_prompt(prompt),
        task_dir=str(task_dir),
        requirement_path=str(requirement_path),
        scene_path=str(scene_path),
        status=JobStatus.QUEUED,
        created_at=utc_now(),
        generated_files=generated_files(task_dir),
    )

    async with jobs_lock:
        jobs[job_id] = job

    asyncio.create_task(execute_job(job_id, prompt, codex_prompt))

    return CreateJobResponse(
        accepted=True,
        job_id=job_id,
        task_dir=str(task_dir),
        requirement_path=str(requirement_path),
        scene_path=str(scene_path),
        status=job.status,
    )


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
