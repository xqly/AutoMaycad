#!/usr/bin/env bash
set -euo pipefail

HOST_ADDRESS="${HOST_ADDRESS:-0.0.0.0}"
PORT="${PORT:-12123}"
SKIP_INSTALL="${SKIP_INSTALL:-0}"
DEBUG_MODE=0
RELOAD=0

usage() {
    cat <<'EOF'
Usage: ./run.sh [--host HOST] [--port PORT] [--skip-install] [--debug] [--reload]

Starts AutoMaycad. The script creates .venv and installs requirements when needed.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            HOST_ADDRESS="${2:?Missing value for --host}"
            shift 2
            ;;
        --port)
            PORT="${2:?Missing value for --port}"
            shift 2
            ;;
        --skip-install)
            SKIP_INSTALL=1
            shift
            ;;
        --debug)
            DEBUG_MODE=1
            shift
            ;;
        --reload)
            RELOAD=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

VENV_DIR="$PROJECT_ROOT/.venv"
PYTHON_EXE="$VENV_DIR/bin/python"

if [[ ! -x "$PYTHON_EXE" ]]; then
    echo "Creating virtual environment in .venv..."
    if command -v python3 >/dev/null 2>&1; then
        python3 -m venv "$VENV_DIR"
    else
        python -m venv "$VENV_DIR"
    fi
fi

if [[ "$SKIP_INSTALL" != "1" ]]; then
    echo "Installing dependencies from requirements.txt..."
    "$PYTHON_EXE" -m pip install -r "$PROJECT_ROOT/requirements.txt"
fi

export CODEX_WORKDIR="${CODEX_WORKDIR:-$PROJECT_ROOT}"
export CODEX_ARGS="${CODEX_ARGS:-exec --skip-git-repo-check --sandbox workspace-write}"
export TASKS_DIR="${TASKS_DIR:-$PROJECT_ROOT/tasks}"
export JOBS_DB_PATH="${JOBS_DB_PATH:-$TASKS_DIR/jobs.sqlite3}"

UVICORN_LOG_LEVEL="info"
if [[ "$DEBUG_MODE" == "1" ]]; then
    export AUTOMAYCAD_DEBUG=1
    export LOG_LEVEL=DEBUG
    export AUTOMAYCAD_LOG_CODEX_STREAMS=1
    export AUTOMAYCAD_LOG_FILE="${AUTOMAYCAD_LOG_FILE:-$PROJECT_ROOT/logs/automaycad-debug.log}"
    export CODEX_OUTPUT_LIMIT_CHARS="${CODEX_OUTPUT_LIMIT_CHARS:-200000}"
    UVICORN_LOG_LEVEL="debug"
    echo "Debug mode enabled. Detailed logs: $AUTOMAYCAD_LOG_FILE"
fi

if ! "$PYTHON_EXE" - "$PORT" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
then
    echo "Port $PORT is already in use. Try another port, for example: ./run.sh --port 12124" >&2
    exit 1
fi

echo "Starting AutoMaycad at http://127.0.0.1:$PORT"
echo "Login: admin / 123456"

UVICORN_ARGS=(
    "app.main:app"
    "--host" "$HOST_ADDRESS"
    "--port" "$PORT"
    "--log-level" "$UVICORN_LOG_LEVEL"
)

if [[ "$RELOAD" == "1" ]]; then
    UVICORN_ARGS+=(
        "--reload"
        "--reload-dir" "$PROJECT_ROOT/app"
        "--reload-dir" "$PROJECT_ROOT/scripts"
    )
fi

exec "$PYTHON_EXE" -m uvicorn "${UVICORN_ARGS[@]}"
