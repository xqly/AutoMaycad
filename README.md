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

The site requires login before creating or viewing tasks. The current built-in
account is:

```text
username: admin
password: 123456
```

Tasks store an owner. Regular users can only see tasks they created, while
`admin` can see every user's tasks. Existing tasks are assigned to `admin` when
the database is upgraded.

After login, users can open the personal center to change their own password.
The `admin` account can also create new regular accounts there. Account names
must be unique and may contain letters, numbers, underscores, dots, and hyphens.

Jobs are stored persistently in a SQLite database at `tasks/jobs.sqlite3` by
default. Generated task folders remain on disk under `tasks/<task-id>/`.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run

On this Windows machine, use the helper script:

```powershell
.\run.ps1
```

If PowerShell blocks local scripts, run it with:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

The script creates `.venv` when needed, installs dependencies, sets the local
Codex-related paths, and starts the app at `http://127.0.0.1:12123`.

To skip dependency installation on later runs:

```powershell
.\run.ps1 -SkipInstall
```

For detailed debug logs:

```powershell
.\run.ps1 -DebugMode
```

Debug mode sets `LOG_LEVEL=DEBUG`, streams Codex stdout/stderr into the app
logs, raises the captured Codex output limit, and writes detailed logs to:

```text
logs\automaycad-debug.log
```

You can combine options:

```powershell
.\run.ps1 -SkipInstall -DebugMode -Port 12124
```

If the default port is already occupied, the script exits with a clear message;
rerun it with another port such as `-Port 12124`.

Manual start:

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
<bundled codex> exec --skip-git-repo-check --sandbox workspace-write
```

The prompt is piped to Codex on stdin. This avoids CLI argument parsing
differences between Codex builds that can otherwise produce `No prompt
provided` even when the application has built a valid task prompt.

The bundled Codex binary comes from the `openai-codex` Python package installed in this project's virtual environment. This avoids the WindowsApps `codex.exe` launcher if that launcher is blocked by Windows permissions.

When `CODEX_HOME` is not set, the app tries to reuse an existing Codex state
folder for the current Windows user:

```text
%USERPROFILE%\.codex
```

If that folder does not exist, Codex falls back to its own default behavior. Set
`CODEX_HOME` explicitly when the app must reuse a login from another writable
Codex state folder.

You can change this with environment variables:

```powershell
$env:CODEX_COMMAND = "codex"
$env:CODEX_ARGS = "exec --skip-git-repo-check --sandbox workspace-write"
$env:CODEX_MAX_RUNTIME_SECONDS = "1800"
$env:CODEX_IDLE_TIMEOUT_SECONDS = "600"
$env:CODEX_OUTPUT_LIMIT_CHARS = "50000"
$env:CODEX_WORKDIR = "C:\path\to\workspace"
$env:CODEX_HOME = "$env:USERPROFILE\.codex"
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
$env:CODEX_ARGS_JSON = '["exec", "--skip-git-repo-check", "--sandbox", "workspace-write"]'
```

## MAYCAD skill

MAYCAD generation is grounded in the project-local skill at:

```text
skills\maycad
```

Each Codex task prompt points Codex at `skills\maycad\SKILL.md`, the scene XML
reference, and the skill generator script. If Codex does not produce a scene,
the server fallback loads:

```text
skills\maycad\scripts\generate_maycad_cabinet.py
```

To test or replace the skill without changing the repository copy:

```powershell
$env:MAYCAD_SKILL_DIR = "C:\path\to\maycad"
```

If Codex fails with `attempt to write a readonly database`, `access denied`, or
`CODEX_HOME points to ... but that path does not exist`, start Uvicorn from the
same Windows user that ran `codex login`, or set `CODEX_HOME` to a writable
Codex state folder that exists and has valid authentication.

## Security note

The built-in account is intentionally minimal and uses a fixed password for
local use. Do not expose this app to the public internet without replacing it
with stronger authentication and careful sandboxing. Anyone who can submit
prompts can ask Codex to work in `CODEX_WORKDIR`.
