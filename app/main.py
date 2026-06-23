from __future__ import annotations

import asyncio
import hmac
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import textwrap
import time
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile as StarletteUploadFile

from .codex_runner import CodexRunError, run_codex
from scripts.generate_maycad_shelf import (
    ShelfSceneBuilder,
    generate_three_views,
    remove_chinese_text,
    safe_name,
)


APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"
TASKS_DIR = Path(os.getenv("TASKS_DIR") or PROJECT_DIR / "tasks").expanduser().resolve()
JOBS_DB_PATH = Path(os.getenv("JOBS_DB_PATH") or TASKS_DIR / "jobs.sqlite3").expanduser().resolve()
MAX_PROMPT_LENGTH = 20_000
MAX_IMAGE_COUNT = 8
MAX_IMAGE_BYTES = 15 * 1024 * 1024
IMAGE_CHUNK_BYTES = 1024 * 1024
HIDDEN_GENERATED_FILES = {"codex_prompt.md", "shelf_requirements.md"}
IMAGE_INPUT_DIR = "input_images"
SESSION_COOKIE_NAME = "automaycad_session"
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "123456"
PASSWORD_HASH_ITERATIONS = 210_000
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
DEFAULT_SHELF_DIMENSIONS_MM = {
    "length": 1200.0,
    "depth": 450.0,
    "height": 1800.0,
}
SHELF_KEYWORD_RE = re.compile(r"货架|架子|置物架|shelf|rack|display|storage", re.I)
ALLOWED_IMAGE_TYPES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


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
    owner: str
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: str | None = None
    error: str | None = None
    generated_files: list[str] | None = None


class CreateJobRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class SessionResponse(BaseModel):
    authenticated: Literal[True]
    username: str
    is_admin: bool


class UserResponse(BaseModel):
    username: str
    is_admin: bool
    created_at: str


class CreateJobResponse(BaseModel):
    accepted: Literal[True]
    job_id: str
    task_dir: str
    requirement_path: str
    scene_path: str
    owner: str
    status: JobStatus


class JobResponse(BaseModel):
    id: str
    prompt_preview: str
    task_dir: str
    requirement_path: str
    scene_path: str
    owner: str
    status: JobStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    result: str | None
    error: str | None
    generated_files: list[str] | None


app = FastAPI(title="AutoMaycad 货架任务")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

jobs_lock = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    username: str
    is_admin: bool


@dataclass(frozen=True, slots=True)
class UserAccount:
    username: str
    password_hash: str
    is_admin: bool
    created_at: str


