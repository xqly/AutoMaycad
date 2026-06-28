param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 12123,
    [switch]$SkipInstall,
    [switch]$DebugMode,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$VenvDir = Join-Path $ProjectRoot ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$ReloadAppDir = Join-Path $ProjectRoot "app"
$ReloadScriptsDir = Join-Path $ProjectRoot "scripts"

function Test-PortAvailable {
    param([int]$PortToCheck)

    $Listener = $null
    try {
        $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $PortToCheck)
        $Listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($Listener) {
            $Listener.Stop()
        }
    }
}

if (-not (Test-Path $PythonExe)) {
    Write-Host "Creating virtual environment in .venv..."
    $SystemPython = (Get-Command py -ErrorAction SilentlyContinue)
    if ($SystemPython) {
        py -3 -m venv $VenvDir
    } else {
        python -m venv $VenvDir
    }
}

if (-not $SkipInstall) {
    Write-Host "Installing dependencies from requirements.txt..."
    & $PythonExe -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
}

if (-not $env:CODEX_HOME) {
    $env:CODEX_HOME = "C:\Users\xqly\.codex"
}

if (-not $env:CODEX_WORKDIR) {
    $env:CODEX_WORKDIR = $ProjectRoot
}

if (-not $env:TASKS_DIR) {
    $env:TASKS_DIR = Join-Path $ProjectRoot "tasks"
}

if (-not $env:JOBS_DB_PATH) {
    $env:JOBS_DB_PATH = Join-Path $env:TASKS_DIR "jobs.sqlite3"
}

if ($DebugMode) {
    $env:AUTOMAYCAD_DEBUG = "1"
    $env:LOG_LEVEL = "DEBUG"
    $env:AUTOMAYCAD_LOG_CODEX_STREAMS = "1"
    $env:AUTOMAYCAD_LOG_FILE = Join-Path $ProjectRoot "logs\automaycad-debug.log"
    $env:CODEX_OUTPUT_LIMIT_CHARS = "200000"
    $UvicornLogLevel = "debug"
    Write-Host "Debug mode enabled. Detailed logs: $env:AUTOMAYCAD_LOG_FILE"
} else {
    $UvicornLogLevel = "info"
}

if (-not (Test-PortAvailable -PortToCheck $Port)) {
    throw "Port $Port is already in use. Try another port, for example: .\run.ps1 -DebugMode -Port 12124"
}

Write-Host "Starting AutoMaycad at http://127.0.0.1:$Port"
Write-Host "Login: admin / 123456"

$UvicornArgs = @(
    "app.main:app",
    "--host", $HostAddress,
    "--port", "$Port",
    "--log-level", $UvicornLogLevel
)

if ($Reload) {
    Write-Host "Reload mode enabled. Avoid creating 画图大模型 jobs in this mode on Windows."
    $UvicornArgs += @(
        "--reload",
        "--reload-dir", $ReloadAppDir,
        "--reload-dir", $ReloadScriptsDir
    )
}

& $PythonExe -m uvicorn @UvicornArgs

