# Smart Bot — Graduation Project

Smart Bot is a local full-stack application that helps a user describe, generate,
inspect, and refine FastAPI projects with an AI model. This directory contains the
web interface, the FastAPI service, and the local model/training package needed to
run the complete product.

> Start here if this is your first time using the project. The application code is
> split across the three folders below; the files in `docs/` explain how to install,
> run, use, test, and troubleshoot the complete system.

## Project folders

| Folder | Purpose | Main technology | Detailed guide |
| --- | --- | --- | --- |
| `front-end-final/` | Browser interface for accounts, projects, generation, chat, files, and settings | React 18, Tailwind CSS | [Frontend README](front-end-final/README.md) |
| `back-end-final/` | REST/WebSocket API, authentication, database, project files, and model orchestration | FastAPI, SQLAlchemy, MySQL | [Backend README](back-end-final/README.md) |
| `bot-training/` | Fine-tuned model installer plus datasets, evaluation reports, and optional training utilities | Ollama, Qwen2.5 Coder | [Model README](bot-training/README.md) |

## Documentation map

- [System requirements](REQUIREMENTS.md) — software, hardware, ports, and optional services.
- [Installation and startup](project-docs/SETUP_GUIDE.md) — complete first-time setup in the correct order.
- [User guide](project-docs/USER_GUIDE.md) — how to use the product after it starts.
- [Developer guide](project-docs/DEVELOPER_GUIDE.md) — architecture, tests, logs, data, and safe development workflow.
- [Troubleshooting](project-docs/TROUBLESHOOTING.md) — solutions for common setup and runtime problems.
- [Backend API reference](back-end-final/API_ENDPOINTS.md) — endpoint-level reference.

## Quick start (Windows PowerShell)

Read [REQUIREMENTS.md](REQUIREMENTS.md) first. Then open PowerShell in this
directory and run the following sections in order.

### 1. Install the public model with Ollama

Install and start Ollama, then run:

```powershell
cd .\bot-training
.\setup_fastAPI_Model.ps1
ollama list
cd ..
```

`fastAPI_Model:latest` should appear in the list. On first use, the script
downloads the project's fine-tuned GGUF (about 1.8 GB) from its public Google
Drive file, validates it, and registers it with Ollama. Interrupted downloads
are resumed automatically.

On Linux or macOS, use `./setup_fastAPI_Model.sh` instead. An already downloaded,
valid GGUF can be installed without a network connection by adding `-Offline` on
Windows or `--offline` on Linux/macOS.

### 2. Create the MySQL database

Run this in MySQL Workbench or the `mysql` client:

```sql
CREATE DATABASE new_back
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
```

### 3. Configure and start the backend

```powershell
cd .\back-end-final
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Edit `.env` and set at least `SECRET_KEY`, `DB_USER`, and `DB_PASSWORD`. Generate
a safe development secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Then apply the database migrations and start the API:

```powershell
alembic upgrade head
python scripts\dev_server.py
```

Confirm that [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)
responds before starting the frontend. Interactive API documentation is available
at [http://localhost:8000/docs](http://localhost:8000/docs).

### 4. Start the frontend

Keep the backend terminal open. Open a second PowerShell window in the project root:

```powershell
cd .\front-end-final
npm ci
$env:REACT_APP_API_URL = "http://localhost:8000/api/v1"
npm start
```

Open [http://localhost:3000](http://localhost:3000). See the
[setup guide](project-docs/SETUP_GUIDE.md) for email verification, alternate model
providers, optional Redis, validation checks, and shutdown instructions.

## Normal startup after the first installation

You do not need to reinstall dependencies or recreate the database each time.

1. Start MySQL and Ollama.
2. In one PowerShell window, activate `back-end-final\.venv` and run
   `python scripts\dev_server.py`.
3. In another window, enter `front-end-final` and run `npm start`.
4. Open `http://localhost:3000`.

## Important safety notes

- Never share or commit `back-end-final/.env`, API keys, passwords, or access tokens.
- `back-end-final/media/` contains user uploads and generated projects. Back it up
  before deleting or replacing it.
- GGUF files in `bot-training/models/` are ignored by Git. The installer downloads
  the fine-tuned model separately, avoiding a multi-gigabyte Git clone.
- The `bot-training` data-building and training workflow is not required for normal
  application use.

## Model distribution

The fine-tuned GGUF is intentionally not committed to Git. Its public Drive file
ID and expected byte size are recorded in the manifest, and the setup scripts
download, validate, and register it with Ollama.
