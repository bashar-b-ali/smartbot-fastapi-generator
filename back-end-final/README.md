# Smart Bot Backend

FastAPI backend for an AI-powered backend project generator. Users describe a
project or request an edit, and the service plans, writes, indexes, validates,
and returns generated FastAPI project files.

Research notes, training material, datasets, and fine-tuning artifacts belong in
the sibling workspace `../bot-training/`. This repository is the runtime backend
only.

For a first-time full-product installation, start with the
[root README](../README.md), [system requirements](../REQUIREMENTS.md), and
[setup guide](../docs/SETUP_GUIDE.md).

## Stack

- FastAPI 0.115+ with Pydantic v2
- SQLAlchemy 2.x async with MySQL via `aiomysql`
- Alembic migrations
- Redis-backed WebSocket fan-out
- PyJWT plus passlib[bcrypt] auth
- structlog, slowapi, aiosmtplib
- LLM providers: server-owned Ollama default plus user-owned OpenAI, Anthropic, Google, Ollama, and OpenAI-compatible model settings

## Runtime Layout

```text
app/
  main.py                    FastAPI app factory, lifespan, middleware
  core/                      settings, crypto, security, errors, logging
  db/                        declarative base, async session factory, mixins
  models/                    SQLAlchemy ORM models
  schemas/                   Pydantic API contracts
  repositories/              async DB queries
  services/                  business logic and integrations
    generator.py             generation pipeline orchestration
    project_editor.py        edit pipeline orchestration
    project_indexer.py       AST index, endpoint docs, artifact facts
    model_pipeline/          model-owned schema/API/file/edit pipeline stages
    project_edit/            edit validation, context, IO, memory
  api/v1/                    auth, users, projects, chatbot, models, ws
  llm/                       providers, planner, factory, prompts, parsers, writer
  realtime/manager.py        WebSocket connection registry and Redis pub/sub
alembic/                     database migrations
media/                       user uploads and generated project folders
tests/                       pipeline and artifact-gate tests
```

## Pipeline Contract

The active code/files path is model-owned generation through
`POST /api/v1/chatbot/projects/{project_id}/generate-from-prompt`. Deprecated
compatibility generation routes are disabled and point callers to that endpoint.

Generation flow:

1. Schema plan: the model proposes compact database/schema facts.
2. API contract: the model converts the request into explicit routes, tables,
   fields, filters, relationships, and behavior artifacts.
3. File plan: the model chooses the files to write for the FastAPI profile.
4. Per-file generation: each planned file is generated with bounded context.
5. Validation: static checks and artifact checks run against a temp project.
6. Persistence: accepted contracts become active memory; failed or draft runs
   remain inspectable as debug/recent history.

The generation/edit pipeline accepts a change only when both gates pass:

- Static validation: Python parses, local imports resolve, required scaffold files exist.
- Artifact validation: deterministic project-index facts satisfy required routes, tables, fields, filters, relationships, behaviors, and previous accepted contracts.

AI semantic coverage is recorded as notes only. It is not allowed to mark a
result accepted. Failed or no-change contracts stay in recent/debug history and
do not become active future context.

Pipeline response states:

- `accepted`: static and artifact validation passed.
- `draft_written`: static-safe files were written but artifact validation still has gaps.
- `rejected_before_write`: static validation or regression checks blocked persistence.
- `edit_no_change`: the edit did not produce an active source mutation.
- `legacy_generation_disabled`: deprecated generation endpoint was called.

Pipeline inspection:

- `GET /api/v1/chatbot/projects/{project_id}/pipeline-audit`
- `GET /api/v1/chatbot/projects/{project_id}/pipeline-runs`
- `GET /api/v1/chatbot/projects/{project_id}/pipeline-runs/{run_id}`

Inspection output is bounded and redacted before it reaches the UI.

Long-running local model requests:

