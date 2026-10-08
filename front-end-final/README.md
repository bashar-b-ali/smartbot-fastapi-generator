# Smart Bot Frontend

React interface for the Smart Bot graduation project. It provides account flows,
the project dashboard, project generation/editing, generated-file inspection,
downloads, model settings, profile management, and realtime run updates.

For complete system installation, begin with the [root README](../README.md) and
[setup guide](../docs/SETUP_GUIDE.md).

## Requirements

- Node.js 18 LTS or newer.
- npm (installed with Node.js).
- A running Smart Bot backend, normally at `http://localhost:8000`.

## Install

From this folder:

```powershell
npm ci
```

`npm ci` installs exactly what is recorded in `package-lock.json`. Use `npm install`
only when intentionally changing dependencies.

## Configure and run

The frontend reads its API URL from `REACT_APP_API_URL`. If it is not set, the app
defaults to `http://localhost:8000/api/v1`.

```powershell
$env:REACT_APP_API_URL = "http://localhost:8000/api/v1"
npm start
```

Open `http://localhost:3000`. Environment variables must be set before `npm start`;
restart the development server after changing them.

## Main routes

| Route | Purpose |
| --- | --- |
| `/` | Public introduction |
| `/login` | Sign in |
| `/register` | Create account |
| `/forgot-password` | Password recovery |
| `/dashboard` | Project list and creation |
| `/projects/:projectId` | Project generation, chat, and files |
| `/settings` | Model/application settings |
| `/profile` | User profile |
| `/docs` | In-app system documentation |

Authenticated routes redirect signed-out users to `/login`.

## Source layout

```text
src/
  components/   Reusable UI, auth, chat, layout, and project components
  contexts/     Authentication, projects, runs, chat, and theme state
  pages/        Route-level screens
  services/     API and WebSocket client
  utils/        Formatting and metrics helpers
  App.jsx       Router and provider composition
  index.js      Browser entry point
```

## Commands

```powershell
npm start
npm test -- --watchAll=false
npm run build
```

- `npm start` starts the development server.
- `npm test -- --watchAll=false` runs tests once.
- `npm run build` creates an optimized bundle under `build/`.

Avoid `npm run eject`; it is irreversible and is not needed for normal development.

## Backend integration

- REST requests use the configured `/api/v1` base URL.
- WebSocket URLs are derived from the same API server origin.
- Protected requests use the current access token.
- The backend CORS configuration must include the frontend origin, normally
  `http://localhost:3000`.

If the UI loads but data does not, verify the backend health endpoint first:
`http://localhost:8000/api/v1/health`. Then see the
[troubleshooting guide](../docs/TROUBLESHOOTING.md).

## Related documentation

- [Root project guide](../README.md)
- [System requirements](../REQUIREMENTS.md)
- [User guide](../docs/USER_GUIDE.md)
- [Developer guide](../docs/DEVELOPER_GUIDE.md)
- [Backend API reference](../back-end-final/API_ENDPOINTS.md)
