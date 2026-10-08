# User Guide

This guide assumes the backend and frontend are already running and the browser is
open at `http://localhost:3000`.

## 1. Account access

1. Select **Register** and enter the requested account details.
2. Complete email verification. In a non-production setup without SMTP, use the
   development code returned by the interface/API response.
3. Sign in. Protected pages redirect unauthenticated users to the login page.
4. Use **Profile** to review account details and **Settings** for application/model
   preferences available to the account.

Do not paste provider API keys into chat messages or project prompts. Add them only
through the intended model settings interface.

## 2. Create a project

1. Open the dashboard.
2. Create a new project with a short, meaningful name and description.
3. Open the project from the dashboard.
4. Describe the desired backend clearly, including entities, fields, relationships,
   authentication rules, endpoints, filters, and important behaviors.

Example request:

```text
Create a task-management FastAPI API. Users can register and own projects. Each
project has tasks with title, description, status, due date, and created date.
Add CRUD endpoints, filter tasks by status, and prevent users from accessing other
users' projects.
```

Specific prompts produce more reliable contracts than requests such as “make a
complete website.” This model generates FastAPI backend projects, not arbitrary
desktop or mobile applications.

## 3. Generate and inspect files

1. Review the generation preview/plan when it is shown.
2. Start generation and wait for the run to reach a final state.
3. Open generated files in the project file viewer.
4. Review the API documentation generated with the project.
5. Download the project archive when the result is ready.

Possible pipeline outcomes include:

- `accepted`: static and artifact validation passed.
- `draft_written`: files are syntactically safe, but requested artifacts still have gaps.
- `rejected_before_write`: validation prevented unsafe/incomplete persistence.
- `edit_no_change`: the edit did not change an active source file.

A draft or rejected run is useful diagnostic information, not a confirmed complete
implementation.

## 4. Request an edit

Open the relevant project and state one focused change at a time. Mention exact
entities, endpoints, or behavior when possible.

Good example:

```text
Add a priority field to Task with low, medium, and high values. Accept it in create
and update requests, return it in responses, and add a list filter for priority.
```

After the edit:

1. Check the reported status.
2. Inspect the changed files.
3. Confirm earlier requirements were preserved.
4. Download a fresh archive if you need the updated project outside Smart Bot.

## 5. Model selection

The default server-owned model is `fastAPI_Model` through Ollama. If enabled by the
backend, a user can configure a private provider/model in Settings. Provider keys
are account-specific and should never be committed to files or shared in screenshots.

## 6. Common usage advice

- Ask for one coherent project or one focused edit per request.
- State authorization and ownership rules explicitly.
- Name required fields, filters, and relationships.
- Inspect generated code before running it in a production environment.
- Treat generated projects as development output: add production secrets,
  deployment configuration, monitoring, backups, and security review separately.
- If the interface stops updating during a long generation, check the run status
  and backend logs before submitting the same request again.
