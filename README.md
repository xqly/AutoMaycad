# Codex Prompt Runner

A small FastAPI site for submitting prompts to Codex asynchronously.

The browser submits a prompt, the API queues a background job, and the server runs Codex. The UI shows whether each job is queued, running, succeeded, or failed, and displays captured Codex output when the job finishes.

Jobs are stored in memory, so job history is cleared when the server restarts.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run

```powershell
uvicorn app.main:app --reload
```

Then open:

```text
http://127.0.0.1:8000
```

## Codex command

By default the server runs:

```text
<bundled codex> exec --skip-git-repo-check <prompt>
```

The bundled Codex binary comes from the `openai-codex` Python package installed in this project's virtual environment. This avoids the WindowsApps `codex.exe` launcher if that launcher is blocked by Windows permissions.

On this machine the app also defaults Codex state to:

```text
C:\Users\xqly\.codex
```

That lets the background Codex process reuse the Codex login already present on the computer.

You can change this with environment variables:

```powershell
$env:CODEX_COMMAND = "codex"
$env:CODEX_ARGS = "exec --skip-git-repo-check"
$env:CODEX_TIMEOUT_SECONDS = "900"
$env:CODEX_OUTPUT_LIMIT_CHARS = "50000"
$env:CODEX_WORKDIR = "C:\path\to\workspace"
$env:CODEX_HOME = "C:\Users\xqly\.codex"
uvicorn app.main:app --reload
```

`CODEX_ARGS` is split like a shell command, but the prompt itself is passed as a separate subprocess argument.

For exact argument control, especially on Windows, you can use JSON instead:

```powershell
$env:CODEX_ARGS_JSON = '["exec", "--skip-git-repo-check"]'
```

## Security note

Do not expose this app to the public internet without adding authentication and careful sandboxing. Anyone who can submit prompts can ask Codex to work in `CODEX_WORKDIR`.
