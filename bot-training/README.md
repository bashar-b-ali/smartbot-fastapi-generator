# fastAPI_Model 3B Runtime Package

This folder contains the local 3B FastAPI generation model package used by the backend evaluation setup.

Normal application users only need to register the included model with Ollama.
Dataset building and training are separate, optional workflows. For complete
application installation, begin with the [root README](../README.md).

## Requirements

- Ollama for using the packaged GGUF model.
- Python 3.11+ for dataset preparation/validation utilities.
- `PyGithub` and a minimally scoped `GITHUB_TOKEN` only for GitHub repository discovery.
- A suitable GPU or hosted notebook environment is strongly recommended for training.

## Included artifact

- `models/fastAPI_Model/fastAPI_Model.Q4_K_M.gguf`
- Base architecture: Qwen2.5 Coder 3B
- Quantization: Q4_K_M
- Ollama runtime model name: `fastAPI_Model`
- Context configured by Modelfile: `8192`

## Create the Ollama model

From this folder:

```powershell
.\setup_fastAPI_Model.ps1
```

Or manually:

```powershell
ollama create fastAPI_Model -f Modelfile.fastAPI_Model
ollama run fastAPI_Model
```

## Backend configuration

In `..\back-end-final\.env`:

```env
LLM_PROVIDER=ollama
LLM_MODEL=fastAPI_Model
OLLAMA_HOST=http://localhost:11434
OLLAMA_NUM_CTX=8192
OLLAMA_AUTO_PULL=false
OLLAMA_AUTO_WARMUP=true
```

Start the backend without reload:

```powershell
cd ..\back-end-final
python scripts\dev_server.py
```

## Training notebooks

The notebooks in this folder are aligned to this package name and base model:

- `train.ipynb`
- `colab_qwen_lora_training.ipynb`
- `colab_qwen_lora_training_max.ipynb`

They document the dataset and optional LoRA/merge/export workflow for the FastAPI backend-generation task. The included GGUF is the ready-to-run local model artifact used by the project runtime.

## Folder layout

```text
models/fastAPI_Model/       Ready-to-run quantized GGUF and manifest
training_data/              Runtime-protocol training/evaluation JSONL
reports/                    Evaluation results, predictions, and loss history
fastAPI_dataset_build/      Optional repository discovery and dataset utilities
train.ipynb                 Optional training/export workflow
MODEL_CARD.md               Model purpose, limitations, and evaluation context
training_protocol.json      Versioned task/lifecycle contract
setup_fastAPI_Model.ps1     Validates and registers the GGUF with Ollama
```

## Runtime-aligned training contract

Production uses the versioned `project-run.v1` lifecycle and `runtime-task.v1`
training protocol. The model is trained across schema planning, API contracts,
file planning, file creation, scoped editing, and validation repair rather than
on one monolithic directive format.

Validate a prepared multi-task JSONL before training:

```powershell
python validate_runtime_dataset.py training_data\runtime_protocol_seed.jsonl --require-all-tasks
```

`setup_fastAPI_Model.ps1` verifies the GGUF size and SHA-256 before registering
it with Ollama. A successful validation means the package is internally
consistent; it does not prove that the included GGUF has received the new
multi-task fine-tuning.

## Optional dataset-building workflow

This workflow is not required to run Smart Bot. Run it only when intentionally
rebuilding research/training data.

```powershell
cd .\fastAPI_dataset_build
python -m pip install PyGithub
$env:GITHUB_TOKEN = "<fine-grained token with minimum read permissions>"
python discover_repos.py
python dataset_making.py
python extract_candidates.py
python dedupe_candidates.py
```

The prompt-generation scripts support heuristic generation or configured LLM
providers. Inspect their help before use:

```powershell
python generate_prompts_heuristic.py --help
python generate_prompts.py --help
```

Never hard-code or commit a GitHub/provider token. Dataset collection may clone
third-party repositories; review licensing and remove sensitive or unsuitable data
before training.

## Related documentation

- [System requirements](../REQUIREMENTS.md)
- [Full setup guide](../docs/SETUP_GUIDE.md)
- [Developer guide](../docs/DEVELOPER_GUIDE.md)
- [Troubleshooting](../docs/TROUBLESHOOTING.md)
- [Model card](MODEL_CARD.md)