- Use `POST /api/v1/chatbot/projects/{project_id}/chat/start` instead of holding one `/chat` request open.
- Poll `GET /api/v1/chatbot/projects/{project_id}/chat/jobs/{job_id}` until `status` is `succeeded` or `failed`.
- The completed `result` is the same `ChatResponse` shape returned by the synchronous `/chat` route.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

Create `.env` from your local template and set at least `SECRET_KEY` and the DB
settings. For local development, Ollama can be the default provider:

```env
LLM_PROVIDER=ollama
LLM_MODEL=fastAPI_Model
OLLAMA_HOST=http://localhost:11434
OLLAMA_NUM_CTX=8192
OLLAMA_AUTO_WARMUP=true
OLLAMA_AUTO_PULL=false
OLLAMA_KEEP_ALIVE=24h
```

For the default local `fastAPI_Model`, create the model from the sibling training/runtime package before startup:

```bash
cd ..\bot-training
.\setup_fastAPI_Model.ps1
cd ..\back-end-final
```

The backend never pulls Ollama models automatically. Install or create the selected model locally before startup.
`llama3.2` or `deepseek-r1:8b`.

SMTP fields can stay empty in development; emails are logged instead of sent.

## Database

The project uses Alembic as the source of schema truth.

```bash
alembic heads
alembic upgrade head
```

The current migration chain removes unused legacy research tables and adds
`project_helpers.requirement_contracts` for accepted artifact contracts.

## Run

```bash
python scripts/dev_server.py
```

- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/v1/health
- LLM health: http://localhost:8000/api/v1/chatbot/health

`media/` is excluded from reload because generated project files are written
there. Without that exclusion, a generation can restart the backend during chat.

## Quality Gates

Run these before presenting or committing:

```bash
python -m ruff check app tests
python -m compileall app tests
pytest
git diff --check
```

Recommended optional checks:

```bash
alembic heads
mypy app
```

`mypy` is configured as strict, so expect to fix typing incrementally if it has
not been kept green continuously.

Optional live-model pipeline smoke tests require a running Ollama server and are
skipped by default:

```bash
$env:RUN_OLLAMA_SMOKE = "1"
$env:OLLAMA_SMOKE_MODEL = "fastAPI_Model"
pytest tests/test_ollama_smoke.py
```

## Auth

JWT access and refresh tokens are stateless.

1. `POST /api/v1/auth/register`
2. `POST /api/v1/auth/verify-email`
3. `POST /api/v1/auth/login`
4. Send `Authorization: Bearer <access_token>` on protected routes.
5. `POST /api/v1/auth/refresh` when access expires.
6. WebSocket connections authenticate with `?token=<access_token>`.

## LLM Model Policy

- The server default model is configured through `.env`.
- User-owned provider settings are managed through `/api/v1/chatbot/models/custom`.
- `model_id=None` means use the user's default private model if present; otherwise use the server-owned default.
- Chat is scoped to FastAPI project files. Off-topic requests are refused before LLM calls.
- Code changes are written through the generator/editor pipeline, not pasted into chat responses.

## Realtime Events

Server code publishes through `realtime.manager.publish(channel, event)`.

- `user:{user_id}:projects`
- `user:{user_id}:chat`
- `project:{project_id}:chat`

Redis pub/sub fans events out across Uvicorn workers.

## Security Notes

- Keep secrets in `.env`; never commit provider keys, SMTP passwords, JWT secrets, or database passwords.
- Rotate any credential that was ever committed before sharing this repository.
- File operations resolve paths and reject traversal outside the project root.
- Error responses use `{ "error": { "code": "...", "message": "...", "details": {...} } }`.
- Database integrity errors are returned as generic conflicts to avoid schema leakage.

## Related Guides

- [Full setup](../docs/SETUP_GUIDE.md)
- [Product user guide](../docs/USER_GUIDE.md)
- [Developer guide](../docs/DEVELOPER_GUIDE.md)
- [Troubleshooting](../docs/TROUBLESHOOTING.md)
- [API endpoints](API_ENDPOINTS.md)
- [Backend development setup](DEV_SETUP.md)
- [Project requirements](PROJECT_REQUIREMENTS.md)