def connect_db() -> sqlite3.Connection:
    JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(JOBS_DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def validate_username(username: str) -> str:
    username = username.strip()
    if not username:
        raise HTTPException(status_code=422, detail="账号不能为空。")
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(status_code=422, detail="账号只能包含字母、数字、下划线、点和短横线，最长 80 个字符。")
    return username


def validate_password(password: str) -> str:
    if not password:
        raise HTTPException(status_code=422, detail="密码不能为空。")
    if len(password) > 200:
        raise HTTPException(status_code=422, detail="密码不能超过 200 个字符。")
    return password


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected_digest = stored_hash.split("$", 3)
        iterations = int(iterations_text)
    except ValueError:
        return False

    if algorithm != "pbkdf2_sha256" or iterations <= 0:
        return False

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(actual_digest, expected_digest)


def user_from_row(row: sqlite3.Row) -> UserAccount:
    return UserAccount(
        username=row["username"],
        password_hash=row["password_hash"],
        is_admin=bool(row["is_admin"]),
        created_at=row["created_at"],
    )


def ensure_users_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    row = connection.execute(
        "SELECT 1 FROM users WHERE username = ?",
        (DEFAULT_ADMIN_USERNAME,),
    ).fetchone()
    if row is None:
        now = utc_now()
        connection.execute(
            """
            INSERT INTO users (username, password_hash, is_admin, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            """,
            (
                DEFAULT_ADMIN_USERNAME,
                hash_password(DEFAULT_ADMIN_PASSWORD),
                now,
                now,
            ),
        )


def get_user_from_db(username: str) -> UserAccount | None:
    with connect_db() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    return user_from_row(row) if row else None


def list_users_from_db() -> list[UserAccount]:
    with connect_db() as connection:
        rows = connection.execute("SELECT * FROM users ORDER BY is_admin DESC, username ASC").fetchall()
    return [user_from_row(row) for row in rows]


def insert_user(username: str, password: str, *, is_admin: bool = False) -> UserAccount:
    now = utc_now()
    with connect_db() as connection:
        try:
            connection.execute(
                """
                INSERT INTO users (username, password_hash, is_admin, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    username,
                    hash_password(password),
                    1 if is_admin else 0,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="账号已存在。") from exc
    return UserAccount(username=username, password_hash="", is_admin=is_admin, created_at=now)


def update_user_password(username: str, password: str) -> None:
    with connect_db() as connection:
        cursor = connection.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE username = ?",
            (hash_password(password), utc_now(), username),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="找不到该账号。")


def session_signature(username: str) -> str:
    return hmac.new(
        SESSION_SECRET.encode("utf-8"),
        username.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def session_cookie_value(username: str) -> str:
    return f"{username}:{session_signature(username)}"


def user_from_session_cookie(cookie_value: str | None) -> AuthenticatedUser | None:
    if not cookie_value or ":" not in cookie_value:
        return None

    username, signature = cookie_value.split(":", 1)
    user = get_user_from_db(username)
    if user is None:
        return None

    if not hmac.compare_digest(signature, session_signature(username)):
        return None

    return AuthenticatedUser(username=username, is_admin=user.is_admin)


def current_user(request: Request) -> AuthenticatedUser:
    user = user_from_session_cookie(request.cookies.get(SESSION_COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录。")
    return user


def can_access_job(user: AuthenticatedUser, job: Job) -> bool:
    return user.is_admin or job.owner == user.username


def require_job_access(user: AuthenticatedUser, job: Job) -> None:
    if not can_access_job(user, job):
        raise HTTPException(status_code=404, detail="找不到该任务。")


def set_session_cookie(response: Response, username: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_cookie_value(username),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, httponly=True, samesite="lax")


def ensure_jobs_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
    }
    if "owner" not in columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN owner TEXT NOT NULL DEFAULT 'admin'")


def init_jobs_db() -> None:
    with connect_db() as connection:
        ensure_users_schema(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                prompt_preview TEXT NOT NULL,
                task_dir TEXT NOT NULL,
                requirement_path TEXT NOT NULL,
                scene_path TEXT NOT NULL,
                owner TEXT NOT NULL DEFAULT 'admin',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                result TEXT,
                error TEXT,
                generated_files TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_jobs_schema(connection)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_owner_created_at ON jobs(owner, created_at)")


def encode_generated_files(files: list[str] | None) -> str:
    return json.dumps(files or [], ensure_ascii=False)


def decode_generated_files(value: str | None) -> list[str]:
    if not value:
        return []

    try:
        files = json.loads(value)
    except json.JSONDecodeError:
        return []

    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, str)]


def job_from_row(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        prompt_preview=row["prompt_preview"],
        task_dir=row["task_dir"],
        requirement_path=row["requirement_path"],
        scene_path=row["scene_path"],
        owner=row["owner"],
        status=JobStatus(row["status"]),
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        result=row["result"],
        error=row["error"],
        generated_files=decode_generated_files(row["generated_files"]),
    )


def insert_job(job: Job) -> None:
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO jobs (
                id,
                prompt_preview,
                task_dir,
                requirement_path,
                scene_path,
                owner,
                status,
                created_at,
                started_at,
                finished_at,
                result,
                error,
                generated_files,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.prompt_preview,
                job.task_dir,
                job.requirement_path,
                job.scene_path,
                job.owner,
                job.status.value,
                job.created_at,
                job.started_at,
                job.finished_at,
                job.result,
                job.error,
                encode_generated_files(job.generated_files),
                utc_now(),
            ),
        )


def save_job(job: Job) -> None:
    with connect_db() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET prompt_preview = ?,
                task_dir = ?,
                requirement_path = ?,
                scene_path = ?,
                owner = ?,
                status = ?,
                created_at = ?,
                started_at = ?,
                finished_at = ?,
                result = ?,
                error = ?,
                generated_files = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                job.prompt_preview,
                job.task_dir,
                job.requirement_path,
                job.scene_path,
                job.owner,
                job.status.value,
                job.created_at,
                job.started_at,
                job.finished_at,
                job.result,
                job.error,
                encode_generated_files(job.generated_files),
                utc_now(),
                job.id,
            ),
        )


def get_job_from_db(job_id: str) -> Job | None:
    with connect_db() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return job_from_row(row) if row else None


def list_jobs_from_db(user: AuthenticatedUser | None = None, owner: str | None = None) -> list[Job]:
    with connect_db() as connection:
        if user is not None and not user.is_admin:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE owner = ? ORDER BY created_at DESC",
                (user.username,),
            ).fetchall()
        elif owner:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE owner = ? ORDER BY created_at DESC",
                (owner,),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    return [job_from_row(row) for row in rows]


def job_exists(job_id: str) -> bool:
    with connect_db() as connection:
        row = connection.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row is not None


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def preview_prompt(prompt: str) -> str:
    normalized = " ".join(prompt.split())
    return normalized[:140] + ("..." if len(normalized) > 140 else "")


def validate_prompt(prompt: str) -> str:
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="需求不能为空。")
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise HTTPException(status_code=422, detail=f"需求不能超过 {MAX_PROMPT_LENGTH} 个字符。")
    return prompt


async def parse_create_job_request(request: Request) -> tuple[str, list[StarletteUploadFile]]:
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        prompt_value = form.get("prompt")
        prompt = prompt_value if isinstance(prompt_value, str) else ""
        uploads = [
            item
            for item in form.getlist("images")
            if isinstance(item, StarletteUploadFile) and item.filename
        ]
        return validate_prompt(prompt), uploads

    try:
        payload = CreateJobRequest.model_validate(await request.json())
    except Exception as exc:
        raise HTTPException(status_code=422, detail="请求体必须包含 prompt。") from exc
    return validate_prompt(payload.prompt), []


def validate_image_uploads(uploads: list[StarletteUploadFile]) -> None:
    if len(uploads) > MAX_IMAGE_COUNT:
        raise HTTPException(status_code=422, detail=f"最多只能上传 {MAX_IMAGE_COUNT} 张图片。")

    for upload in uploads:
        if upload.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=422, detail=f"不支持的图片类型：{upload.content_type or 'unknown'}。")


def safe_upload_name(upload: StarletteUploadFile, index: int) -> str:
    raw_name = Path(upload.filename or "").name
    raw_path = Path(raw_name)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_path.stem).strip("._")
    suffix = raw_path.suffix.lower() or ALLOWED_IMAGE_TYPES.get(upload.content_type or "", ".png")
    allowed_suffixes = set(ALLOWED_IMAGE_TYPES.values()) | {".jpeg"}
    if suffix not in allowed_suffixes:
        suffix = ALLOWED_IMAGE_TYPES.get(upload.content_type or "", ".png")

    return f"{index:02d}_{(stem or 'image')[:80]}{suffix}"


async def save_image_uploads(
    uploads: list[StarletteUploadFile],
    task_dir: Path,
) -> list[Path]:
    validate_image_uploads(uploads)
    if not uploads:
        return []

    image_dir = task_dir / IMAGE_INPUT_DIR
    image_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[Path] = []

    for index, upload in enumerate(uploads, start=1):
        target_path = image_dir / safe_upload_name(upload, index)
        written_bytes = 0
        try:
            with target_path.open("wb") as target:
                while chunk := await upload.read(IMAGE_CHUNK_BYTES):
                    written_bytes += len(chunk)
                    if written_bytes > MAX_IMAGE_BYTES:
                        raise HTTPException(status_code=422, detail=f"单张图片不能超过 {MAX_IMAGE_BYTES // 1024 // 1024} MB。")
                    target.write(chunk)
        finally:
            await upload.close()

        image_paths.append(target_path)

    return image_paths


def image_lines(image_paths: list[Path], task_dir: Path) -> list[str]:
    return [
        f"- {path.relative_to(task_dir).as_posix()} ({path.resolve()})"
        for path in image_paths
    ]


def write_requirement_file(
    *,
    job_id: str,
    prompt: str,
    task_dir: Path,
    requirement_path: Path,
    image_paths: list[Path],
) -> None:
    images_section = "\n".join(image_lines(image_paths, task_dir)) if image_paths else "None"
    requirement_path.write_text(
        textwrap.dedent(
            f"""\
            # Shelf Requirements

            Task ID: {job_id}

            ```text
            {prompt}
            ```

            ## Reference Images

            {images_section}
            """
        ),
        encoding="utf-8",
    )


def create_task_files(job_id: str, prompt: str) -> tuple[Path, Path, Path]:
    task_dir = TASKS_DIR / job_id
    requirement_path = task_dir / "shelf_requirements.md"
    scene_path = task_dir / f"{job_id}.scene"

    task_dir.mkdir(parents=True, exist_ok=False)
    write_requirement_file(
        job_id=job_id,
        prompt=prompt,
        task_dir=task_dir,
        requirement_path=requirement_path,
        image_paths=[],
    )

    return task_dir, requirement_path, scene_path


def build_maycad_prompt(
    *,
    job_id: str,
    user_prompt: str,
    task_dir: Path,
    requirement_path: Path,
    scene_path: Path,
    image_paths: list[Path] | None = None,
) -> str:
    image_paths = image_paths or []
    image_section = (
        "\n".join(image_lines(image_paths, task_dir))
        if image_paths
        else "No reference images were attached."
    )
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
        - The `.scene` file must not contain Chinese characters or Chinese
          punctuation. Use English ASCII text for all metadata, object names,
          labels, comments, CDATA text, and descriptions inside the `.scene`.
        - If the user wrote the requirement in Chinese, translate any
          human-readable `.scene` metadata to English before writing the file.

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

        Reference images attached to the initial prompt:
        {image_section}

        If reference images are attached, inspect them and use them as visual
        requirements alongside the written text.

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
            relative_path = path.relative_to(task_dir).as_posix()
            if relative_path not in HIDDEN_GENERATED_FILES:
                files.append(relative_path)
    return sorted(files)


def scene_files(task_dir: Path) -> list[str]:
    return [item for item in generated_files(task_dir) if item.lower().endswith(".scene")]


def sanitize_scene_file(scene_path: Path) -> None:
    try:
        scene_text = scene_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return

    cleaned_scene_text = remove_chinese_text(scene_text)
    if cleaned_scene_text != scene_text:
        scene_path.write_text(cleaned_scene_text, encoding="utf-8")


def sanitize_scene_files(task_dir: Path) -> None:
    for relative_file in scene_files(task_dir):
        scene_path = (task_dir / relative_file).resolve()
        try:
            scene_path.relative_to(task_dir)
        except ValueError:
            continue

        if scene_path.is_file():
            sanitize_scene_file(scene_path)


def task_activity_snapshot(task_dir: Path) -> tuple[tuple[str, int, int], ...]:
    if not task_dir.exists():
        return ()

    files: list[tuple[str, int, int]] = []
    for path in task_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            relative_path = path.relative_to(task_dir).as_posix()
        except OSError:
            continue
        files.append((relative_path, stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(files))


def task_completion_check(task_dir: Path, stable_seconds: float = 5.0) -> Callable[[], bool]:
    stable_snapshot: tuple[tuple[str, int, int], ...] | None = None
    stable_since = 0.0

    def check() -> bool:
        nonlocal stable_snapshot, stable_since

        if not scene_files(task_dir):
            stable_snapshot = None
            stable_since = 0.0
            return False

        snapshot = task_activity_snapshot(task_dir)
        now = time.monotonic()
        if snapshot != stable_snapshot:
            stable_snapshot = snapshot
            stable_since = now
            return False

        return now - stable_since >= stable_seconds

    return check


def refresh_job_files(job: Job) -> bool:
    files = generated_files(Path(job.task_dir))
    if files == (job.generated_files or []):
        return False

    job.generated_files = files
    return True


def job_to_response(job: Job) -> JobResponse:
    return JobResponse(**asdict(job))


def build_job_archive(task_dir: Path) -> io.BytesIO:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for relative_file in generated_files(task_dir):
            source_path = (task_dir / relative_file).resolve()
            try:
                source_path.relative_to(task_dir)
            except ValueError:
                continue

            if source_path.is_file():
                if source_path.suffix.lower() == ".scene":
                    sanitize_scene_file(source_path)
                zip_file.write(source_path, arcname=relative_file)

    archive.seek(0)
    return archive


def prompt_from_requirement(requirement_path: Path) -> str:
    if not requirement_path.exists():
        return ""

    text = requirement_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"```text\s*(.*?)\s*```", text, re.S)
    return match.group(1).strip() if match else text.strip()


def timestamp_for_path(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()


def recover_jobs_from_disk() -> None:
    if not TASKS_DIR.exists():
        return

    for task_dir in sorted(TASKS_DIR.iterdir()):
        if not task_dir.is_dir() or job_exists(task_dir.name):
            continue

        job_id = task_dir.name
        requirement_path = task_dir / "shelf_requirements.md"
        sanitize_scene_files(task_dir)
        scenes = scene_files(task_dir)
        scene_path = task_dir / scenes[0] if scenes else task_dir / f"{job_id}.scene"
        prompt = prompt_from_requirement(requirement_path)
        files = generated_files(task_dir)
        newest_mtime = max((path.stat().st_mtime for path in task_dir.rglob("*") if path.is_file()), default=task_dir.stat().st_mtime)

        insert_job(Job(
            id=job_id,
            prompt_preview=preview_prompt(prompt) if prompt else f"已恢复任务 {job_id}",
            task_dir=str(task_dir.resolve()),
            requirement_path=str(requirement_path.resolve()),
            scene_path=str(scene_path.resolve()),
            owner="admin",
            status=JobStatus.SUCCEEDED if scenes else JobStatus.FAILED,
            created_at=timestamp_for_path(task_dir),
            finished_at=datetime.fromtimestamp(newest_mtime, UTC).isoformat(),
            generated_files=files,
        ))


def reconcile_jobs_after_startup() -> None:
    for job in list_jobs_from_db():
        task_dir = Path(job.task_dir)
        if task_dir.exists():
            sanitize_scene_files(task_dir)
            refresh_job_files(job)

        if job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
            save_job(job)
            continue

        scenes = scene_files(task_dir)
        job.status = JobStatus.SUCCEEDED if scenes else JobStatus.FAILED
        job.finished_at = job.finished_at or utc_now()
        job.error = None if scenes else "服务重启，后台任务未继续运行。请重新创建任务。"
        refresh_job_files(job)
        save_job(job)


def parse_number_after_label(prompt: str, labels: tuple[str, ...]) -> float | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_pattern})\s*(?:为|约|=|:|：)?\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)?", prompt, re.I)
    return float(match.group(1)) if match else None


def parse_shelf_spec(job_id: str, prompt: str) -> dict | None:
    length = parse_number_after_label(prompt, ("长", "长度", "length", "l"))
    depth = parse_number_after_label(prompt, ("宽", "深", "宽度", "深度", "depth", "width", "d", "w"))
    height = parse_number_after_label(prompt, ("高", "高度", "height", "h"))
    looks_like_shelf = bool(SHELF_KEYWORD_RE.search(prompt))
    defaulted_dimensions: list[str] = []

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
        if not looks_like_shelf:
            return None

        if length is None:
            length = DEFAULT_SHELF_DIMENSIONS_MM["length"]
            defaulted_dimensions.append("length")
        if depth is None:
            depth = DEFAULT_SHELF_DIMENSIONS_MM["depth"]
            defaulted_dimensions.append("depth")
        if height is None:
            height = DEFAULT_SHELF_DIMENSIONS_MM["height"]
            defaulted_dimensions.append("height")

    shelf_match = re.search(r"(?:共|做|要|有)?\s*(\d+)\s*层", prompt)
    load_match = re.search(r"(\d+(?:\.\d+)?)\s*kg", prompt, re.I)
    shelf_count = int(shelf_match.group(1)) if shelf_match else 5
    load_per_shelf = float(load_match.group(1)) if load_match else 40
    assumptions: list[str] = []
    if defaulted_dimensions:
        assumptions.append(
            "Missing "
            + ", ".join(defaulted_dimensions)
            + " defaulted to a practical shelf envelope of 1200 x 450 x 1800 mm."
        )
    if shelf_match is None:
        assumptions.append("Shelf count defaulted to 5 adjustable levels.")
    if load_match is None:
        assumptions.append("Load rating defaulted to 40 kg per shelf.")

    return {
        "project_name": job_id,
        "title": f"AutoMaycad Shelf Task {job_id}",
        "description": "Auto-generated aluminum profile shelf scene from the task requirements.",
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
        "assumptions": assumptions,
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

    initial_assumptions = list(spec.get("assumptions", []))
    builder = ShelfSceneBuilder(spec)
    built = builder.build()
    spec["assumptions"] = [*initial_assumptions, *builder.assumptions]

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
        "assumptions": spec["assumptions"],
        "generator": "local_fallback",
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"本地 MAYCAD 货架生成器已写入 {scene_path}。"


async def execute_job(
    job_id: str,
    user_prompt: str,
    codex_prompt: str,
    image_paths: list[Path] | None = None,
) -> None:
    async with jobs_lock:
        job = get_job_from_db(job_id)
        if job is None:
            return
        job.status = JobStatus.RUNNING
        job.started_at = utc_now()
        task_dir = Path(job.task_dir)
        save_job(job)

    result: str | None = None
    error: str | None = None
    try:
        result = await run_codex(
            codex_prompt,
            image_paths=[str(path) for path in image_paths or []],
            completion_check=task_completion_check(task_dir),
            activity_snapshot=lambda: task_activity_snapshot(task_dir),
        )
    except CodexRunError as exc:
        result = exc.output or None
        if scene_files(task_dir):
            status = JobStatus.SUCCEEDED
            error = None
            result = "\n\n".join(
                item
                for item in (
                    result,
                    f"Codex 返回异常（{exc}），但任务目录中已找到 .scene 文件，按生成成功处理。",
                )
                if item
            )
        else:
            status = JobStatus.FAILED
            error = str(exc)
    else:
        scenes = scene_files(task_dir)
        if scenes:
            status = JobStatus.SUCCEEDED
        else:
            status = JobStatus.FAILED
            error = "Codex 已完成，但任务文件夹中未找到 .scene 文件。"

    if status == JobStatus.FAILED and not scene_files(task_dir):
        fallback_result = run_local_shelf_generator(job_id, user_prompt, task_dir)
        if fallback_result and scene_files(task_dir):
            status = JobStatus.SUCCEEDED
            result = "\n\n".join(item for item in (result, fallback_result) if item)
            error = None

    if scene_files(task_dir):
        sanitize_scene_files(task_dir)

    async with jobs_lock:
        job = get_job_from_db(job_id)
        if job is None:
            return
        job.status = status
        job.finished_at = utc_now()
        job.result = result
        job.error = error
        job.generated_files = generated_files(Path(job.task_dir))
        save_job(job)


@app.on_event("startup")
async def load_recovered_jobs() -> None:
    async with jobs_lock:
        init_jobs_db()
        recover_jobs_from_disk()
        reconcile_jobs_after_startup()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/jobs")
async def jobs_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/jobs/{job_id}")
async def job_page(job_id: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/account")
async def account_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/session", response_model=SessionResponse)
async def get_session(request: Request) -> SessionResponse:
    user = current_user(request)
    return SessionResponse(authenticated=True, username=user.username, is_admin=user.is_admin)


@app.post("/api/login", response_model=SessionResponse)
async def login(payload: LoginRequest, response: Response) -> SessionResponse:
    username = validate_username(payload.username)
    password = validate_password(payload.password)
    user = get_user_from_db(username)
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码不正确。")

    set_session_cookie(response, username)
    return SessionResponse(authenticated=True, username=username, is_admin=user.is_admin)


@app.post("/api/logout", status_code=204)
async def logout(response: Response) -> Response:
    clear_session_cookie(response)
    response.status_code = 204
    return response


@app.post("/api/account/password", status_code=204)
async def change_password(request: Request, payload: ChangePasswordRequest, response: Response) -> Response:
    user = current_user(request)
    current_password = validate_password(payload.current_password)
    new_password = validate_password(payload.new_password)
    account = get_user_from_db(user.username)
    if account is None or not verify_password(current_password, account.password_hash):
        raise HTTPException(status_code=401, detail="当前密码不正确。")

    update_user_password(user.username, new_password)
    response.status_code = 204
    return response


@app.get("/api/users", response_model=list[UserResponse])
async def list_users(request: Request) -> list[UserResponse]:
    user = current_user(request)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="只有管理员可以查看账号。")

    return [
        UserResponse(username=account.username, is_admin=account.is_admin, created_at=account.created_at)
        for account in list_users_from_db()
    ]


@app.post("/api/users", response_model=UserResponse, status_code=201)
async def create_user(request: Request, payload: CreateUserRequest) -> UserResponse:
    user = current_user(request)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="只有管理员可以添加账号。")

    username = validate_username(payload.username)
    password = validate_password(payload.password)
    account = insert_user(username, password)
    return UserResponse(username=account.username, is_admin=account.is_admin, created_at=account.created_at)


@app.post("/api/jobs", response_model=CreateJobResponse, status_code=202)
async def create_job(request: Request) -> CreateJobResponse:
    user = current_user(request)
    prompt, uploads = await parse_create_job_request(request)
    validate_image_uploads(uploads)

    job_id = uuid4().hex
    try:
        task_dir, requirement_path, scene_path = create_task_files(job_id, prompt)
        image_paths = await save_image_uploads(uploads, task_dir)
        write_requirement_file(
            job_id=job_id,
            prompt=prompt,
            task_dir=task_dir,
            requirement_path=requirement_path,
            image_paths=image_paths,
        )
    except OSError as exc:
        if "task_dir" in locals():
            shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"无法创建任务文件夹：{exc}") from exc
    except HTTPException:
        if "task_dir" in locals():
            shutil.rmtree(task_dir, ignore_errors=True)
        raise

    codex_prompt = build_maycad_prompt(
        job_id=job_id,
        user_prompt=prompt,
        task_dir=task_dir,
        requirement_path=requirement_path,
        scene_path=scene_path,
        image_paths=image_paths,
    )
    (task_dir / "codex_prompt.md").write_text(codex_prompt, encoding="utf-8")

    job = Job(
        id=job_id,
        prompt_preview=preview_prompt(prompt),
        task_dir=str(task_dir),
        requirement_path=str(requirement_path),
        scene_path=str(scene_path),
        owner=user.username,
        status=JobStatus.QUEUED,
        created_at=utc_now(),
        generated_files=generated_files(task_dir),
    )

    try:
        async with jobs_lock:
            insert_job(job)
    except sqlite3.Error as exc:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"无法写入任务数据库：{exc}") from exc

    asyncio.create_task(execute_job(job_id, prompt, codex_prompt, image_paths))

    return CreateJobResponse(
        accepted=True,
        job_id=job_id,
        task_dir=str(task_dir),
        requirement_path=str(requirement_path),
        scene_path=str(scene_path),
        owner=job.owner,
        status=job.status,
    )


@app.get("/api/jobs", response_model=list[JobResponse])
async def list_jobs(request: Request, owner: str | None = None) -> list[JobResponse]:
    user = current_user(request)
    owner_filter = validate_username(owner) if owner else None
    if owner_filter and not user.is_admin and owner_filter != user.username:
        raise HTTPException(status_code=403, detail="只有管理员可以查看其他账号的任务。")

    async with jobs_lock:
        jobs = list_jobs_from_db(user, owner_filter)
        responses: list[JobResponse] = []
        for job in jobs:
            if refresh_job_files(job):
                save_job(job)
            responses.append(job_to_response(job))
        return responses


@app.get("/api/jobs/{job_id}/files/{file_path:path}")
async def download_job_file(request: Request, job_id: str, file_path: str) -> FileResponse:
    user = current_user(request)
    async with jobs_lock:
        job = get_job_from_db(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="找不到该任务。")
        require_job_access(user, job)
        task_dir = Path(job.task_dir).resolve()

    requested_path = Path(file_path)
    normalized_file_path = requested_path.as_posix()
    if (
        not file_path
        or requested_path.is_absolute()
        or normalized_file_path in HIDDEN_GENERATED_FILES
    ):
        raise HTTPException(status_code=400, detail="文件路径无效。")

    resolved_path = (task_dir / requested_path).resolve()
    try:
        resolved_path.relative_to(task_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="文件路径无效。") from exc

    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="找不到该文件。")

    is_scene_file = resolved_path.suffix.lower() == ".scene"
    if is_scene_file:
        sanitize_scene_file(resolved_path)

    download_name = f"{job_id}.scene" if is_scene_file else resolved_path.name
    return FileResponse(resolved_path, filename=download_name)


@app.get("/api/jobs/{job_id}/download-all")
async def download_all_job_files(request: Request, job_id: str) -> StreamingResponse:
    user = current_user(request)
    async with jobs_lock:
        job = get_job_from_db(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="找不到该任务。")
        require_job_access(user, job)
        task_dir = Path(job.task_dir).resolve()

    archive = build_job_archive(task_dir)
    filename = f"{job_id}_files.zip"
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/jobs/{job_id}/preview/{file_path:path}")
async def preview_job_file(request: Request, job_id: str, file_path: str) -> FileResponse:
    user = current_user(request)
    async with jobs_lock:
        job = get_job_from_db(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="找不到该任务。")
        require_job_access(user, job)
        task_dir = Path(job.task_dir).resolve()

    requested_path = Path(file_path)
    normalized_file_path = requested_path.as_posix()
    if (
        not file_path
        or requested_path.is_absolute()
        or normalized_file_path in HIDDEN_GENERATED_FILES
        or not normalized_file_path.lower().endswith(".html")
    ):
        raise HTTPException(status_code=400, detail="预览路径无效。")

    resolved_path = (task_dir / requested_path).resolve()
    try:
        resolved_path.relative_to(task_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="预览路径无效。") from exc

    if not resolved_path.is_file() or normalized_file_path not in generated_files(task_dir):
        raise HTTPException(status_code=404, detail="找不到预览文件。")

    return FileResponse(resolved_path, media_type="text/html")


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job(request: Request, job_id: str) -> JobResponse:
    user = current_user(request)
    async with jobs_lock:
        job = get_job_from_db(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="找不到该任务。")
        require_job_access(user, job)
        if refresh_job_files(job):
            save_job(job)
        return job_to_response(job)
