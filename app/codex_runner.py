from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MAX_RUNTIME_SECONDS = 1800
DEFAULT_IDLE_TIMEOUT_SECONDS = 600
DEFAULT_CODEX_ARGS = "exec --skip-git-repo-check --sandbox workspace-write"
LEGACY_CODEX_HOME = Path("C:/Users/xqly/.codex")
DEFAULT_OUTPUT_LIMIT_CHARS = 50_000
POLL_SECONDS = 1.0
GRACEFUL_SHUTDOWN_SECONDS = 10.0
STREAM_LOG_CHARS = 4000

logger = logging.getLogger("automaycad.codex")


@dataclass(frozen=True, slots=True)
class CodexTokenUsage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class CodexRunResult:
    output: str
    token_usage: CodexTokenUsage | None = None


class CodexRunError(RuntimeError):
    """Raised when the Codex subprocess cannot be started or exits unsuccessfully."""

    def __init__(
        self,
        message: str,
        output: str = "",
        token_usage: CodexTokenUsage | None = None,
    ) -> None:
        super().__init__(message)
        self.output = output
        self.token_usage = token_usage


def _positive_int_from_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        return max(1, int(raw_value))
    except ValueError:
        return default


def _max_runtime_seconds() -> int:
    raw_value = os.getenv("CODEX_MAX_RUNTIME_SECONDS") or os.getenv("CODEX_TIMEOUT_SECONDS")
    if raw_value is None:
        return DEFAULT_MAX_RUNTIME_SECONDS

    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_MAX_RUNTIME_SECONDS


def _idle_timeout_seconds() -> int | None:
    raw_value = os.getenv("CODEX_IDLE_TIMEOUT_SECONDS")
    if raw_value is None:
        return DEFAULT_IDLE_TIMEOUT_SECONDS

    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_IDLE_TIMEOUT_SECONDS

    return value if value > 0 else None


def _output_limit_chars() -> int:
    return max(1_000, _positive_int_from_env("CODEX_OUTPUT_LIMIT_CHARS", DEFAULT_OUTPUT_LIMIT_CHARS))


