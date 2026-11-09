$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process -FilePath "powershell.exe" -Verb RunAs -WindowStyle Hidden -ArgumentList @(
        "-NoProfile",
        "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`""
    )
    exit
}

$logDirectory = Join-Path $projectRoot ".local"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$server = Start-Process -FilePath $python -WindowStyle Hidden -WorkingDirectory $projectRoot -ArgumentList @(
    "-u", "server.py", "--host", "0.0.0.0", "--port", "80", "--tv-playback"
) -RedirectStandardOutput (Join-Path $logDirectory "server.stdout.log") -RedirectStandardError (Join-Path $logDirectory "server.stderr.log") -PassThru -Wait
exit $server.ExitCode
