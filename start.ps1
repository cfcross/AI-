$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup.ps1 first to create the Python environment.' }
$env:PYTHONUTF8 = '1'
$cfg = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
$url = "http://127.0.0.1:$($cfg.display.port)"
try {
    $status = Invoke-RestMethod "$url/api/state" -TimeoutSec 2
    if ($status.application_id -eq 'ai-printer-phone-asr') {
        if ($cfg.display.workbuddy_showcase) {
            Write-Host "Application is already running. WorkBuddy display: $url/showcase"
        } else {
            Start-Process $url
            Write-Host 'Application is already running. Opened the display page.'
        }
        return
    }
} catch { }
& $python -u app.py
if ($LASTEXITCODE -ne 0) { throw "Application exited with code $LASTEXITCODE" }
