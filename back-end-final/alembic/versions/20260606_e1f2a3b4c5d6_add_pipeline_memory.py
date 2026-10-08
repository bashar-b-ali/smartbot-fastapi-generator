"""add pipeline memory tables

Revision ID: e1f2a3b4c5d6
Revises: d2e3f4a5b6c7
Create Date: 2026-06-06 03:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa

import app.db.mixins
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_blueprints",
        sa.Column("id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("project_id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_blueprints_project_id"), "project_blueprints", ["project_id"])

    op.create_table(
        "project_schema_versions",
        sa.Column("id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("project_id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("blueprint_id", app.db.mixins.UUIDChar(length=36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("ddl_sql", sa.Text(), nullable=False),
        sa.Column("schema_json", sa.JSON(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["blueprint_id"], ["project_blueprints.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_schema_versions_project_id"), "project_schema_versions", ["project_id"])
    op.create_index(op.f("ix_project_schema_versions_blueprint_id"), "project_schema_versions", ["blueprint_id"])
    op.create_index(op.f("ix_project_schema_versions_accepted"), "project_schema_versions", ["accepted"])

    op.create_table(
        "project_api_contract_versions",
        sa.Column("id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("project_id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("schema_version_id", app.db.mixins.UUIDChar(length=36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("contract_json", sa.JSON(), nullable=False),
        sa.Column("artifact_contract", sa.JSON(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["schema_version_id"], ["project_schema_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_api_contract_versions_project_id"), "project_api_contract_versions", ["project_id"])
    op.create_index(op.f("ix_project_api_contract_versions_schema_version_id"), "project_api_contract_versions", ["schema_version_id"])
    op.create_index(op.f("ix_project_api_contract_versions_accepted"), "project_api_contract_versions", ["accepted"])

    op.create_table(
        "project_file_plans",
        sa.Column("id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("project_id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("api_contract_id", app.db.mixins.UUIDChar(length=36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("file_plan", sa.JSON(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["api_contract_id"], ["project_api_contract_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_file_plans_project_id"), "project_file_plans", ["project_id"])
    op.create_index(op.f("ix_project_file_plans_api_contract_id"), "project_file_plans", ["api_contract_id"])
    op.create_index(op.f("ix_project_file_plans_accepted"), "project_file_plans", ["accepted"])

    op.create_table(
        "project_file_summaries",
        sa.Column("id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("project_id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_file_summaries_project_id"), "project_file_summaries", ["project_id"])
    op.create_index(op.f("ix_project_file_summaries_file_path"), "project_file_summaries", ["file_path"])

    op.create_table(
        "project_pipeline_runs",
        sa.Column("id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("project_id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("stage_outputs", sa.JSON(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_pipeline_runs_project_id"), "project_pipeline_runs", ["project_id"])
    op.create_index(op.f("ix_project_pipeline_runs_status"), "project_pipeline_runs", ["status"])

    op.create_table(
        "project_pipeline_trace_events",
        sa.Column("id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("project_id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("run_id", app.db.mixins.UUIDChar(length=36), nullable=True),
        sa.Column("stage", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("event", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["project_pipeline_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_pipeline_trace_events_project_id"), "project_pipeline_trace_events", ["project_id"])
    op.create_index(op.f("ix_project_pipeline_trace_events_run_id"), "project_pipeline_trace_events", ["run_id"])
    op.create_index(op.f("ix_project_pipeline_trace_events_stage"), "project_pipeline_trace_events", ["stage"])
    op.create_index(op.f("ix_project_pipeline_trace_events_status"), "project_pipeline_trace_events", ["status"])


def downgrade() -> None:
    op.drop_table("project_pipeline_trace_events")
    op.drop_table("project_pipeline_runs")
    op.drop_table("project_file_summaries")
    op.drop_table("project_file_plans")
    op.drop_table("project_api_contract_versions")
    op.drop_table("project_schema_versions")
    op.drop_table("project_blueprints")
