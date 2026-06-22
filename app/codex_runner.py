from __future__ import annotations

import asyncio
import json
import os
import shlex
from pathlib import Path


DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_CODEX_ARGS = "exec --skip-git-repo-check"
DEFAULT_CODEX_HOME = Path("C:/Users/xqly/.codex")
DEFAULT_OUTPUT_LIMIT_CHARS = 50_000


class CodexRunError(RuntimeError):
    """Raised when the Codex subprocess cannot be started or exits unsuccessfully."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


def _timeout_seconds() -> int:
    raw_value = os.getenv("CODEX_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _output_limit_chars() -> int:
    raw_value = os.getenv("CODEX_OUTPUT_LIMIT_CHARS", str(DEFAULT_OUTPUT_LIMIT_CHARS))
    try:
        return max(1_000, int(raw_value))
    except ValueError:
        return DEFAULT_OUTPUT_LIMIT_CHARS


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


def _command(prompt: str) -> list[str]:
    command = _codex_command()
    args_json = os.getenv("CODEX_ARGS_JSON")
    if args_json:
        try:
            parsed_args = json.loads(args_json)
        except json.JSONDecodeError as exc:
            raise CodexRunError("CODEX_ARGS_JSON is not valid JSON.") from exc
        if not isinstance(parsed_args, list) or not all(isinstance(item, str) for item in parsed_args):
            raise CodexRunError("CODEX_ARGS_JSON must be a JSON array of strings.")
        args = parsed_args
    else:
        args = shlex.split(os.getenv("CODEX_ARGS", DEFAULT_CODEX_ARGS))
    return [command, *args, prompt]


def _decode_output(output: bytes) -> str:
    return output.decode("utf-8", errors="replace").strip()


def _trim_output(output: str) -> str:
    limit = _output_limit_chars()
    if len(output) <= limit:
        return output

    omitted = len(output) - limit
    return f"{output[:limit]}\n\n[output truncated: {omitted} characters omitted]"


def _combined_output(stdout: bytes, stderr: bytes) -> str:
    stdout_text = _decode_output(stdout)
    stderr_text = _decode_output(stderr)
    if stdout_text and stderr_text:
        return _trim_output(f"{stdout_text}\n\n[stderr]\n{stderr_text}")
    return _trim_output(stdout_text or stderr_text)


async def run_codex(prompt: str) -> str:
    """Run Codex for a prompt and return captured output."""

    try:
        process = await asyncio.create_subprocess_exec(
            *_command(prompt),
            cwd=str(_working_directory()),
            env=_codex_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise CodexRunError(f"Could not start Codex: {exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=_timeout_seconds(),
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        stdout, stderr = await process.communicate()
        output = _combined_output(stdout, stderr)
        raise CodexRunError("Codex timed out.", output=output) from exc

    return_code = process.returncode
    output = _combined_output(stdout, stderr)
    if return_code != 0:
        raise CodexRunError(f"Codex exited with status {return_code}.", output=output)

    return output or "(Codex completed without output.)"
