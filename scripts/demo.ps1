<#
.SYNOPSIS
  Starts the full local stack for the PR review bot demo: Postgres,
  Temporal dev server, the Temporal worker, the FastAPI webhook, and
  (optionally) a smee.io tunnel.

.PARAMETER SmeeUrl
  Your smee.io channel URL (e.g. https://smee.io/xxxxxxxxx). If omitted,
  the smee tunnel is skipped -- start it yourself if you need GitHub to
  reach this machine.

.PARAMETER ForceFailureAfterPost
  Pass -ForceFailureAfterPost to start the worker with
  PRBOT_DEMO_FORCE_FAILURE_AFTER_POST=true, to demo Stage 6's saga
  compensation (a posted comment gets deleted and the run marked
  failed). Omit for normal operation.

.EXAMPLE
  .\scripts\demo.ps1 -SmeeUrl https://smee.io/AbCdEf123456

.EXAMPLE
  .\scripts\demo.ps1 -SmeeUrl https://smee.io/AbCdEf123456 -ForceFailureAfterPost
#>
param(
    [string]$SmeeUrl,
    [switch]$ForceFailureAfterPost
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "== Starting Postgres (docker-compose) ==" -ForegroundColor Cyan
docker-compose up -d
Start-Sleep -Seconds 2

Write-Host "== Starting Temporal dev server ==" -ForegroundColor Cyan
$temporalProc = Start-Process -PassThru -WindowStyle Hidden powershell -ArgumentList "-NoProfile", "-Command", "temporal server start-dev"
Start-Sleep -Seconds 3

Write-Host "== Starting Temporal worker ==" -ForegroundColor Cyan
if ($ForceFailureAfterPost) {
    Write-Host "   (PRBOT_DEMO_FORCE_FAILURE_AFTER_POST=true -- saga compensation will trigger)" -ForegroundColor Yellow
    $env:PRBOT_DEMO_FORCE_FAILURE_AFTER_POST = "true"
} else {
    Remove-Item Env:\PRBOT_DEMO_FORCE_FAILURE_AFTER_POST -ErrorAction SilentlyContinue
}
$workerProc = Start-Process -PassThru -WindowStyle Hidden powershell -ArgumentList "-NoProfile", "-Command", ".venv\Scripts\python -m prbot.orchestration.worker"

Write-Host "== Starting webhook server (localhost:8000) ==" -ForegroundColor Cyan
$webhookProc = Start-Process -PassThru -WindowStyle Hidden powershell -ArgumentList "-NoProfile", "-Command", ".venv\Scripts\uvicorn prbot.api.app:app --port 8000"

$smeeProc = $null
if ($SmeeUrl) {
    Write-Host "== Starting smee tunnel ($SmeeUrl -> 127.0.0.1:8000/webhook) ==" -ForegroundColor Cyan
    $smeeProc = Start-Process -PassThru -WindowStyle Hidden npx -ArgumentList "--yes", "smee-client", "-u", $SmeeUrl, "-t", "http://127.0.0.1:8000/webhook"
} else {
    Write-Host "== No -SmeeUrl given, skipping tunnel -- start one yourself if GitHub needs to reach this machine ==" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Stack is up:" -ForegroundColor Green
Write-Host "  Temporal Web UI:  http://localhost:8233"
Write-Host "  Webhook:          http://127.0.0.1:8000/webhook"
Write-Host "  API docs:         http://127.0.0.1:8000/docs"
Write-Host ""
Write-Host "Open or push to a PR on your GitHub App's installed repo to trigger a review."
Write-Host ""
Write-Host "Process IDs (stop with Stop-Process -Id <id>):"
Write-Host "  Temporal server: $($temporalProc.Id)"
Write-Host "  Worker:          $($workerProc.Id)"
Write-Host "  Webhook:         $($webhookProc.Id)"
if ($smeeProc) { Write-Host "  smee tunnel:     $($smeeProc.Id)" }
Write-Host ""
Write-Host "Press Ctrl+C to stop watching (the background processes keep running)." -ForegroundColor DarkGray
