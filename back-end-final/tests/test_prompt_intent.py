from __future__ import annotations

from dataclasses import dataclass

from app.services.change_request import build_change_request_state
from app.services.model_pipeline.intent import extract_prompt_intent, user_request_text


@dataclass
class HistoryRow:
    message_type: str
    content: str


def _artifact_tables(intent: dict) -> set[str]:
    contract = intent["contract"]
    values: set[str] = set()
    for key in ("required_tables", "required_fields", "required_filters", "required_behaviors"):
        for item in contract.get(key) or []:
            if item.get("table"):
                values.add(item["table"])
    return values


def test_change_request_wrapper_does_not_look_like_schema_syntax() -> None:
    state = build_change_request_state(
        [HistoryRow("user", "Plan a FastAPI app; resources are orders. filters for orders: status.")],
        "Add price to orders.",
    )

    assert "Resolved state:" not in state.resolved_prompt
    assert "oldest to newest:" not in state.resolved_prompt
    assert "recent_user_messages=" not in state.resolved_prompt
    assert user_request_text(state.resolved_prompt) == (
        "Plan a FastAPI app; resources are orders. filters for orders: status.\n"
        "Add price to orders."
    )


def test_prompt_intent_ignores_old_wrapper_metadata_and_normalizes_costumer() -> None:
    wrapped_prompt = """Conversation-derived backend change request.
Resolved state: recent_user_messages=2

User messages, oldest to newest:
1. Plan a FastAPI app for online store; resources are orders. Resource endpoints can be public. Attach websocket notifications. Keep the output directive-based, with create, list, detail, update actions and filters for orders: status.
2. I want an api to get the top 10 costumers that have orders and add price to orders then make an api to paginate the orders 6 each pagination for each costumer
"""
    intent = extract_prompt_intent(
        wrapped_prompt,
        existing_schema={
            "schema": {
                "tables": [
                    {
                        "name": "orders",
                        "columns": [{"name": "id"}, {"name": "customer_id"}, {"name": "status"}],
                    }
                ]
            }
        },
    )

    tables = _artifact_tables(intent)
    assert "orders" in tables
    assert "customers" in tables
    assert "states" not in tables
    assert "newests" not in tables

    schema_tables = {
        table["name"]: [column["name"] for column in table.get("columns") or []]
        for table in intent["schema_additions"]["tables"]
    }
    assert "field_1" not in schema_tables.get("newests", [])
    assert "recent_user_messages" not in schema_tables.get("states", [])
    assert "costumer_id" not in schema_tables.get("orders", [])
    assert "customer_id" in schema_tables["orders"]
    assert "price" in schema_tables["orders"]
    order_columns = {
        column["name"]: column
        for table in intent["schema_additions"]["tables"]
        if table["name"] == "orders"
        for column in table.get("columns") or []
    }
    assert order_columns["customer_id"]["type"] == "INTEGER"
    assert order_columns["customer_id"]["references"] == "customers(id)"

    custom_paths = {route["path"] for route in intent["custom_routes"]}
    assert "/customers/top-by-orders" in custom_paths
    assert "/customers/{customer_id}/orders" in custom_paths
    relationships = intent["contract"]["required_relationships"]
    assert {"from_table": "orders", "field": "customer_id", "to_table": "customers"} in relationships

def test_prompt_intent_accepts_filter_by_resource_colon_wording() -> None:
    intent = extract_prompt_intent(
        "Build a FastAPI backend for a book lending. It should manage books, and authors. "
        "Resource endpoints can be public. Attach file upload/download helpers. "
        "Include create, list, detail, update, delete endpoints and let list endpoints filter by authors: name."
    )

    contract = intent["contract"]
    assert {"table": "authors", "field": "name"} in contract["required_filters"]
    assert {"table": "authors", "field": "name"} in contract["required_fields"]
    assert {"table": "books"} in contract["required_tables"]
    assert {"table": "authors"} in contract["required_tables"]
    assert {"behavior": "file_upload"} in contract["required_behaviors"]

def test_prompt_intent_extracts_book_sales_and_discount_offer_edit() -> None:
    intent = extract_prompt_intent(
        "add an API to return ordered the most sold books, and add discount table for books "
        "where I see what offers we have on special things and add them",
        existing_schema={"schema": {"tables": [{"name": "books", "columns": [{"name": "id"}]}]}},
    )

    contract = intent["contract"]
    assert {"table": "book_sales"} in contract["required_tables"]
    assert {"table": "discounts"} in contract["required_tables"]
    assert any(item.get("table") == "book_sales" and item.get("field") == "book_id" for item in contract["required_fields"])
    assert not any(item.get("table") == "returns" for item in contract["required_fields"])
    assert not any(item.get("table") == "books" and item.get("field") == "discount_table" for item in contract["required_fields"])
    assert {"table": "discounts", "field": "book_id", "references": "books(id)"} in contract["required_filters"]
    assert {"from_table": "book_sales", "field": "book_id", "to_table": "books"} in contract["required_relationships"]
    assert {"from_table": "discounts", "field": "book_id", "to_table": "books"} in contract["required_relationships"]
    custom_kinds = {route["kind"] for route in intent["custom_routes"]}
    assert {"top_related", "parent_children_paginated"} <= custom_kinds


