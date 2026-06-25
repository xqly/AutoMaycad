param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 12123,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunScript = Join-Path $ScriptDir "run.ps1"

$RunArgs = @{
    HostAddress = $HostAddress
    Port = $Port
    SkipInstall = $true
    DebugMode = $true
}

if ($Reload) {
    $RunArgs.Reload = $true
}

& $RunScript @RunArgs

