param(
    [string]$ModelName = "fastAPI_Model",
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$SelectedModelfile = Join-Path $Root "Modelfile.fastAPI_Model"
$GgufFile = Join-Path $Root "models\fastAPI_Model\fastAPI_Model.Q4_K_M.gguf"
$DownloadScript = Join-Path $Root "download_model.py"

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "Ollama is not installed or not available on PATH."
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ is required to download and verify the model."
}

if ($Offline) {
    & python $DownloadScript --verify-only
}
else {
    & python $DownloadScript
}
if ($LASTEXITCODE -ne 0) {
    throw "Could not download or verify the fine-tuned model: $GgufFile"
}

Push-Location $Root
try {
    & ollama create $ModelName -f $SelectedModelfile
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create Ollama model: $ModelName"
    }
    & ollama show $ModelName
    if ($LASTEXITCODE -ne 0) {
        throw "Ollama created the model but could not inspect it: $ModelName"
    }
    Write-Host "Model is ready: $ModelName"
}
finally {
    Pop-Location
}
