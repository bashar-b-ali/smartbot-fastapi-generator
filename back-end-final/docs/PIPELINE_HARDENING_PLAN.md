# Project Generation and Editing Hardening Plan

## Objective

Every create or edit request must reach an observable terminal result, preserve previously accepted behavior, and be reported as completed only when the generated FastAPI application imports and every critical user requirement is verified.

The model may take as long as needed. Safety limits apply only to disposable validation processes, never to model generation or durable-run recovery.

## Non-negotiable invariants

1. Candidate changes are produced in a temporary project and published only after validation.
2. A rejected edit changes zero accepted project files.
3. Existing files, routes, models, fields, filters, and authentication rules survive an edit unless the request explicitly removes them.
4. Missing dependencies, skipped validation, missing critical artifacts, and runtime failures cannot produce a completed status.
5. Every explicit route, field, filter, role rule, ownership rule, and download format is represented in a typed contract.
6. Durable runs expose planning, implementation, validation, repair, and terminal state to the frontend.
7. An interrupted run resumes from durable state without duplicating or undoing accepted work.

## Phase 1: truthful acceptance

- Fail runtime validation when a declared dependency is unavailable instead of treating the check as passed.
- Require static validation, artifact validation, runtime import, OpenAPI generation, and smoke checks for creation and editing.
- Remove warning-only bypasses for critical artifacts.
- Permit completed_with_warnings only when all critical checks passed and warnings are noncritical.

Acceptance:

- A project with a missing import is rejected before write.
- A project with a missing required route or filter is rejected before write.
- Runtime validation that executes zero checks is not accepted.

## Phase 2: typed deterministic contracts

- Parse explicit METHOD /path expressions without model assistance.
- Represent download media type, report columns, role access, ownership scope, authentication, fields, filters, and relationships as typed artifacts.
- Merge deterministic intent above model-proposed intent.
- Reject critical requirements that have no verifiable artifacts.

Acceptance:

- All nine benchmark prompts compile into complete contracts.
- The three download requests contain exact required routes and response-format checks.
- Admin-only and current-user requirements produce access-rule checks.

## Phase 3: baseline preservation

- Capture the current project index before each edit.
- Load accepted historical contracts.
- Derive a current baseline contract from routes, tables, fields, filters, relationships, router wiring, and local imports.
- Compare the validated candidate with the baseline.
- Allow removals only when explicitly present in removed_artifacts.

Acceptance:

- Removing task CRUD during a role edit is rejected.
- Circular or unresolved local imports are rejected.
- Adding one endpoint cannot remove or weaken an existing endpoint.

## Phase 4: bounded edit units and checkpoints

- Convert the typed contract and file dependency graph into ordered edit units.
- Keep units small: schema, model, data access/service, route, wiring, tests, documentation.
- Generate one unit, validate the complete temporary project, then record a checkpoint.
- Publish to the accepted project only after all critical units pass final validation.
- Resume from the first unfinished unit after interruption.

Acceptance:

- Durable runs expose multiple meaningful checkpoints.
- Completed units are not regenerated during resume.
- A repair receives one failing check and only its relevant files.

## Phase 5: context and Qwen 3B policy

- Current prompt and current filesystem outrank saved model memory.
- Schema memory is excluded from a no-schema-change edit.
- Send only affected files and direct dependencies.
- Use low-temperature structured planning, explicit allowed paths, complete small-file responses, and focused repairs.
- Never train on rejected or falsely accepted traces.

Acceptance:

- A download-only request cannot produce a schema migration plan.
- Selected context records why each file is included.
- Repair scope cannot expand without a new plan checkpoint.

## Phase 6: frontend contract

- Preserve project-run.v1 compatibility while adding validation summaries and unit checkpoints.
- Show exact current unit, attempts, accepted files, skipped checks, missing artifacts, regressions, and recovery actions.
- Reconnect by run ID and event sequence after network loss.
- Never infer failure from an HTTP disconnect.

Acceptance:

- Frontend tests cover completed, completed_with_warnings, needs_attention, waiting_for_model, resume, retry, and cancellation.
- Critical validation failures are visible without reading backend logs.

## Phase 7: benchmark gate

Run the exact Easy, Medium, and Hard create/edit/edit sequence.

Release criteria:

- Three of three final applications import.
- Every required OpenAPI route and query parameter exists.
- Generated behavioral tests pass.
- All previous requirements remain valid after both edits.
- Every rejected edit has an empty filesystem delta.
- Every operation has a durable terminal response.
- No critical failure is labeled completed or completed_with_warnings.
