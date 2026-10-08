from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.llm.project_validator import validate_written_project
from app.llm.writer import WriteOutcome
from app.services.project_context import build_project_context
from app.services.project_edit.artifact_validation import (
    build_preservation_contract,
    normalize_requirement_contract,
    validate_artifacts,
)
from app.services.project_edit.context import compact_project_state
from app.services.project_edit.memory import persist_requirement_contract, prepare_edit_memory
from app.services.project_edit.results import finish_edit_result, no_change_result
from app.services.project_edit.selection import select_edit_files
from app.services.project_indexer import build_project_index


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def base_project(root: Path, *, wire_tickets: bool = True, where_filter: bool = True) -> None:
    write(
        root / "database.py",
        """
from sqlmodel import Session, create_engine

engine = create_engine("sqlite:///test.db")

def get_session():
    with Session(engine) as session:
        yield session
""".lstrip(),
    )
    write(root / "requirements.txt", "fastapi\nsqlmodel\n")
    include = "app.include_router(tickets_router)\n" if wire_tickets else ""
    where_block = (
        """
    if status is not None:
        stmt = stmt.where(Ticket.status == status)
""".rstrip()
        if where_filter
        else ""
    )
    write(
        root / "main.py",
        f"""
from fastapi import FastAPI
from routers.tickets import router as tickets_router

app = FastAPI()

{include}
""".lstrip(),
    )
    write(root / "routers" / "__init__.py", "")
    write(
        root / "routers" / "tickets.py",
        f"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Field, Session, SQLModel, select

from database import get_session

router = APIRouter(prefix="/tickets", tags=["tickets"])


class Ticket(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    status: str = Field(index=True)
    created_at: str


class TicketRead(BaseModel):
    id: int
    status: str


@router.get("", response_model=list[TicketRead])
def list_tickets(status: str | None = None, session: Session = Depends(get_session)):
    stmt = select(Ticket)
{where_block}
    return session.exec(stmt).all()
""".lstrip(),
    )


def test_semantic_relationship_validation_rejects_mismatched_id_field(tmp_path: Path) -> None:
    write(
        tmp_path / "models.py",
        """
from sqlmodel import Field, SQLModel


class Order(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    customer_id: str | None = Field(default=None, index=True)


class Customer(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
""".lstrip(),
    )

    validation = validate_artifacts(
        build_project_index(tmp_path),
        {
            "required_tables": [{"table": "Order"}, {"table": "Customer"}],
            "required_fields": [{"table": "Order", "field": "customer_id"}],
        },
    )

    assert validation.passed is False
    assert any("should foreign key Customer.id" in item for item in validation.missing_artifacts)
    assert any("type mismatch" in item for item in validation.missing_artifacts)


def test_semantic_relationship_validation_accepts_matching_foreign_key(tmp_path: Path) -> None:
    write(
        tmp_path / "models.py",
        """
from sqlmodel import Field, SQLModel


class Order(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    customer_id: int | None = Field(default=None, foreign_key="customers.id", index=True)


class Customer(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
""".lstrip(),
    )

    validation = validate_artifacts(
        build_project_index(tmp_path),
        {
            "required_tables": [{"table": "Order"}, {"table": "Customer"}],
            "required_fields": [{"table": "Order", "field": "customer_id"}],
        },
    )

    assert validation.passed is True
    assert validation.missing_artifacts == []

def test_relationship_contract_accepts_llm_name_aliases(tmp_path: Path) -> None:
    write(
        tmp_path / "models.py",
        """
from sqlmodel import Field, SQLModel


class Book(SQLModel, table=True):
    __tablename__ = "books"
    id: int | None = Field(default=None, primary_key=True)
    author_id: int | None = Field(default=None, foreign_key="authors.id", index=True)


class Author(SQLModel, table=True):
    __tablename__ = "authors"
    id: int | None = Field(default=None, primary_key=True)
""".lstrip(),
    )

    validation = validate_artifacts(
        build_project_index(tmp_path),
        {
            "required_relationships": [
                {
                    "table_name": "books",
                    "column_name": "author_id",
                    "related_table_name": "authors",
                    "related_column_name": "id",
                }
            ],
        },
    )

    assert validation.passed is True
    assert validation.missing_artifacts == []


def multi_resource_project(root: Path) -> None:
    write(
        root / "database.py",
        """
from sqlmodel import Session, create_engine

engine = create_engine("sqlite:///test.db")


def get_session():
    with Session(engine) as session:
        yield session
""".lstrip(),
    )
    write(
        root / "auth.py",
        """
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True)
""".lstrip(),
    )
    write(
        root / "main.py",
        """
from fastapi import FastAPI
from routers.article import router as article_router
from routers.auth import router as auth_router
from routers.review import router as review_router

app = FastAPI()
app.include_router(auth_router)
app.include_router(article_router)
app.include_router(review_router)
""".lstrip(),
    )
    write(root / "routers" / "__init__.py", "")
    for resource in ("article", "review"):
        class_name = resource.capitalize()
        write(
            root / "routers" / f"{resource}.py",
            f"""
from fastapi import APIRouter
from sqlmodel import Field, SQLModel

router = APIRouter(prefix="/{resource}s", tags=["{resource}"])


class {class_name}(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    owner_id: int | None = Field(default=None, foreign_key="user.id", index=True)
    title: str = Field(index=True)


@router.get("")
def list_{resource}():
    return []
""".lstrip(),
        )
    write(
        root / "routers" / "auth.py",
        """
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
def me():
    return {}
""".lstrip(),
    )


class FakeDb:
    def __init__(self) -> None:
        self.added = []
        self.flushes = 0

    def add(self, item) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        self.flushes += 1


class FakeProjectFiles:
    def __init__(self) -> None:
        self.deleted = []

    async def delete_paths_for_project(self, project_id, paths) -> None:
        self.deleted.append((project_id, list(paths)))


def patch_finish_repositories(monkeypatch: pytest.MonkeyPatch, helper: SimpleNamespace) -> None:
    class FakeIndexer:
        def __init__(self, db) -> None:
            self.db = db

        async def refresh(self, project, *, reason: str = "index") -> dict:
            index = build_project_index(Path(project.folder_path))
            index["endpoint_doc"] = {"path": "API_ENDPOINTS.md", "changed": False, "bytes_written": 0}
            return index

    class FakeHelperRepository:
        def __init__(self, db) -> None:
            self.db = db

        async def get_or_create(self, project_id):
            return helper

    monkeypatch.setattr("app.services.project_edit.results.ProjectIndexer", FakeIndexer)
    monkeypatch.setattr("app.services.project_edit.results.ProjectHelperRepository", FakeHelperRepository)


async def finish_with_fake_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    changed: list[WriteOutcome],
    edit_plan: dict,
    previous_contracts: list[dict] | None = None,
) -> dict:
    helper = SimpleNamespace(requirement_contracts=list(previous_contracts or []))
    patch_finish_repositories(monkeypatch, helper)
    project_id = uuid4()
    project = SimpleNamespace(
        id=project_id,
        folder_path=str(tmp_path),
        name="test",
        description="",
        is_active=True,
    )
    return await finish_edit_result(
        db=FakeDb(),
        project_files=FakeProjectFiles(),
        project=project,
        project_id=project_id,
        root=tmp_path,
        changed=changed,
        summary=[],
        edit_plan=edit_plan,
        retries=0,
    )


def test_missing_required_field_is_reported(tmp_path: Path) -> None:
    base_project(tmp_path)
    write(
        tmp_path / "routers" / "replies.py",
        """
from sqlmodel import Field, SQLModel


class Reply(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    content: str
    created_at: str
""".lstrip(),
    )
    index = build_project_index(tmp_path)
    contract = normalize_requirement_contract(
        {
            "requirements": [
                {
                    "id": "R2",
                    "description": "Reply.ticket_id must exist so replies belong to tickets.",
                    "critical": True,
                }
            ],
            "required_fields": [
                {"requirement_id": "R2", "table": "Reply", "field": "ticket_id"}
            ],
        }
    )

    result = validate_artifacts(index, contract).as_dict()

    assert result["passed"] is False
    assert "R2 missing field Reply.ticket_id" in result["missing_artifacts"]


def test_filter_and_wired_route_pass(tmp_path: Path) -> None:
    base_project(tmp_path)
    index = build_project_index(tmp_path)
    contract = normalize_requirement_contract(
        {
            "required_routes": [{"requirement_id": "R1", "method": "GET", "path": "/tickets"}],
            "required_filters": [{"requirement_id": "R1", "table": "Ticket", "field": "status"}],
        }
    )

    result = validate_artifacts(index, contract).as_dict()

    assert result["passed"] is True
    assert result["missing_artifacts"] == []


def test_filter_query_param_without_where_comparison_passes(tmp_path: Path) -> None:
    base_project(tmp_path, where_filter=False)
    index = build_project_index(tmp_path)
    contract = normalize_requirement_contract(
        {
            "required_filters": [{"requirement_id": "R1", "table": "Ticket", "field": "status"}],
        }
    )

    result = validate_artifacts(index, contract).as_dict()

    assert result["passed"] is True
    assert result["missing_artifacts"] == []


def test_sqlalchemy_models_in_model_py_satisfy_required_tables(tmp_path: Path) -> None:
    write(
        tmp_path / "model.py",
        """
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, index=True)


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True)
    name = Column(String, index=True)


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    content = Column(String)
""".lstrip(),
    )
    index = build_project_index(tmp_path)
    contract = normalize_requirement_contract(
        {
            "required_tables": [
                {"requirement_id": "R1", "table": "User"},
                {"requirement_id": "R1", "table": "Project"},
                {"requirement_id": "R1", "table": "Note"},
            ],
            "required_fields": [
                {"requirement_id": "R1", "table": "Project", "field": "owner_id"},
                {"requirement_id": "R1", "table": "Note", "field": "project_id"},
            ],
            "required_relationships": [
                {"requirement_id": "R1", "from_table": "Project", "field": "owner_id", "to_table": "User"},
                {"requirement_id": "R1", "from_table": "Note", "field": "project_id", "to_table": "Project"},
            ],
        }
    )

    result = validate_artifacts(index, contract).as_dict()

    assert result["passed"] is True
    assert result["missing_artifacts"] == []
    assert {table["name"] for table in result["detected_artifacts"]["tables"]} == {"User", "Project", "Note"}
    assert {table["db_table_name"] for table in result["detected_artifacts"]["tables"]} == {"users", "projects", "notes"}


def test_plural_sqlmodel_tablename_satisfies_singular_required_table(tmp_path: Path) -> None:
    write(
        tmp_path / "models.py",
        """
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True)
""".lstrip(),
    )

    result = validate_artifacts(
        build_project_index(tmp_path),
        normalize_requirement_contract({"required_tables": [{"requirement_id": "R1", "table": "User"}]}),
    ).as_dict()

    assert result["passed"] is True
    assert result["missing_artifacts"] == []


def test_pydantic_only_model_does_not_satisfy_required_table(tmp_path: Path) -> None:
    write(
        tmp_path / "model.py",
        """
from pydantic import BaseModel


class User(BaseModel):
    id: int
    username: str
""".lstrip(),
    )

    result = validate_artifacts(
        build_project_index(tmp_path),
        normalize_requirement_contract({"required_tables": [{"requirement_id": "R1", "table": "User"}]}),
    ).as_dict()

    assert result["passed"] is False
    assert "R1 missing table User" in result["missing_artifacts"]
    assert result["detected_artifacts"]["tables"] == []
    assert result["detected_artifacts"]["classes"][0]["is_pydantic_model"] is True


def test_static_validation_rejects_route_handlers_in_singular_model_file(tmp_path: Path) -> None:
    write(tmp_path / "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
    write(tmp_path / "database.py", "from sqlalchemy import create_engine\nengine = create_engine('sqlite:///x.db')\n\ndef get_db():\n    yield\n")
    write(tmp_path / "requirements.txt", "fastapi\nsqlalchemy\n")
    write(
        tmp_path / "model.py",
        """
from fastapi import FastAPI

app = FastAPI()


@app.get("/items")
def list_items():
    return []
""".lstrip(),
    )

    result = validate_written_project(
        tmp_path,
        [
            WriteOutcome(path="main.py", bytes_written=1),
            WriteOutcome(path="database.py", bytes_written=1),
            WriteOutcome(path="model.py", bytes_written=1),
            WriteOutcome(path="requirements.txt", bytes_written=1),
        ],
    ).as_dict()

    assert result["passed"] is False
    model_error = next(check["error"] for check in result["checks"] if check["path"] == "model.py")
    assert "non-entrypoint file instantiates FastAPI application" in model_error
    assert "model file contains route handlers" in model_error


def test_route_without_main_wiring_fails(tmp_path: Path) -> None:
    base_project(tmp_path, wire_tickets=False)
    index = build_project_index(tmp_path)
    contract = normalize_requirement_contract(
        {
            "required_routes": [{"requirement_id": "R1", "method": "GET", "path": "/tickets"}],
        }
    )

    result = validate_artifacts(index, contract).as_dict()

    assert result["passed"] is False
    assert "R1 route GET /tickets is not wired in main.py" in result["missing_artifacts"]


def test_route_wiring_understands_package_module_include(tmp_path: Path) -> None:
    base_project(tmp_path)
    write(
        tmp_path / "main.py",
        """
from fastapi import FastAPI
from routers import tickets

app = FastAPI()
app.include_router(tickets.router)
""".lstrip(),
    )
    index = build_project_index(tmp_path)
    contract = normalize_requirement_contract(
        {
            "required_routes": [{"requirement_id": "R1", "method": "GET", "path": "/tickets"}],
        }
    )

    result = validate_artifacts(index, contract).as_dict()

    assert result["passed"] is True
    assert result["missing_artifacts"] == []


def test_requirement_text_does_not_create_hidden_artifacts(tmp_path: Path) -> None:
    base_project(tmp_path)
    contract = normalize_requirement_contract(
        requirements=[
            {
                "id": "R2",
                "description": "Reply.ticket_id must exist so replies belong to tickets.",
                "critical": True,
            }
        ]
    )

    result = validate_artifacts(build_project_index(tmp_path), contract).as_dict()

    assert contract["required_fields"] == []
    assert result["passed"] is False
    assert "R2 has no model-supplied artifact checks" in result["missing_artifacts"]


def test_prompt_contract_is_kept_as_notes_only() -> None:
    contract = normalize_requirement_contract(
        requirements=[{"id": "R1", "description": "Ticket list should filter by status."}],
        prompt_contract={
            "expected": {
                "resources": ["Ticket"],
                "include": ["list"],
                "list_filters": {"tickets": ["status"]},
            }
        },
    )

    assert contract["prompt_contract"]["expected"]["resources"] == ["Ticket"]
    assert contract["required_tables"] == []
    assert contract["required_routes"] == []
    assert contract["required_filters"] == []


def test_model_edit_selection_includes_matching_table_and_resource_files(tmp_path: Path) -> None:
    multi_resource_project(tmp_path)
    index = build_project_index(tmp_path)
    prompt = (
        "Add role-based users, update articles and reviews visibility, "
        "and add a paginated API for reviews."
    )

    selected = select_edit_files(tmp_path, index, prompt)

    assert "auth.py" in selected
    assert "routers/auth.py" in selected
    assert "routers/article.py" in selected
    assert "routers/review.py" in selected


def test_model_edit_selection_includes_indexed_table_file_for_table_changes(tmp_path: Path) -> None:
    multi_resource_project(tmp_path)
    index = build_project_index(tmp_path)
    prompt = (
        "Add profile_slug column to the user table, and update articles "
        "and reviews so the new profile metadata is visible."
    )

    selected = select_edit_files(tmp_path, index, prompt)

    assert "auth.py" in selected
    assert "routers/article.py" in selected
    assert "routers/review.py" in selected


def test_model_edit_selection_can_target_dependency_files(tmp_path: Path) -> None:
    base_project(tmp_path)

    selected = select_edit_files(
        tmp_path,
        build_project_index(tmp_path),
        "Add the missing package dependency to requirements.txt.",
    )

    assert "requirements.txt" in selected


def test_previous_accepted_contract_regression_fails(tmp_path: Path) -> None:
    base_project(tmp_path)
    write(
        tmp_path / "routers" / "tickets.py",
        """
from fastapi import APIRouter
from sqlmodel import Field, SQLModel

router = APIRouter(prefix="/tickets", tags=["tickets"])


class Ticket(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
""".lstrip(),
    )
    index = build_project_index(tmp_path)
    previous = normalize_requirement_contract(
        {
            "accepted": True,
            "required_fields": [{"requirement_id": "R1", "table": "Ticket", "field": "status"}],
        }
    )

    result = validate_artifacts(index, normalize_requirement_contract({}), previous_contracts=[previous]).as_dict()

    assert result["passed"] is False
    assert "R1 missing field Ticket.status" in result["regressions"]


def test_failed_previous_contract_is_not_revalidated_or_active_context(tmp_path: Path) -> None:
    base_project(tmp_path)
    failed = normalize_requirement_contract(
        {
            "id": "failed-contract",
            "accepted": False,
            "prompt": "failed",
            "required_fields": [{"requirement_id": "R1", "table": "Ticket", "field": "missing"}],
            "validation": {"passed": True, "artifact_validation": {"passed": True}},
        }
    )
    accepted = normalize_requirement_contract(
        {
            "id": "accepted-contract",
            "accepted": True,
            "prompt": "accepted",
            "required_fields": [{"requirement_id": "R2", "table": "Ticket", "field": "status"}],
        }
    )

    result = validate_artifacts(
        build_project_index(tmp_path),
        normalize_requirement_contract({}),
        previous_contracts=[failed],
    ).as_dict()
    state = compact_project_state(build_project_index(tmp_path), requirement_contracts=[failed, accepted])
    helper = SimpleNamespace(
        project_context="",
        database_schema={},
        api_routes=[],
        file_index=[],
        function_summaries=[],
        recent_changes=[],
        requirement_contracts=[failed, accepted],
    )
    context = build_project_context(helper=helper) or ""

    assert result["regressions"] == []
    assert [item["id"] for item in state["requirement_contracts"]] == ["accepted-contract"]
    assert "accepted-contract" in context
    assert "failed-contract" not in context


@pytest.mark.asyncio
async def test_static_pass_with_missing_artifact_is_written_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_project(tmp_path)

    result = await finish_with_fake_state(
        tmp_path,
        monkeypatch,
        changed=[WriteOutcome(path="routers/tickets.py", bytes_written=1)],
        edit_plan={
            "requirements": [
                {
                    "id": "R2",
                    "description": "Reply.ticket_id must exist so replies belong to tickets.",
                    "critical": True,
                }
            ],
            "artifact_contract": {
                "required_fields": [
                    {"requirement_id": "R2", "table": "Reply", "field": "ticket_id"}
                ]
            },
        },
    )

    assert result["accepted"] is True
    assert result["pipeline_state"] in {"draft_written", "accepted"}
    assert result["failure_category"] == ""
    assert result["validation"]["missing_required"] == []
    assert all(check["passed"] for check in result["validation"]["checks"])
    assert "R2 missing field Reply.ticket_id" in result["missing_artifacts"]


@pytest.mark.asyncio
async def test_no_source_change_fails_for_mutation_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_project(tmp_path)

    result = await finish_with_fake_state(
        tmp_path,
        monkeypatch,
        changed=[],
        edit_plan={
            "requirements": [
                {
                    "id": "R1",
                    "description": "Ticket.status must exist.",
                    "critical": True,
                }
            ],
        },
    )

    assert result["accepted"] is False
    assert result["pipeline_state"] == "edit_no_change"
    assert result["failure_category"] == "no_change"
    assert result["artifact_validation"]["passed"] is False
    assert "No source files changed for a mutation request." in result["missing_artifacts"]


def test_no_change_result_has_normalized_gate_fields(tmp_path: Path) -> None:
    result = no_change_result(
        project_id=uuid4(),
        root=tmp_path,
        target_paths=["main.py"],
        model_result={"summary": ["no files"]},
        change_state=None,
        reason="No edits were returned.",
        pipeline_trace=[{"stage": "edit.patch", "status": "parse_error"}],
    )

    assert result["accepted"] is False
    assert result["artifact_validation"]["passed"] is False
    assert result["missing_artifacts"] == ["No edits were returned."]
    assert result["regressions"] == []
    assert result["stage_timings_ms"] == {}
    assert result["pipeline_trace"] == [{"stage": "edit.patch", "status": "parse_error"}]
    assert result["validation"]["model_edit"]["trace"] == result["pipeline_trace"]


@pytest.mark.asyncio
async def test_failed_contract_is_not_persisted_as_active() -> None:
    helper = SimpleNamespace(requirement_contracts=[], recent_changes=[])

    class FakeHelperRepository:
        async def get_or_create(self, project_id):
            return helper

    await persist_requirement_contract(
        FakeHelperRepository(),
        uuid4(),
        prompt="add status",
        requirements=[{"id": "R1", "description": "Ticket.status must exist."}],
        gap={},
        plan={},
        coverage={"passed": True},
        validation={
            "passed": True,
            "accepted": False,
            "artifact_validation": {"passed": True, "missing_artifacts": [], "regressions": []},
        },
        changed_files=["routers/tickets.py"],
        stats={},
        provider="test",
        source="edit",
    )

    assert helper.requirement_contracts == []
    assert helper.recent_changes[0]["accepted"] is False


def test_file_upload_behavior_accepts_upload_route(tmp_path: Path) -> None:
    write(
        tmp_path / "main.py",
        '''
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse

app = FastAPI()

@app.post("/files/upload")
async def upload_file(file: UploadFile = File(...)):
    return {"filename": file.filename}

@app.get("/files/{filename}")
def download_file(filename: str):
    return FileResponse(filename)
''',
    )
    write(tmp_path / "database.py", "")
    write(tmp_path / "requirements.txt", "fastapi\n")

    index = build_project_index(tmp_path)
    result = validate_artifacts(
        index,
        {
            "required_behaviors": [
                {"requirement_id": "R1", "behavior": "file_upload"},
            ]
        },
    )

    assert result.passed is True
    assert result.missing_artifacts == []

def test_project_context_includes_rich_index_map(tmp_path: Path) -> None:
    base_project(tmp_path)
    index = build_project_index(tmp_path)
    helper = SimpleNamespace(
        project_context="Ticket API",
        database_schema={**index["database_schema"], "class_summaries": index["class_summaries"]},
        api_routes=index["api_routes"],
        file_index=index["file_index"],
        function_summaries=index["function_summaries"],
        recent_changes=[],
        requirement_contracts=[],
    )

    context = build_project_context(helper=helper, query="ticket status filter") or ""

    assert '"classes":' in context
    assert '"params":' in context
    assert '"query_filters":' in context
    assert "TicketRead" in context
    assert "status" in context
    assert "routers/tickets.py" in context


def test_generic_file_download_accepts_a_download_response_route(tmp_path: Path) -> None:
    write(
        tmp_path / "files.py",
        '''from fastapi import APIRouter\nfrom fastapi.responses import FileResponse\n\nrouter = APIRouter(prefix="/files")\n\n@router.get("/{filename}")\ndef download_file(filename: str):\n    return FileResponse(filename)\n''',
    )

    result = validate_artifacts(
        build_project_index(tmp_path),
        {"required_behaviors": [{"requirement_id": "R1", "behavior": "file_download", "method": "GET"}]},
    ).as_dict()

    assert result["passed"] is True


def test_jwt_authentication_behavior_accepts_token_route_and_auth_symbols(tmp_path: Path) -> None:
    write(
        tmp_path / "auth.py",
        '''from fastapi import APIRouter\n\nrouter = APIRouter(prefix="/auth")\n\ndef create_access_token():\n    return jwt.encode({})\n\ndef get_current_user():\n    return jwt.decode("token")\n\n@router.post("/token")\ndef issue_token():\n    return {"access_token": create_access_token()}\n''',
    )

    result = validate_artifacts(
        build_project_index(tmp_path),
        {"required_behaviors": [{"requirement_id": "R1", "behavior": "JWT authentication"}]},
    ).as_dict()

    assert result["passed"] is True


def test_artifact_validation_checks_csv_download_implementation(tmp_path: Path) -> None:
    write(
        tmp_path / "main.py",
        """import csv
from io import StringIO
from fastapi import FastAPI
from fastapi.responses import Response

app = FastAPI()

@app.get("/tasks/unfinished/download")
def download_tasks():
    output = StringIO()
    csv.writer(output).writerow(["title", "description"])
    return Response(content=output.getvalue(), media_type="text/csv")
""",
    )

    result = validate_artifacts(
        build_project_index(tmp_path),
        {
            "required_behaviors": [
                {
                    "requirement_id": "R1",
                    "behavior": "file_download",
                    "method": "GET",
                    "path": "/tasks/unfinished/download",
                    "format": "csv",
                }
            ]
        },
    )

    assert result.passed is True


def test_artifact_validation_checks_admin_role_enforcement(tmp_path: Path) -> None:
    write(
        tmp_path / "main.py",
        """from fastapi import Depends, FastAPI, HTTPException, status

app = FastAPI()

def get_current_user():
    return {"role": "admin"}

@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return {"deleted": task_id}
""",
    )

    result = validate_artifacts(
        build_project_index(tmp_path),
        {
            "required_behaviors": [
                {
                    "requirement_id": "R1",
                    "behavior": "role_restricted",
                    "method": "DELETE",
                    "path": "/tasks/{task_id}",
                    "role": "admin",
                }
            ]
        },
    )

    assert result.passed is True


def test_artifact_validation_rejects_download_of_wrong_resource(tmp_path: Path) -> None:
    write(
        tmp_path / "main.py",
        '''from fastapi import FastAPI
from fastapi.responses import Response
from sqlmodel import SQLModel, select

app = FastAPI()

class Student(SQLModel):
    id: int

@app.get("/students/me/unfinished-assignments/download")
def download_report():
    statement = select(Student)
    return Response(content=str(statement), media_type="text/csv")
''',
    )

    result = validate_artifacts(
        build_project_index(tmp_path),
        {
            "required_behaviors": [
                {
                    "requirement_id": "R1",
                    "behavior": "file_download",
                    "method": "GET",
                    "path": "/students/me/unfinished-assignments/download",
                    "format": "csv",
                    "table": "assignments",
                }
            ]
        },
    )

    assert result.passed is False
    assert any("file_download" in item for item in result.missing_artifacts)


def test_artifact_validation_rejects_unscoped_current_user_download(tmp_path: Path) -> None:
    write(
        tmp_path / "main.py",
        """from fastapi import Depends, FastAPI

app = FastAPI()

def get_current_user():
    return {"id": 1}

@app.get("/tasks/unfinished/download")
def download_tasks(current_user: dict = Depends(get_current_user)):
    return {"tasks": []}
""",
    )

    result = validate_artifacts(
        build_project_index(tmp_path),
        {
            "required_behaviors": [
                {
                    "requirement_id": "R1",
                    "behavior": "current_user_scoped",
                    "method": "GET",
                    "path": "/tasks/unfinished/download",
                }
            ]
        },
    )

    assert result.passed is False
    assert any("current_user_scoped" in item for item in result.missing_artifacts)


def test_preservation_contract_rejects_unrequested_route_removal(tmp_path: Path) -> None:
    write(
        tmp_path / "main.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.get("/tickets")
def list_tickets():
    return []

@app.post("/tickets")
def create_ticket():
    return {}
""",
    )
    baseline = build_preservation_contract(build_project_index(tmp_path))
    write(
        tmp_path / "main.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.get("/tickets")
def list_tickets():
    return []
""",
    )

    result = validate_artifacts(
        build_project_index(tmp_path),
        normalize_requirement_contract(),
        previous_contracts=[baseline],
    )

    assert result.passed is False
    assert any("POST /tickets" in item for item in result.regressions)


def test_preservation_contract_allows_explicit_route_removal(tmp_path: Path) -> None:
    write(
        tmp_path / "main.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.get("/tickets")
def list_tickets():
    return []

@app.post("/tickets")
def create_ticket():
    return {}
""",
    )
    baseline = build_preservation_contract(build_project_index(tmp_path))
    write(
        tmp_path / "main.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.get("/tickets")
def list_tickets():
    return []
""",
    )

    result = validate_artifacts(
        build_project_index(tmp_path),
        {
            "removed_artifacts": [
                {
                    "requirement_id": "R1",
                    "kind": "route",
                    "method": "POST",
                    "path": "/tickets",
                }
            ]
        },
        previous_contracts=[baseline],
    )

    assert result.passed is True
    assert result.regressions == []


def test_prepare_edit_memory_removes_stale_api_and_route_only_schema_memory() -> None:
    prepared = prepare_edit_memory(
        {
            "schema": {"ddl_sql": "ALTER TABLE notes ADD COLUMN old_tag TEXT"},
            "api_contract": {"contract": {"required_routes": [{"path": "/old"}]}},
            "file_plan": {"plan": {"files": ["old.py"]}},
        },
        current_contract={
            "required_routes": [
                {"method": "GET", "path": "/notes/pinned/download"}
            ]
        },
    )

    assert prepared["schema"] == {}
    assert prepared["api_contract"] == {}
    assert prepared["file_plan"] == {}
    assert prepared["context_policy"]["current_request_first"] is True
