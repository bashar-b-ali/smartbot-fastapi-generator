"""Run sequential create/edit scenarios through the durable project-run worker."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.project import Project
from app.models.project_run import ProjectAgentRun
from app.models.user import User
from app.repositories.project_run import ProjectRunRepository
from app.services.project_runs import project_run_worker, run_out
from app.services.runtime_validation import validate_runtime_project

SETTLED = {"completed", "completed_with_warnings", "needs_attention", "failed", "cancelled"}

SCENARIOS = [
    {
        "slug": "easy-notes",
        "name": "Live Eval Easy Notes",
        "prompts": [
            "Build a FastAPI backend for a notes app. It should manage notes with title, body, and pinned fields. Include create, list, detail, update, and delete endpoints. Let list notes filter by pinned.",
            "Add a tag field to notes and let list notes filter by tag.",
            "Add an endpoint GET /notes/pinned/download that downloads a text file containing all pinned notes with title and body.",
        ],
    },
    {
        "slug": "medium-team-task-tracker",
        "name": "Live Eval Team Task Tracker",
        "prompts": [
            "Build a FastAPI backend for a team task tracker. It should manage users and tasks. Tasks have title, description, status, priority, and assignee_id. Include JWT auth, user login/token handling, create, list, detail, update, and delete task endpoints. Let users filter tasks by status, priority, and assignee_id.",
            "Add roles to users and make delete task admin-only.",
            "Add an endpoint GET /tasks/unfinished/download that downloads a text file containing only the current user's unfinished tasks, including title and description.",
        ],
    },
    {
        "slug": "hard-school-operations",
        "name": "Live Eval School Operations",
        "prompts": [
            "Build a FastAPI backend for a school operations system. It should manage students, teachers, courses, enrollments, and assignments. Include JWT auth with user roles. Teachers can create assignments for their courses, students can list their own assignments, admins can delete courses, and list assignments can filter by course_id and status.",
            "Add submissions with assignment_id, student_id, content, grade, and submitted_at. Include create, list, detail, update, and delete endpoints, and let list submissions filter by assignment_id and student_id.",
            "Add an endpoint GET /students/me/unfinished-assignments/download that downloads a CSV report of the current student's unfinished assignments with title, course name, due date, and description.",
        ],
    },
]


def file_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    if not root.exists():
        return snapshot
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts or relative.endswith((".pyc", ".db")):
            continue
        snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def snapshot_delta(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    return {
        "created": sorted(after.keys() - before.keys()),
        "modified": sorted(path for path in before.keys() & after.keys() if before[path] != after[path]),
        "deleted": sorted(before.keys() - after.keys()),
    }


async def ensure_user() -> User:
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == "live-pipeline-eval@example.invalid"))
        if user is None:
            user = User(
                email="live-pipeline-eval@example.invalid",
                username=f"live_eval_{uuid4().hex[:10]}",
                password="live-evaluation-only",
                is_active=True,
                email_verified=True,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return user


async def create_project(user: User, name: str, root: Path) -> Project:
    root.mkdir(parents=True, exist_ok=False)
    async with SessionLocal() as db:
        project = Project(
            user_id=user.id,
            name=name,
            description="Automated live pipeline evaluation",
            folder_path=str(root),
            is_active=True,
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)
        return project


async def enqueue(project: Project, user: User, operation: str, prompt: str) -> ProjectAgentRun:
    async with SessionLocal() as db:
        run = await ProjectRunRepository(db).create(
            project_id=project.id,
            user_id=user.id,
            operation=operation,
            prompt=prompt,
            provider="auto",
            model_id=None,
            idempotency_key=f"live-eval:{project.id}:{operation}:{hashlib.sha256(prompt.encode()).hexdigest()[:16]}",
            request_json={"operation": operation, "provider": "auto", "source": "live_project_scenarios"},
        )
        await db.commit()
        await db.refresh(run)
        return run


async def existing_run(project_id: UUID, prompt: str) -> ProjectAgentRun | None:
    async with SessionLocal() as db:
        return await db.scalar(
            select(ProjectAgentRun)
            .where(
                ProjectAgentRun.project_id == project_id,
                ProjectAgentRun.prompt == prompt,
            )
            .order_by(ProjectAgentRun.created_at.desc())
            .limit(1)
        )


async def wait_for_run(run_id, label: str) -> dict:
    started = perf_counter()
    previous = ""
    while True:
        async with SessionLocal() as db:
            repo = ProjectRunRepository(db)
            run = await db.get(ProjectAgentRun, run_id)
            if run is None:
                raise RuntimeError(f"Run disappeared: {run_id}")
            state = f"{run.status}/{run.stage}/checkpoint-{run.current_checkpoint}"
            if state != previous:
                print(f"[{label}] {state}", flush=True)
                previous = state
            if run.status in SETTLED:
                output = (await run_out(repo, run)).model_dump(mode="json")
                output["wall_time_seconds"] = round(perf_counter() - started, 2)
                return output
        await asyncio.sleep(2)


async def main(
    output_root: Path,
    resume_root: Path | None = None,
    scenario_slugs: set[str] | None = None,
) -> int:
    if resume_root is not None:
        run_root = resume_root.resolve()
        partial_path = run_root / "report.partial.json"
        report = json.loads(partial_path.read_text(encoding="utf-8"))
        print(f"Resuming report: {partial_path}", flush=True)
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_root = output_root.resolve() / stamp
        run_root.mkdir(parents=True, exist_ok=False)
        report = {
            "started_at": datetime.now(UTC).isoformat(),
            "model": "fastAPI_model",
            "runner": "durable-project-run-worker",
            "root": str(run_root),
            "projects": [],
        }
    user = await ensure_user()
    selected_scenarios = [
        scenario
        for scenario in SCENARIOS
        if not scenario_slugs or scenario["slug"] in scenario_slugs
    ]
    await project_run_worker.start()
    try:
        for scenario in selected_scenarios:
            project_report = next(
                (item for item in report["projects"] if item["name"] == scenario["name"]),
                None,
            )
            if project_report is None:
                project_root = run_root / scenario["slug"]
                project = await create_project(user, scenario["name"], project_root)
                project_report = {
                    "name": scenario["name"],
                    "project_id": str(project.id),
                    "root": str(project_root),
                    "operations": [],
                }
                report["projects"].append(project_report)
            else:
                project_root = Path(project_report["root"])
                async with SessionLocal() as db:
                    project = await db.get(Project, UUID(project_report["project_id"]))
                if project is None:
                    raise RuntimeError(f"Resume project is missing: {project_report['project_id']}")
            start_index = len(project_report["operations"])
            for index, prompt in enumerate(scenario["prompts"]):
                if index < start_index:
                    continue
                operation = "create" if index == 0 else "edit"
                before = file_snapshot(project_root)
                run = await existing_run(project.id, prompt)
                if run is None:
                    run = await enqueue(project, user, operation, prompt)
                project_report["in_progress"] = {
                    "sequence": index + 1,
                    "operation": operation,
                    "prompt": prompt,
                    "run_id": str(run.id),
                    "before_snapshot": before,
                }
                (run_root / "report.partial.json").write_text(
                    json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                result = await wait_for_run(run.id, f"{scenario['slug']}:{operation}-{index + 1}")
                after = file_snapshot(project_root)
                project_report["operations"].append(
                    {
                        "sequence": index + 1,
                        "operation": operation,
                        "prompt": prompt,
                        "run": result,
                        "observed_file_delta": snapshot_delta(before, after),
                    }
                )
                project_report.pop("in_progress", None)
                (run_root / "report.partial.json").write_text(
                    json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            project_report["final_runtime_validation"] = await asyncio.to_thread(
                validate_runtime_project, project_root
            )
            project_report["final_files"] = sorted(file_snapshot(project_root))
    finally:
        await project_run_worker.stop()
    report["completed_at"] = datetime.now(UTC).isoformat()
    report_path = run_root / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"REPORT={report_path}", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("live_pipeline_results"),
    )
    parser.add_argument("--resume-root", type=Path)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=[scenario["slug"] for scenario in SCENARIOS],
        help="Run only the selected scenario; repeat to select more than one.",
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            main(
                args.output_root,
                args.resume_root,
                set(args.scenario or []),
            )
        )
    )
