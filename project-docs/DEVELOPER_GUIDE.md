# Developer Guide

## System at a glance

```text
Browser (React, port 3000)
        |
        | HTTP + WebSocket
        v
FastAPI API (port 8000)
   |         |          |
   v         v          v
 MySQL     Ollama     Redis (optional)
 data      AI model   realtime fan-out
```

The frontend calls the API base URL from `REACT_APP_API_URL`, defaulting to
`http://localhost:8000/api/v1`. The backend stores application records in MySQL,
generated/uploaded files under `back-end-final/media/`, and delegates local model
requests to Ollama.

## Repository responsibilities

### `front-end-final`

- React pages and reusable components.
- Authentication and project state contexts.
- HTTP and authenticated WebSocket client logic in `src/services/api.js`.
- Production bundle output in `build/`.

### `back-end-final`

- API entry point in `app/main.py` and routes in `app/api/v1/`.
- Settings, security, logging, and exception behavior in `app/core/`.
- SQLAlchemy models/repositories and Alembic database migrations.
- Generation and edit orchestration in `app/services/` and `app/llm/`.
- Runtime files and generated projects in `media/`.
- Automated tests in `tests/`.

### `bot-training`

- Packaged GGUF model and Ollama `Modelfile`.
- Runtime-aligned training/evaluation datasets.
- Evaluation reports and model card.
- Optional dataset-discovery and preparation utilities.
- Training notebook for an intentional training workflow.

## Backend development checks

Activate `back-end-final/.venv`, then run from `back-end-final`:

```powershell
python -m ruff check app tests
python -m compileall app tests
python -m mypy app
pytest
alembic heads
```

There should be one Alembic head. Optional live-model tests require Ollama:

```powershell
$env:RUN_OLLAMA_SMOKE = "1"
$env:OLLAMA_SMOKE_MODEL = "fastAPI_Model"
pytest tests\test_ollama_smoke.py
```

## Frontend development checks

Run from `front-end-final`:

```powershell
npm test -- --watchAll=false
npm run build
```

`npm ci` is preferred for a clean, reproducible install because a lock file is
included. Use `npm install` only when intentionally changing dependencies.

## Database migrations

Alembic migrations are the database schema history. Before running a changed
backend, apply them with:

```powershell
alembic upgrade head
```

Do not manually edit the production database to bypass migrations. Back up important
data before migration work.

## Configuration and secrets

- `.env.example` documents configuration names; `.env` holds local secrets.
- Never publish `.env`, provider keys, SMTP credentials, database passwords, JWTs,
  or verification/reset tokens.
- `SECRET_KEY` must contain at least 32 characters.
- Keep `OLLAMA_AUTO_PULL=false` for the packaged local model because it is created
  locally rather than pulled from the Ollama registry.
- CORS origins must include the actual frontend origin.

## Runtime data and logs

- `back-end-final/media/` may contain user uploads and generated source projects.
- `back-end-final/logs/` and server log files help diagnose startup and pipeline failures.
- `bot-training/reports/` contains evaluation artifacts, not application runtime logs.
- `front-end-final/build/` is generated output; `node_modules/` contains installed packages.

Before sharing the project, remove private runtime data only after making a backup
and confirming the exact files. Never assume all content under `media/` is disposable.

## API and pipeline references

- Endpoint reference: `back-end-final/API_ENDPOINTS.md`
- Runtime architecture: `back-end-final/README.md`
- Pipeline hardening: `back-end-final/docs/PIPELINE_HARDENING_PLAN.md`
- Token/context strategy: `back-end-final/docs/TOKEN_CONTEXT_STRATEGY.md`
- Model card: `bot-training/MODEL_CARD.md`

## Recommended change workflow

1. Read the relevant component README and existing tests.
2. Create or update tests for the intended behavior.
3. Make the smallest scoped source change.
4. Run the component checks above.
5. Run a complete product smoke flow: register, verify, create project, generate,
   inspect, edit, and download.
6. Review configuration/documentation impact before handing the change to another user.
