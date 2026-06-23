# AutoMaycad Shelf Tasks

A small FastAPI site for turning shelf/rack requirements into asynchronous
Codex tasks that generate MAYCAD `.scene` files.

The browser submits a shelf requirement plus optional reference images, the API
creates a task ID and a folder under `tasks/<task-id>/`, then starts Codex in
the background. Codex receives a MAYCAD-specific prompt, with uploaded images
attached to the initial prompt, that requires the final scene file to be written
into that task folder.

The UI shows whether each job is queued, running, succeeded, or failed. It also
shows the task folder, expected scene path, uploaded images, generated files,
and captured Codex output when available.

Jobs are stored persistently in a SQLite database at `tasks/jobs.sqlite3` by
default. Generated task folders remain on disk under `tasks/<task-id>/`.

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
<bundled codex> exec --skip-git-repo-check
```

The prompt is piped to Codex on stdin. This avoids CLI argument parsing
differences between Codex builds that can otherwise produce `No prompt
provided` even when the application has built a valid task prompt.

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
$env:CODEX_MAX_RUNTIME_SECONDS = "1800"
$env:CODEX_IDLE_TIMEOUT_SECONDS = "600"
$env:CODEX_OUTPUT_LIMIT_CHARS = "50000"
$env:CODEX_WORKDIR = "C:\path\to\workspace"
$env:CODEX_HOME = "C:\Users\xqly\.codex"
$env:TASKS_DIR = "C:\path\to\workspace\tasks"
$env:JOBS_DB_PATH = "C:\path\to\workspace\tasks\jobs.sqlite3"
uvicorn app.main:app --reload
```

`CODEX_ARGS` is split like a shell command, and the prompt itself is written to
the Codex subprocess stdin.
Uploaded prompt images are stored under `tasks/<task-id>/input_images/` and
passed to `codex exec` with repeated `--image <file>` arguments. The web form
accepts up to 8 PNG, JPEG, GIF, or WebP files, with a 15 MB limit per image.
The app now treats a generated, stable `.scene` file as the primary completion
signal. `CODEX_IDLE_TIMEOUT_SECONDS` only fails a run when Codex has no output
and the task folder has no file changes for that many seconds. `CODEX_MAX_RUNTIME_SECONDS`
is a final safety cap. The older `CODEX_TIMEOUT_SECONDS` is still accepted as a
fallback for the maximum runtime.

For exact argument control, especially on Windows, you can use JSON instead:

```powershell
$env:CODEX_ARGS_JSON = '["exec", "--skip-git-repo-check"]'
```

If Codex fails with `attempt to write a readonly database` or `access denied`
under `C:\Users\xqly\.codex`, start Uvicorn from a normal user terminal rather
than a sandboxed process, or set `CODEX_HOME` to a writable Codex state folder
that has valid authentication.

## Security note

Do not expose this app to the public internet without adding authentication and careful sandboxing. Anyone who can submit prompts can ask Codex to work in `CODEX_WORKDIR`.