def _working_directory() -> Path:
    configured = os.getenv("CODEX_WORKDIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.cwd()


def _bundled_codex() -> tuple[str | None, str | None]:
    try:
        from codex_cli_bin import bundled_codex_path, bundled_path_dir
    except ImportError:
        return None, None

    path_dir = bundled_path_dir()
    return str(bundled_codex_path()), str(path_dir) if path_dir else None


def _codex_command() -> str:
    configured = os.getenv("CODEX_COMMAND")
    if configured:
        return configured

    bundled_command, _ = _bundled_codex()
    if bundled_command:
        return bundled_command

    return "codex"


def _codex_environment() -> dict[str, str]:
    env = os.environ.copy()
    _, bundled_path_dir = _bundled_codex()
    if bundled_path_dir:
        env["PATH"] = f"{bundled_path_dir}{os.pathsep}{env.get('PATH', '')}"
    codex_home = env.get("CODEX_HOME", "")
    use_candidate_home = not codex_home or "CodexSandboxOffline" in codex_home
    if codex_home and not use_candidate_home and not Path(codex_home).expanduser().exists():
        logger.warning("codex.invalid_home_ignored path=%s", codex_home)
        env.pop("CODEX_HOME", None)
        use_candidate_home = True
    if use_candidate_home:
        home = Path.home() / ".codex"
        for candidate in (home, LEGACY_CODEX_HOME):
            if candidate.exists():
                env["CODEX_HOME"] = str(candidate)
                break
    return env


def _codex_sessions_dir(env: dict[str, str]) -> Path | None:
    codex_home = env.get("CODEX_HOME")
    if not codex_home:
        return None
    return Path(codex_home).expanduser() / "sessions"


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes", "on"}


def _without_legacy_approval_args(args: list[str]) -> list[str]:
    normalized_args: list[str] = []
    index = 0
    removed = False

    while index < len(args):
        arg = args[index]
        if arg.startswith("--ask-for-approval="):
            removed = True
            index += 1
            continue
        if arg in {"--ask-for-approval", "-a"}:
            removed = True
            index += 1
            if index < len(args) and not args[index].startswith("-"):
                index += 1
            continue

        normalized_args.append(arg)
        index += 1

    if removed:
        logger.warning("codex.legacy_approval_args_removed")
    return normalized_args


def _with_default_exec_permissions(args: list[str]) -> list[str]:
    if not args or args[0] != "exec":
        return args

    args = _without_legacy_approval_args(args)
    has_full_access = any(
        arg in {"--dangerously-bypass-approvals-and-sandbox", "--yolo", "--full-auto"}
        for arg in args
    )
    has_sandbox = any(arg in {"--sandbox", "-s"} or arg.startswith("--sandbox=") for arg in args)

    normalized_args = list(args)
    if not has_sandbox and not has_full_access:
        normalized_args.extend(["--sandbox", "workspace-write"])
    return normalized_args


def _log_text_chunk(stream_name: str, chunk: bytes) -> None:
    if not _truthy_env("AUTOMAYCAD_LOG_CODEX_STREAMS"):
        return

    text = chunk.decode("utf-8", errors="replace").rstrip()
    if not text:
        return

    if len(text) > STREAM_LOG_CHARS:
        omitted = len(text) - STREAM_LOG_CHARS
        text = f"{text[:STREAM_LOG_CHARS]}\n[stream chunk truncated: omitted {omitted} chars]"
    logger.debug("codex.%s %s", stream_name, text)


def _command(image_paths: list[str] | None = None) -> list[str]:
    command = _codex_command()
    args_json = os.getenv("CODEX_ARGS_JSON")
    if args_json:
        try:
            parsed_args = json.loads(args_json)
        except json.JSONDecodeError as exc:
            raise CodexRunError("CODEX_ARGS_JSON 不是有效的 JSON。") from exc
        if not isinstance(parsed_args, list) or not all(isinstance(item, str) for item in parsed_args):
            raise CodexRunError("CODEX_ARGS_JSON 必须是字符串数组。")
        args = parsed_args
    else:
        args = shlex.split(os.getenv("CODEX_ARGS", DEFAULT_CODEX_ARGS))

    args = _with_default_exec_permissions(args)

    image_args: list[str] = []
    for image_path in image_paths or []:
        image_args.extend(["--image", image_path])

    return [command, *args, *image_args]


async def _write_prompt(
    stdin: asyncio.StreamWriter | None,
    prompt: str,
) -> None:
    if stdin is None:
        return

    with contextlib.suppress(BrokenPipeError, ConnectionResetError, RuntimeError):
        stdin.write(prompt.encode("utf-8"))
        await stdin.drain()
    stdin.close()
    with contextlib.suppress(BrokenPipeError, ConnectionResetError, RuntimeError):
        await stdin.wait_closed()


def _decode_output(output: bytes) -> str:
    return output.decode("utf-8", errors="replace").strip()


def _trim_output(output: str) -> str:
    limit = _output_limit_chars()
    if len(output) <= limit:
        return output

    omitted = len(output) - limit
    return f"{output[:limit]}\n\n[输出已截断：省略 {omitted} 个字符]"


def _combined_output(stdout: bytes, stderr: bytes) -> str:
    stdout_text = _decode_output(stdout)
    stderr_text = _decode_output(stderr)
    if stdout_text and stderr_text:
        return _trim_output(f"{stdout_text}\n\n[错误输出]\n{stderr_text}")
    return _trim_output(stdout_text or stderr_text)


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _token_usage_from_mapping(value: object) -> CodexTokenUsage | None:
    if not isinstance(value, dict):
        return None

    input_tokens = _non_negative_int(value.get("input_tokens"))
    cached_input_tokens = _non_negative_int(value.get("cached_input_tokens"))
    output_tokens = _non_negative_int(value.get("output_tokens"))
    reasoning_output_tokens = _non_negative_int(value.get("reasoning_output_tokens"))
    total_tokens = _non_negative_int(value.get("total_tokens"))
    if None in {
        input_tokens,
        cached_input_tokens,
        output_tokens,
        reasoning_output_tokens,
        total_tokens,
    }:
        return None

    return CodexTokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
        total_tokens=total_tokens,
    )


def extract_token_usage_from_session(path: Path, job_id: str) -> CodexTokenUsage | None:
    """Return the final cumulative token usage from a matching Codex JSONL session."""

    matched_job = False
    token_usage: CodexTokenUsage | None = None

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    for line in lines:
        if job_id in line:
            matched_job = True

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") != "event_msg":
            continue

        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            continue

        info = payload.get("info")
        if not isinstance(info, dict):
            continue

        usage = _token_usage_from_mapping(info.get("total_token_usage"))
        if usage is not None:
            token_usage = usage

    return token_usage if matched_job else None


