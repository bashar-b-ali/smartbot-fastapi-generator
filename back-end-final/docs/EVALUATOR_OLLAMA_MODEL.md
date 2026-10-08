# Evaluator Ollama Model Setup

This project expects an Ollama model named `fastAPI_Model`.

The model package is stored in the sibling `../bot-training/` folder. The main files are:

- `../bot-training/Modelfile.fastAPI_Model`
- `../bot-training/models/fastAPI_Model/fastAPI_Model.Q4_K_M.gguf`
- `../bot-training/models/fastAPI_Model/MANIFEST.json`
- `../bot-training/setup_fastAPI_Model.ps1`

## No-Download Evaluation Package

Include `../bot-training/models/fastAPI_Model/` in the submitted package. It contains the GGUF artifact used to create `fastAPI_Model`.

On the evaluator machine:

```powershell
cd ..\bot-training
.\setup_fastAPI_Model.ps1
```

After model creation:

```powershell
ollama list
ollama run fastAPI_Model
cd ..\back-end-final
python scripts\dev_server.py
```

## Recreate From Included GGUF

The packaged model is recreated from the included GGUF and Modelfile:

```powershell
cd ..\bot-training
ollama create fastAPI_Model -f Modelfile.fastAPI_Model
cd ..\back-end-final
python scripts\dev_server.py
```

## Future Adapter Or Fine-Tuned Artifact

If a future LoRA/fine-tuned artifact is produced, replace the GGUF under `../bot-training/models/fastAPI_Model/` or update `../bot-training/Modelfile.fastAPI_Model`:

Full GGUF model:

```dockerfile
FROM ./models/fastAPI_Model/my-fastapi-model.Q4_K_M.gguf
```

LoRA/adapter GGUF:

```dockerfile
FROM ../bot-training/models/fastAPI_Model/fastAPI_Model.Q4_K_M.gguf
ADAPTER ./models/fastAPI_Model/my-fastapi-adapter.gguf
```

Then run:

```powershell
cd ..\bot-training
.\setup_fastAPI_Model.ps1
ollama run fastAPI_Model
```

## Backend Configuration

`.env` should contain:

```env
LLM_PROVIDER=ollama
LLM_MODEL=fastAPI_Model
OLLAMA_HOST=http://localhost:11434
OLLAMA_NUM_CTX=8192
OLLAMA_AUTO_PULL=false
OLLAMA_AUTO_WARMUP=true
```

Use only `python scripts\dev_server.py` for backend startup. Do not use raw `uvicorn --reload`; it can restart during generated file writes and interrupt edit jobs.
