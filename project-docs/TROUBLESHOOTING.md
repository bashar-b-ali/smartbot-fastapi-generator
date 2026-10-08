# Troubleshooting

Start with the first error shown in the relevant terminal. Later messages are often
effects of the same root problem.

## A command is not recognized

- `python`: install Python 3.11+ and enable **Add Python to PATH**, or try `py -3.11`.
- `node` / `npm`: install Node.js 18 LTS or newer and restart PowerShell.
- `mysql`: use MySQL Workbench or add the MySQL `bin` directory to `PATH`.
- `ollama`: install/start Ollama and reopen the terminal.
- `alembic`: activate `back-end-final/.venv` and reinstall with
  `python -m pip install -e ".[dev]"`.

## PowerShell will not activate `.venv`

Use a process-only policy change (it ends when the terminal closes):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Backend reports a settings or `SECRET_KEY` error

Confirm that `back-end-final/.env` exists and that `SECRET_KEY` is at least 32
characters. Create `.env` from `.env.example`, not from an unrelated project.

## Backend cannot connect to MySQL

1. Confirm the MySQL service is running.
2. Check `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` in `.env`.
3. Confirm the `new_back` database exists.
4. Confirm the selected user has access to that database.
5. Run `alembic upgrade head` after connectivity is restored.

Errors mentioning access denied usually indicate credentials/permissions. Connection
refused usually means the host, port, or MySQL service is wrong.

## `fastAPI_Model` is missing

Confirm Ollama is running and the GGUF exists at:

```text
bot-training/models/fastAPI_Model/fastAPI_Model.Q4_K_M.gguf
```

Then rerun:

```powershell
cd .\bot-training
.\setup_fastAPI_Model.ps1
ollama list
```

The setup script validates the local artifact before registering it. Do not enable
`OLLAMA_AUTO_PULL` for this locally packaged model.

## Ollama is slow or runs out of memory

- Close other memory-heavy applications.
- Let the first request finish; initial model loading is slower.
- Check that Ollama can run the model directly with `ollama run fastAPI_Model`.
- CPU-only inference is expected to be slower than GPU inference.
- A smaller `OLLAMA_NUM_CTX` uses less memory but may reduce generation/edit quality.
  Change it only after confirming memory pressure is the cause.

## Frontend cannot reach the backend

1. Open `http://localhost:8000/api/v1/health` directly.
2. Start the frontend terminal with:

   ```powershell
   $env:REACT_APP_API_URL = "http://localhost:8000/api/v1"
   npm start
   ```

3. Restart `npm start` after changing a `REACT_APP_*` variable; Create React App
   reads these values at startup.
4. Check that the frontend origin is present in backend `CORS_ORIGINS`.
5. Verify that ports `3000` and `8000` are not used by unrelated programs.

## Registration email does not arrive

If SMTP credentials are intentionally empty, no message is sent; inspect the backend
registration/resend response for the development verification code. This code is
exposed only outside production. For Gmail, enable two-step verification and use a
Google App Password. Do not use the normal Gmail password.

## Realtime updates do not appear

Refresh the browser and confirm the access token/session is still valid. Check the
browser console and backend logs for WebSocket errors. Redis is optional for a single
backend process but should be running and match `REDIS_URL` for multi-worker fan-out.

## `npm ci` fails

Confirm Node/npm versions first. Then run `npm ci` from `front-end-final`, where both
`package.json` and `package-lock.json` exist. Do not delete the lock file merely to
hide a dependency error; record the first error and resolve its stated cause.

## A generated project is incomplete

Inspect its pipeline outcome. `draft_written` and `rejected_before_write` are not
accepted results. Make the request smaller and more explicit, include missing fields
or endpoints, and submit one focused correction. Check backend pipeline logs/audit
details if repeated requests fail at the same validation gate.

## Port already in use

Identify the existing process before stopping anything:

```powershell
Get-NetTCPConnection -LocalPort 3000,3306,6379,8000,11434 -ErrorAction SilentlyContinue |
  Select-Object LocalPort, State, OwningProcess
```

Do not terminate a process until you know what it is. Either close the application
that owns the port or configure the intended service to use a different port and
update every dependent URL/configuration value consistently.