def test_prompt_intent_extracts_notes_fields_and_filter_by_wording() -> None:
    intent = extract_prompt_intent(
        "Build a FastAPI backend for a notes app. It should manage notes with title, body, "
        "and pinned fields. Include create, list, detail, update, and delete endpoints. "
        "Let list notes filter by pinned."
    )

    contract = intent["contract"]
    assert {"table": "notes"} in contract["required_tables"]
    assert {"table": "notes", "field": "title"} in contract["required_fields"]
    assert {"table": "notes", "field": "body"} in contract["required_fields"]
    assert {"table": "notes", "field": "pinned"} in contract["required_fields"]
    assert {"table": "notes", "field": "pinned"} in contract["required_filters"]
    assert {"method": "POST", "path": "/notes"} in contract["required_routes"]
    assert {"method": "GET", "path": "/notes/{note_id}"} in contract["required_routes"]
    assert not any(item.get("table") == "bodies" for item in contract["required_tables"])


def test_prompt_intent_extracts_single_tag_field_from_add_field_wording() -> None:
    intent = extract_prompt_intent(
        "Add a tag field to notes and let list notes filter by tag.",
        existing_schema={"schema": {"tables": [{"name": "notes", "columns": [{"name": "id"}]}]}},
    )

    contract = intent["contract"]
    assert {"table": "notes", "field": "tag"} in contract["required_fields"]
    assert {"table": "notes", "field": "tag"} in contract["required_filters"]
    assert not any(item.get("field") == "a_tag_field" for item in contract["required_fields"])


def test_prompt_intent_extracts_explicit_text_download_route() -> None:
    intent = extract_prompt_intent(
        "Add an endpoint GET /notes/pinned/download that downloads a text file "
        "containing all pinned notes with title and body."
    )

    contract = intent["contract"]
    assert {"method": "GET", "path": "/notes/pinned/download"} in contract["required_routes"]
    assert any(
        item.get("behavior") == "file_download"
        and item.get("format") == "text"
        and item.get("path") == "/notes/pinned/download"
        for item in contract["required_behaviors"]
    )
    assert not any(item.get("behavior") == "file_upload" for item in contract["required_behaviors"])


def test_prompt_intent_extracts_csv_download_and_current_user_scope() -> None:
    intent = extract_prompt_intent(
        "Add an endpoint GET /students/me/unfinished-assignments/download that downloads "
        "a CSV report of the current student's unfinished assignments."
    )

    behaviors = intent["contract"]["required_behaviors"]
    assert any(
        item.get("behavior") == "file_download"
        and item.get("format") == "csv"
        and item.get("path") == "/students/me/unfinished-assignments/download"
        for item in behaviors
    )
    assert any(
        item.get("behavior") == "current_user_scoped"
        and item.get("path") == "/students/me/unfinished-assignments/download"
        for item in behaviors
    )


def test_prompt_intent_does_not_treat_api_as_a_resource() -> None:
    intent = extract_prompt_intent("Build a task API with tasks and filter tasks by priority.")

    assert all(item.get("table") != "apis" for item in intent["contract"].get("required_tables", []))
    assert all(table.get("name") != "apis" for table in intent["schema_additions"]["tables"])


def test_prompt_intent_extracts_admin_only_delete_rule() -> None:
    intent = extract_prompt_intent(
        "Add roles to users and make delete task admin-only.",
        existing_schema={
            "schema": {
                "tables": [
                    {"name": "users", "columns": [{"name": "id"}]},
                    {"name": "tasks", "columns": [{"name": "id"}]},
                ]
            }
        },
    )

    contract = intent["contract"]
    assert {"table": "users", "field": "role"} in contract["required_fields"]
    assert any(
        item.get("behavior") == "role_restricted"
        and item.get("role") == "admin"
        and item.get("method") == "DELETE"
        and item.get("path") == "/tasks/{task_id}"
        for item in contract["required_behaviors"]
    )


def test_prompt_intent_extracts_added_submission_resource_and_crud() -> None:
    intent = extract_prompt_intent(
        "Add submissions with assignment_id, student_id, content, grade, and submitted_at. "
        "Include create, list, detail, update, and delete endpoints, and let list submissions "
        "filter by assignment_id and student_id.",
        existing_schema={
            "tables": [
                {"name": "assignments"},
                {"name": "students"},
            ]
        },
    )

    contract = intent["contract"]
    assert {"table": "submissions"} in contract["required_tables"]
    assert {"table": "submissions", "field": "assignment_id"} in contract["required_fields"]
    assert {"table": "submissions", "field": "student_id"} in contract["required_filters"]
    assert {
        "from_table": "submissions",
        "field": "assignment_id",
        "to_table": "assignments",
    } in contract["required_relationships"]
    assert {
        "from_table": "submissions",
        "field": "student_id",
        "to_table": "students",
    } in contract["required_relationships"]
    assert {"method": "POST", "path": "/submissions"} in contract["required_routes"]
    assert {"method": "DELETE", "path": "/submissions/{submission_id}"} in contract["required_routes"]


def test_prompt_intent_extracts_school_role_and_ownership_rules() -> None:
    intent = extract_prompt_intent(
        "Teachers can create assignments for their courses, students can list their own assignments, "
        "admins can delete courses, and list assignments can filter by course_id and status."
    )

    behaviors = intent["contract"]["required_behaviors"]
    assert {("assignments", "course_id"), ("assignments", "status")} <= {
        (item.get("table"), item.get("field"))
        for item in intent["contract"]["required_filters"]
    }
    assert any(
        item.get("behavior") == "role_restricted"
        and item.get("role") == "teacher"
        and item.get("method") == "POST"
        and item.get("path") == "/assignments"
        for item in behaviors
    )
    assert sum(item.get("table") == "assignments" for item in behaviors) == 4
    assert any(
        item.get("behavior") == "current_user_scoped"
        and item.get("path") == "/assignments"
        for item in behaviors
    )
    assert any(
        item.get("behavior") == "role_restricted"
        and item.get("role") == "admin"
        and item.get("path") == "/courses/{course_id}"
        for item in behaviors
    )
