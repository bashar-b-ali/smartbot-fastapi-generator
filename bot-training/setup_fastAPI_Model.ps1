param(
    [string]$ModelName = "fastAPI_Model"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Modelfile = Join-Path $Root "Modelfile.fastAPI_Model"
$ModelFile = Join-Path $Root "models\fastAPI_Model\fastAPI_Model.Q4_K_M.gguf"
$ManifestFile = Join-Path $Root "models\fastAPI_Model\MANIFEST.json"

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "Ollama is not installed or not available on PATH."
}

if (-not (Test-Path -LiteralPath $ModelFile)) {
    throw "Missing model artifact: $ModelFile"
}
if (-not (Test-Path -LiteralPath $ManifestFile)) {
    throw "Missing model manifest: $ManifestFile"
}

$Manifest = Get-Content -LiteralPath $ManifestFile -Raw | ConvertFrom-Json
$ActualSize = (Get-Item -LiteralPath $ModelFile).Length
$ActualHash = (Get-FileHash -LiteralPath $ModelFile -Algorithm SHA256).Hash
if ($ActualSize -ne [int64]$Manifest.artifact_size_bytes) {
    throw "Model size mismatch. Expected $($Manifest.artifact_size_bytes), got $ActualSize."
}
if ($ActualHash -ne $Manifest.sha256) {
    throw "Model checksum mismatch. Expected $($Manifest.sha256), got $ActualHash."
}
Write-Host "Verified model artifact checksum: $ActualHash"

Push-Location $Root
try {
    ollama create $ModelName -f $Modelfile
    ollama show $ModelName
    Write-Host "Model is ready: $ModelName"
}
finally {
    Pop-Location
}
