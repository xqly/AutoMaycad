from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shlex
from collections.abc import Callable
from pathlib import Path


DEFAULT_MAX_RUNTIME_SECONDS = 1800
DEFAULT_IDLE_TIMEOUT_SECONDS = 600
DEFAULT_CODEX_ARGS = "exec --skip-git-repo-check --sandbox workspace-write --ask-for-approval never"
DEFAULT_CODEX_HOME = Path("C:/Users/xqly/.codex")
DEFAULT_OUTPUT_LIMIT_CHARS = 50_000
POLL_SECONDS = 1.0
GRACEFUL_SHUTDOWN_SECONDS = 10.0
STREAM_LOG_CHARS = 4000

logger = logging.getLogger("automaycad.codex")


class CodexRunError(RuntimeError):
    """Raised when the Codex subprocess cannot be started or exits unsuccessfully."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


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
    if DEFAULT_CODEX_HOME.exists() and (
        not codex_home or "CodexSandboxOffline" in codex_home
    ):
        env["CODEX_HOME"] = str(DEFAULT_CODEX_HOME)
    return env


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes", "on"}


def _with_default_exec_permissions(args: list[str]) -> list[str]:
    if not args or args[0] != "exec":
        return args

    has_full_access = any(
        arg in {"--dangerously-bypass-approvals-and-sandbox", "--yolo", "--full-auto"}
        for arg in args
    )
    has_sandbox = any(arg in {"--sandbox", "-s"} or arg.startswith("--sandbox=") for arg in args)
    has_approval = any(
        arg in {"--ask-for-approval", "-a"} or arg.startswith("--ask-for-approval=")
        for arg in args
    )

    normalized_args = list(args)
    if not has_sandbox and not has_full_access:
        normalized_args.extend(["--sandbox", "workspace-write"])
    if not has_approval and not has_full_access:
        normalized_args.extend(["--ask-for-approval", "never"])
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
    image_paths: list[str] | None = None,
    completion_check: Callable[[], bool] | None = None,
    activity_snapshot: Callable[[], object] | None = None,
) -> str:
    """Run Codex for a prompt and return captured output."""

    command = _command(image_paths)
    cwd = _working_directory()
    env = _codex_environment()
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
                logger.warning(
                    "codex.idle_timeout pid=%s seconds=%s stdout_bytes=%s stderr_bytes=%s",
                    process.pid,
                    idle_timeout,
                    len(stdout_buffer),
                    len(stderr_buffer),
                )
                raise CodexRunError("Codex 长时间没有输出或文件进展。", output=output)

            if now - started_at >= max_runtime:
                await _stop_process(process, force=True)
                output = _combined_output(bytes(stdout_buffer), bytes(stderr_buffer))
                logger.warning(
                    "codex.max_runtime_exceeded pid=%s seconds=%s stdout_bytes=%s stderr_bytes=%s",
                    process.pid,
                    max_runtime,
                    len(stdout_buffer),
                    len(stderr_buffer),
                )
                raise CodexRunError("Codex 运行超过最大时长。", output=output)
    finally:
        await prompt_writer
        await asyncio.gather(*readers, return_exceptions=True)

    stdout = bytes(stdout_buffer)
    stderr = bytes(stderr_buffer)
    output = _combined_output(stdout, stderr)

    if completed_by_artifact:
        logger.info(
            "codex.finish_by_artifact pid=%s stdout_bytes=%s stderr_bytes=%s output_chars=%s",
            process.pid,
            len(stdout),
            len(stderr),
            len(output),
        )
        completion_message = "Codex 已生成目标场景文件，已提前结束子进程。"
        return "\n\n".join(item for item in (output, completion_message) if item)

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
        raise CodexRunError(f"Codex 退出状态码：{return_code}。", output=output)

    logger.info(
        "codex.finish pid=%s return_code=%s stdout_bytes=%s stderr_bytes=%s output_chars=%s",
        process.pid,
        return_code,
        len(stdout),
        len(stderr),
        len(output),
    )
    return output or "（Codex 已完成，没有输出。）"
