# Windows: downloads and serves the local LLM with LM Studio's CLI.
# Prerequisite: install LM Studio from https://lmstudio.ai and open it once.
$ErrorActionPreference = "Stop"
$Model = "qwen/qwen3-8b"
$Id    = "qwen3-8b"

if (-not (Get-Command lms -ErrorAction SilentlyContinue)) {
    Write-Host "'lms' CLI not found. Trying to bootstrap it..."
    if (Test-Path "$env:USERPROFILE\.lmstudio\bin\lms.exe") { & "$env:USERPROFILE\.lmstudio\bin\lms.exe" bootstrap }
    else { npx lmstudio install-cli }
    Write-Host "Open a NEW terminal and run this script again."; exit 1
}

Write-Host "Downloading $Model (about 5 GB, pick the Q4_K_M option if asked)..."
lms get $Model
Write-Host "Loading model..."
lms unload --all 2>$null
lms load $Model --identifier $Id --context-length 8192 --gpu max
Write-Host "Starting API server on port 1234..."
lms server start --port 1234
lms ps
Write-Host "Done. LM Studio is serving '$Id' at http://localhost:1234/v1"
