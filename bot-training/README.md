# fastAPI_Model 3B Runtime Package

This folder contains the setup for the local 3B FastAPI generation model used by
the backend.

Normal application users only need to run the platform setup script. It downloads
the public fine-tuned GGUF from Google Drive and registers it with Ollama.
Dataset building and training are separate, optional workflows. For complete
application installation, begin with the [root README](../README.md).

## Requirements

- Ollama for running the model.
- Python 3.11+ for dataset preparation/validation utilities.
- `PyGithub` and a minimally scoped `GITHUB_TOKEN` only for GitHub repository discovery.
- A suitable GPU or hosted notebook environment is strongly recommended for training.

## Model distribution

- Public source: [fine-tuned GGUF on Google Drive](https://drive.google.com/file/d/1d4J7nO7Z1GT8S1v1_9eHlcx-UGRLx7Tb/view)
- Local artifact: `models/fastAPI_Model/fastAPI_Model.Q4_K_M.gguf`
- Base architecture: Qwen2.5 Coder 3B
- Quantization: Q4_K_M
- Ollama runtime model name: `fastAPI_Model`
- Context configured by Modelfile: `8192`

Review and comply with the base model's license and the licenses of the training
data before redistributing or using the fine-tuned weights.

## Create the Ollama model

From this folder:

```powershell
.\setup_fastAPI_Model.ps1
```

On Linux or macOS:

```sh
chmod +x setup_fastAPI_Model.sh
./setup_fastAPI_Model.sh
```

The first run downloads about 1.8 GB. Interrupted downloads resume from the
`.part` file. To use an existing GGUF without a network connection, run the
PowerShell script with `-Offline` or the shell script with `--offline`.

After either setup path, test with `ollama run fastAPI_Model`.

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

They document the dataset and optional LoRA/merge/export workflow for the FastAPI
backend-generation task. The public Google Drive GGUF contains the current
ready-to-run fine-tuned weights used by the project runtime.

## Folder layout

```text
models/fastAPI_Model/       Manifest and downloaded fine-tuned GGUF
training_data/              Runtime-protocol training/evaluation JSONL
reports/                    Evaluation results, predictions, and loss history
fastAPI_dataset_build/      Optional repository discovery and dataset utilities
train.ipynb                 Optional training/export workflow
MODEL_CARD.md               Model purpose, limitations, and evaluation context
training_protocol.json      Versioned task/lifecycle contract
setup_fastAPI_Model.ps1     Windows public/offline installer
setup_fastAPI_Model.sh      Linux/macOS public/offline installer
download_model.py           Resumable Drive downloader and validator
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

The setup path verifies the GGUF size and signature and reports its SHA-256 before
registering it with Ollama. The manifest records the public Drive file ID and the
authoritative artifact size.

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
- [Full setup guide](../project-docs/SETUP_GUIDE.md)
- [Developer guide](../project-docs/DEVELOPER_GUIDE.md)
- [Troubleshooting](../project-docs/TROUBLESHOOTING.md)
- [Model card](MODEL_CARD.md)