def find_codex_token_usage(
    sessions_dir: Path | None,
    job_id: str | None,
    *,
    updated_since: float | None = None,
) -> CodexTokenUsage | None:
    if not sessions_dir or not job_id or not sessions_dir.exists():
        return None

    candidates: list[tuple[float, Path]] = []
    try:
        for path in sessions_dir.rglob("*.jsonl"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if updated_since is not None and mtime < updated_since:
                continue
            candidates.append((mtime, path))
    except OSError:
        return None

    for _, path in sorted(candidates, reverse=True):
        usage = extract_token_usage_from_session(path, job_id)
        if usage is not None:
            return usage

    return None


async def _read_stream(
    stream_name: str,
    stream: asyncio.StreamReader | None,
    buffer: bytearray,
    mark_activity: Callable[[], None],
) -> None:
    if stream is None:
        return

    while chunk := await stream.read(4096):
        buffer.extend(chunk)
        _log_text_chunk(stream_name, chunk)
        mark_activity()


async def _stop_process(
    process: asyncio.subprocess.Process,
    *,
    force: bool = False,
) -> None:
    if process.returncode is not None:
        return

    if force:
        process.kill()
        await process.wait()
        return

    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=GRACEFUL_SHUTDOWN_SECONDS)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def run_codex(
    prompt: str,
    *,
    job_id: str | None = None,
    image_paths: list[str] | None = None,
    completion_check: Callable[[], bool] | None = None,
    activity_snapshot: Callable[[], object] | None = None,
) -> CodexRunResult:
    """Run Codex for a prompt and return captured output plus token usage."""

    command = _command(image_paths)
    cwd = _working_directory()
    env = _codex_environment()
    sessions_dir = _codex_sessions_dir(env)
    session_scan_started_at = time.time() - 2.0
    idle_timeout = _idle_timeout_seconds()
    max_runtime = _max_runtime_seconds()
    logger.info(
        "codex.start command=%s cwd=%s image_count=%s prompt_chars=%s max_runtime_seconds=%s idle_timeout_seconds=%s codex_home=%s",
        json.dumps(command, ensure_ascii=False),
        cwd,
        len(image_paths or []),
        len(prompt),
        max_runtime,
        idle_timeout,
        env.get("CODEX_HOME", ""),
    )

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        logger.exception("codex.start_failed command=%s cwd=%s", json.dumps(command, ensure_ascii=False), cwd)
        raise CodexRunError(f"无法启动 Codex：{exc}") from exc

    logger.debug("codex.process_started pid=%s", process.pid)
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    last_activity_at = started_at
    last_snapshot = activity_snapshot() if activity_snapshot else None

    def mark_activity() -> None:
        nonlocal last_activity_at
        last_activity_at = loop.time()

    readers = [
        asyncio.create_task(_read_stream("stdout", process.stdout, stdout_buffer, mark_activity)),
        asyncio.create_task(_read_stream("stderr", process.stderr, stderr_buffer, mark_activity)),
    ]
    prompt_writer = asyncio.create_task(_write_prompt(process.stdin, prompt))

    completed_by_artifact = False

    def current_token_usage() -> CodexTokenUsage | None:
        return find_codex_token_usage(sessions_dir, job_id, updated_since=session_scan_started_at)

    try:
        while True:
            if completion_check and completion_check():
                completed_by_artifact = True
                logger.info("codex.completion_artifact_detected pid=%s", process.pid)
                await _stop_process(process)
                break

            try:
                await asyncio.wait_for(process.wait(), timeout=POLL_SECONDS)
                break
            except asyncio.TimeoutError:
                pass

            now = loop.time()
            if activity_snapshot:
                snapshot = activity_snapshot()
                if snapshot != last_snapshot:
                    last_snapshot = snapshot
                    last_activity_at = now
                    logger.debug("codex.activity_snapshot_changed pid=%s", process.pid)

            if idle_timeout is not None and now - last_activity_at >= idle_timeout:
                await _stop_process(process, force=True)
                output = _combined_output(bytes(stdout_buffer), bytes(stderr_buffer))
                token_usage = current_token_usage()
                logger.warning(
                    "codex.idle_timeout pid=%s seconds=%s stdout_bytes=%s stderr_bytes=%s",
                    process.pid,
                    idle_timeout,
                    len(stdout_buffer),
                    len(stderr_buffer),
                )
                raise CodexRunError("Codex 长时间没有输出或文件进展。", output=output, token_usage=token_usage)

            if now - started_at >= max_runtime:
                await _stop_process(process, force=True)
                output = _combined_output(bytes(stdout_buffer), bytes(stderr_buffer))
                token_usage = current_token_usage()
                logger.warning(
                    "codex.max_runtime_exceeded pid=%s seconds=%s stdout_bytes=%s stderr_bytes=%s",
                    process.pid,
                    max_runtime,
                    len(stdout_buffer),
                    len(stderr_buffer),
                )
                raise CodexRunError("Codex 运行超过最大时长。", output=output, token_usage=token_usage)
    finally:
        await prompt_writer
        await asyncio.gather(*readers, return_exceptions=True)

    stdout = bytes(stdout_buffer)
    stderr = bytes(stderr_buffer)
    output = _combined_output(stdout, stderr)
    token_usage = current_token_usage()

    if completed_by_artifact:
        logger.info(
            "codex.finish_by_artifact pid=%s stdout_bytes=%s stderr_bytes=%s output_chars=%s",
            process.pid,
            len(stdout),
            len(stderr),
            len(output),
        )
        completion_message = "Codex 已生成目标场景文件，已提前结束子进程。"
        return CodexRunResult(
            output="\n\n".join(item for item in (output, completion_message) if item),
            token_usage=token_usage,
        )

    return_code = process.returncode
    if return_code != 0:
        logger.warning(
            "codex.exit_nonzero pid=%s return_code=%s stdout_bytes=%s stderr_bytes=%s output_chars=%s",
            process.pid,
            return_code,
            len(stdout),
            len(stderr),
            len(output),
        )
        raise CodexRunError(f"Codex 退出状态码：{return_code}。", output=output, token_usage=token_usage)

    logger.info(
        "codex.finish pid=%s return_code=%s stdout_bytes=%s stderr_bytes=%s output_chars=%s",
        process.pid,
        return_code,
        len(stdout),
        len(stderr),
        len(output),
    )
    return CodexRunResult(
        output=output or "（Codex 已完成，没有输出。）",
        token_usage=token_usage,
    )
