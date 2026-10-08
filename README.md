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
| `bot-training/` | Ready-to-run local GGUF model plus datasets, evaluation reports, and optional training utilities | Ollama, Qwen2.5 Coder | [Model README](bot-training/README.md) |

## Documentation map

- [System requirements](REQUIREMENTS.md) — software, hardware, ports, and optional services.
- [Installation and startup](docs/SETUP_GUIDE.md) — complete first-time setup in the correct order.
- [User guide](docs/USER_GUIDE.md) — how to use the product after it starts.
- [Developer guide](docs/DEVELOPER_GUIDE.md) — architecture, tests, logs, data, and safe development workflow.
- [Troubleshooting](docs/TROUBLESHOOTING.md) — solutions for common setup and runtime problems.
- [Backend API reference](back-end-final/API_ENDPOINTS.md) — endpoint-level reference.
- [Original Arabic startup notes](تعليمات%20التشغيل.txt) — the earlier concise operating instructions.

## Quick start (Windows PowerShell)

Read [REQUIREMENTS.md](REQUIREMENTS.md) first. Then open PowerShell in this
directory and run the following sections in order.

### 1. Register the included model with Ollama

Install and start Ollama, then run:

```powershell
cd .\bot-training
.\setup_fastAPI_Model.ps1
ollama list
cd ..
```

`fastAPI_Model:latest` should appear in the list. The script uses the included
GGUF file; it does not download a base model.

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
[setup guide](docs/SETUP_GUIDE.md) for email verification, alternate model
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
- `bot-training/models/` contains a large model artifact. Do not move or rename it
  unless you also update the model setup instructions.
- The `bot-training` data-building and training workflow is not required for normal
  application use.

## Documentation-only maintenance

The onboarding improvements in this directory are documentation only. They do not
change application source code, package manifests, model files, datasets, or runtime
behavior.
