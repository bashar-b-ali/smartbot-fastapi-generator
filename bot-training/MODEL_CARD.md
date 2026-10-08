# fastAPI_Model Model Card

## Purpose

`fastAPI_Model` is the local Ollama model configured for the AI Bot backend generator. It is used for FastAPI/SQLModel project creation, edit planning, focused repair, and runtime validation support.

## Runtime Artifact

- File: `models/fastAPI_Model/fastAPI_Model.Q4_K_M.gguf`
- Runtime name: `fastAPI_Model`
- Family: Qwen2.5 Coder
- Parameters: 3B class
- Quantization: Q4_K_M
- Context setting: 8192 tokens
- Fine-tuned weights: no; the current GGUF is the packaged base-model artifact

The runtime name and Modelfile specialize prompting, but they do not make the
current GGUF a fine-tuned model. Produce and evaluate a merged/exported artifact
before changing the manifest's `fine_tuned` field.

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

The notebooks provide a LoRA training and export starting point. Before a real
training run, expand the six-task seed into train/eval splits, validate both
JSONL files, train, merge, export to GGUF, update the checksum, and benchmark
create/edit/repair acceptance separately.

## Usage

```powershell
ollama create fastAPI_Model -f Modelfile.fastAPI_Model
ollama run fastAPI_Model
```
