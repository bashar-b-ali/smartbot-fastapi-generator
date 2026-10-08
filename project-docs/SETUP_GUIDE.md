# Installation and Startup Guide

This guide sets up the complete application on Windows using PowerShell. Run all
commands from the project root unless a step says otherwise.

## 1. Verify prerequisites

Install the software listed in [REQUIREMENTS.md](../REQUIREMENTS.md), then verify:

```powershell
python --version
node --version
npm --version
ollama --version
mysql --version
```

If `mysql` is not on `PATH`, it is fine to use MySQL Workbench for database steps.

## 2. Prepare the local AI model

Start the Ollama application, then run:

```powershell
cd .\bot-training
.\setup_fastAPI_Model.ps1
ollama list
```

Expected model name: `fastAPI_Model:latest`.

Optional direct test:

```powershell
ollama run fastAPI_Model
```

Enter a short prompt, then type `/bye` to exit. Return to the root directory:

```powershell
cd ..
```

## 3. Create the database

Start MySQL and execute:

```sql
CREATE DATABASE new_back
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
```

Create a dedicated application user if this machine is shared or used beyond a
local demonstration. Grant that user access only to `new_back`, then use its name
and password in the backend `.env` file.

## 4. Install backend dependencies

```powershell
cd .\back-end-final
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

If PowerShell blocks virtual-environment activation, run this once in the current
terminal, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 5. Configure the backend

Open `back-end-final/.env`. Do not edit `.env.example`; it is the safe template.

At minimum, set:

```env
SECRET_KEY=<random value of at least 32 characters>
DB_HOST=localhost
DB_PORT=3306
DB_NAME=new_back
DB_USER=<your MySQL user>
DB_PASSWORD=<your MySQL password>
LLM_PROVIDER=ollama
LLM_MODEL=fastAPI_Model
OLLAMA_HOST=http://localhost:11434
OLLAMA_NUM_CTX=32768
OLLAMA_AUTO_PULL=false
OLLAMA_AUTO_WARMUP=true
```

Generate `SECRET_KEY` locally:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

For real email delivery, also configure the `SMTP_*` and `EMAIL_FROM` values. For
Gmail, use an App Password rather than the normal account password. Leave the
credentials blank for log-only development behavior.

## 6. Initialize and start the backend

With the virtual environment active and from `back-end-final`:

```powershell
alembic upgrade head
python scripts\dev_server.py
```

Do not close this terminal. Verify:

- Health: `http://localhost:8000/api/v1/health`
- API documentation: `http://localhost:8000/docs`
- Model health: `http://localhost:8000/api/v1/chatbot/health`

The first model request may be slower while Ollama loads the model.

## 7. Install and start the frontend

Open a new PowerShell window in the project root:

```powershell
cd .\front-end-final
npm ci
$env:REACT_APP_API_URL = "http://localhost:8000/api/v1"
npm start
```

The browser should open `http://localhost:3000`. Keep both backend and frontend
terminals open while using the product.

## 8. First-use check

1. Open the landing page and create an account.
2. Verify the account. With SMTP disabled in a non-production environment, the
   interface/API response exposes the development verification code instead of
   sending an email.
3. Sign in and create a project.
4. Submit a small FastAPI project request.
5. Wait for generation to finish, inspect the files, and download the result.

## Normal startup on later days

1. Start MySQL and Ollama.
2. Start the backend:

   ```powershell
   cd .\back-end-final
   .\.venv\Scripts\Activate.ps1
   python scripts\dev_server.py
   ```

3. In a second terminal, start the frontend:

   ```powershell
   cd .\front-end-final
   $env:REACT_APP_API_URL = "http://localhost:8000/api/v1"
   npm start
   ```

## Stop the application

Press `Ctrl+C` once in the frontend terminal and once in the backend terminal.
Ollama and MySQL can then be stopped through Windows Services or their respective
applications if they are no longer needed.
