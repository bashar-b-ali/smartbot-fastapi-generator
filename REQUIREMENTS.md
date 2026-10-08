# System Requirements

This page separates what is required to run Smart Bot from what is needed only for
development, testing, or model training.

## Required software

| Software | Recommended baseline | Used by | Check command |
| --- | --- | --- | --- |
| Windows 10/11 with PowerShell, or Linux/macOS with a POSIX shell | Current supported release | Included setup scripts and commands | `$PSVersionTable.PSVersion` or `sh --version` |
| Python | 3.11 or newer | Backend | `python --version` |
| Node.js | 18 LTS or newer | Frontend | `node --version` |
| npm | Included with Node.js | Frontend | `npm --version` |
| MySQL Community Server | MySQL 8.x | Backend database | `mysql --version` |
| Ollama | A current Windows release | Local AI model | `ollama --version` |

The backend formally declares Python `>=3.11`. Use a supported 64-bit Python
installation and ensure both Python and Node.js are available in PowerShell.

## Model download

The default setup downloads the project's fine-tuned GGUF from its public Google
Drive file (about 1.8 GB), validates it, and creates the `fastAPI_Model` Ollama
model locally. The large binary is kept outside Git.

For a fully offline install, place the optional artifact at:

```text
bot-training/models/fastAPI_Model/fastAPI_Model.Q4_K_M.gguf
```

Then pass `-Offline` (PowerShell) or `--offline` (POSIX shell) to the setup script.
The script verifies the exact size and GGUF signature and reports the SHA-256.
Training is not required for either installation path.

## Hardware and disk guidance

- 64-bit CPU and operating system.
- At least 8 GB RAM; 16 GB or more is recommended when running the frontend,
  backend, MySQL, and Ollama together.
- Several gigabytes of free disk space for Python packages, `node_modules`, the
  Ollama model cache, generated projects, logs, and database data.
- A GPU is optional. Ollama can run on the CPU, but generation will be slower.
- More memory may be needed for the configured `OLLAMA_NUM_CTX=8192` context.
  If the machine cannot sustain it, consult the troubleshooting guide before
  lowering it because smaller contexts can reduce code-generation quality.

## Network and local ports

| Port | Service | Required? |
| --- | --- | --- |
| `3000` | React development server | Yes for local UI |
| `3306` | MySQL | Yes, unless MySQL is configured on another port |
| `8000` | FastAPI/Uvicorn | Yes |
| `11434` | Ollama API | Yes for the default local model |
| `6379` | Redis | Optional for multi-process realtime fan-out |

Firewall or security software must allow the application to access these local
ports. Only expose them to other machines when you understand and configure the
security implications.

## Optional services

- **Redis:** improves WebSocket event distribution across multiple backend workers.
  Local single-process startup can continue without it.
- **SMTP account:** required for real verification/reset emails. In local
  development, email delivery is skipped and verification/reset codes are exposed
  only in the non-production API response so the local flow can still be tested.
- **Cloud LLM account:** optional. The backend supports user-owned OpenAI,
  Anthropic, Google, Ollama, and OpenAI-compatible settings. The included Ollama
  model is the default path.
- **Git:** recommended for development and version control but not required to run
  the extracted project.

## Development-only requirements

Backend linting, typing, and test tools are installed by:

```powershell
python -m pip install -e ".[dev]"
```

Frontend development and test dependencies are installed by `npm ci` from the
checked-in `package-lock.json`.

## Model-training-only requirements

Normal users should skip training. Rebuilding datasets or fine-tuning requires
substantially more disk, memory, time, and usually a CUDA-capable GPU or a hosted
notebook environment. Repository discovery also requires the Python package
`PyGithub` and a minimally scoped `GITHUB_TOKEN`. See
[bot-training/README.md](bot-training/README.md) for the supported workflow.
