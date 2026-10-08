# fastAPI_Model Model Card

## Purpose

`fastAPI_Model` is the local Ollama model configured for the AI Bot backend generator. It is used for FastAPI/SQLModel project creation, edit planning, focused repair, and runtime validation support.

## Runtime Artifact

- Distribution: [public Google Drive GGUF](https://drive.google.com/file/d/1d4J7nO7Z1GT8S1v1_9eHlcx-UGRLx7Tb/view)
- Local file: `models/fastAPI_Model/fastAPI_Model.Q4_K_M.gguf`
- Runtime name: `fastAPI_Model`
- Family: Qwen2.5 Coder
- Parameters: 3B class
- Quantization: Q4_K_M
- Context setting: 8192 tokens
- Fine-tuned weights: yes; the Google Drive GGUF is the authoritative artifact

The installer downloads the fine-tuned GGUF separately from the Git repository,
validates its exact byte size and GGUF signature, reports its SHA-256, and then
registers it with the runtime-specific Ollama Modelfile.

## System Behavior

The Modelfile instructs the model to generate complete, runnable FastAPI and SQLModel backend code with correct imports, routers, schemas, database wiring, authentication wiring when requested, and OpenAPI-valid behavior.

The prompt is aligned with the versioned multi-stage runtime contract: schema
planning, API contracts, file planning, creation, scoped editing, and focused
validation repair.

## Data Pipeline

Training and evaluation materials are stored in:

- `training_data/fastapi_directive_dataset.jsonl`
- `training_data/fastapi_directive_eval.jsonl`
- `training_data/runtime_protocol_seed.jsonl`
- `training_protocol.json`
- `validate_runtime_dataset.py`
- `fastAPI_dataset_build/`
- `documentation/markdown/training_pipeline.md`

The notebooks provide the LoRA training and export workflow. For subsequent
versions, preserve train/eval splits, validate both JSONL files, train, merge,
export to GGUF, update the manifest size and checksum, and benchmark
create/edit/repair acceptance separately.

## Usage

```powershell
.\setup_fastAPI_Model.ps1
ollama run fastAPI_Model
```

Use `./setup_fastAPI_Model.sh` on Linux/macOS. Both installers download the exact
fine-tuned GGUF by default; their offline option validates an existing local copy.
