from __future__ import annotations

from app.services.model_pipeline.artifacts import normalize_artifact_contract
from app.services.model_pipeline.intent import extract_prompt_intent
from app.services.model_pipeline.naming import class_name, identifier, plural, singular
from app.services.model_pipeline.quests import build_generation_quests
from app.services.model_pipeline.specs import build_canonical_edit_spec


def test_shared_naming_aliases_customer_typo_everywhere() -> None:
    assert identifier("costumer_id") == "customer_id"
    assert singular("costumers") == "customer"
    assert plural("costumer") == "customers"
    assert class_name("costumers") == "Customer"


def test_numeric_field_policy_is_context_specific() -> None:
    assert identifier("1") == "1"
    assert identifier("1", numeric_prefix="field") == "field_1"


def test_naming_preserves_acronyms_and_common_plural_endings() -> None:
    assert identifier("JWT authentication") == "jwt_authentication"
    assert singular("courses") == "course"
    assert singular("classes") == "class"
    assert singular("statuses") == "status"


def test_intent_keeps_numeric_request_metadata_out_of_schema() -> None:
    intent = extract_prompt_intent(
        "Resolved state: recent_user_messages=2\n"
        "Plan a FastAPI app; resources are orders. filters for newests: 1."
    )

    schema_tables = {
        table["name"]: [column["name"] for column in table.get("columns") or []]
        for table in intent["schema_additions"]["tables"]
    }
    assert "field_1" not in schema_tables.get("newests", [])


def test_canonical_spec_and_quests_share_customer_alias() -> None:
    canonical = build_canonical_edit_spec(
        prompt="Add top customers by orders.",
        index={},
        schema_delta={"schema": {"tables": [{"name": "costumers", "columns": [{"name": "id"}]}]}},
        api_delta={
            "artifact_contract": {
                "required_tables": [{"table": "costumers"}],
                "required_fields": [{"table": "orders", "field": "costumer_id"}],
                "required_routes": [{"method": "GET", "path": "/costumers/top-by-orders"}],
            }
        },
    )

    contract = canonical["artifact_contract"]
    assert {"table": "customers"} in contract["required_tables"]
    assert {"table": "orders", "field": "customer_id"} in contract["required_fields"]

    quests = build_generation_quests(canonical, contract)
    quest_ids = {quest.id for quest in quests}
    assert "table:customer" in quest_ids
    assert "field:order.customer_id" in quest_ids


def test_artifact_contract_normalizes_routes_tables_fields_and_relationships() -> None:
    contract = normalize_artifact_contract(
        {
            "required_routes": [{"method": "get", "path": "costumers/{costumer_id}/orders/"}],
            "required_tables": [{"table": "Costumer"}],
            "required_fields": [{"table": "Order", "field": "costumer_id"}],
            "required_filters": [{"table": "Order", "field": "1"}],
            "required_relationships": [
                {"table": "Order", "columns": ["costumer_id"], "related_table": "Costumer"}
            ],
            "required_behaviors": [{"kind": "Top N", "table": "Costumer"}],
        }
    )

    assert contract["required_routes"] == [{"method": "GET", "path": "/customers/{customer_id}/orders"}]
    assert contract["required_tables"] == [{"table": "customers"}]
    assert contract["required_fields"] == [{"table": "orders", "field": "customer_id"}]
    assert contract["required_filters"] == [{"table": "orders", "field": "field_1"}]
    assert contract["required_relationships"] == [
        {"table": "Order", "columns": ["costumer_id"], "related_table": "Costumer", "from_table": "orders", "to_table": "customers", "field": "customer_id"}
    ]
    assert contract["required_behaviors"] == [{"kind": "Top N", "table": "customers", "behavior": "top_n"}]
