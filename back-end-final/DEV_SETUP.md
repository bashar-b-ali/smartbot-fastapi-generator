# Full Product Development Setup

This backend works with the sibling React app at `../front-end-final`.

## Backend

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Set at least:

- `SECRET_KEY`: 32+ characters.
- `DB_*`: MySQL-compatible database settings.
- `LLM_PROVIDER` and `LLM_MODEL`: usually `ollama` and `fastAPI_Model` locally.
- `OLLAMA_AUTO_WARMUP=false` if Ollama is not running during backend-only checks.
- `OLLAMA_AUTO_PULL=false` for local packaged models such as `fastAPI_Model`.

Run:

```powershell
alembic upgrade head
python scripts/dev_server.py
```

Quality gates:

```powershell
python -m ruff check app tests
python -m mypy app
python -m compileall app tests
pytest
git diff --check
```

`python -m mypy app` is green with strict checking plus explicit overrides for legacy dynamic LLM/generation modules. Tighten those overrides by subsystem as the next hardening track.

## Frontend

From the sibling repo:

```powershell
cd ..\front-end-final
npm install
$env:REACT_APP_API_URL = "http://localhost:8000/api/v1"
npm start
```

Useful checks:

```powershell
npm run build
npm test -- --watchAll=false
```

## Local Services

- MySQL is required for the normal backend runtime.
- Redis is used for realtime fan-out; if unavailable, realtime startup logs a warning and continues.
- Ollama is required for the default local model path. Create the local model from the sibling training/runtime package with:

```powershell
cd ..\bot-training
.\setup_fastAPI_Model.ps1
cd ..\back-end-final
```

Set `OLLAMA_AUTO_PULL=true` only when `LLM_MODEL` is a registry-pullable model,
for example `llama3.2` or `deepseek-r1:8b`.

## Product Smoke Flow

1. Start backend on `http://localhost:8000`.
2. Start frontend on `http://localhost:3000`.
3. Register and verify a user.
4. Create a project.
5. Use the project generation dialog to preview, then generate files.
6. Open the changed file in the file viewer.
7. Ask the chat assistant for a small edit and confirm the file viewer refreshes.
8. Download the generated project zip.

## Current Improvement Baseline

- Backend `ruff`, `compileall`, and `pytest` should pass.
- Backend `mypy app` should pass.
- Backend Alembic should report one head.
- Backend strict mypy overrides identify the remaining legacy typing debt.
- Backend pipeline validation is artifact-gated; static-safe but incomplete generations must not become accepted project state.
- Frontend currently stores JWTs in `localStorage`; keep this behavior until auth storage is redesigned across both apps.
