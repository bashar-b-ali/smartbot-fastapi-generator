$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$TrainingRoot = Join-Path (Split-Path -Parent $Root) "bot-training"
$Setup = Join-Path $TrainingRoot "setup_fastAPI_Model.ps1"

if (-not (Test-Path -LiteralPath $Setup)) {
    throw "Missing model setup script: $Setup"
}

& $Setup -ModelName "fastAPI_Model"
Write-Host "fastAPI_Model is ready. Start backend with: python scripts\dev_server.py"
