"""add durable project agent runs

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-05 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa

import app.db.mixins
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_agent_runs",
        sa.Column("id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("project_id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("user_id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("contract_version", sa.String(length=40), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("stage", sa.String(length=60), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=255), nullable=False),
        sa.Column("model_id", app.db.mixins.UUIDChar(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=False),
        sa.Column("warnings_json", sa.JSON(), nullable=False),
        sa.Column("current_checkpoint", sa.Integer(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_project_agent_run_idempotency"),
    )
    for column in ("project_id", "user_id", "operation", "status", "lease_owner", "lease_expires_at", "next_attempt_at"):
        op.create_index(op.f(f"ix_project_agent_runs_{column}"), "project_agent_runs", [column])

    op.create_table(
        "project_agent_checkpoints",
        sa.Column("id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("run_id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("stage", sa.String(length=60), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=False),
        sa.Column("changed_files", sa.JSON(), nullable=False),
        sa.Column("base_revision", sa.String(length=64), nullable=False),
        sa.Column("applied_revision", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["project_agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_project_agent_checkpoint_sequence"),
    )
    op.create_index(op.f("ix_project_agent_checkpoints_run_id"), "project_agent_checkpoints", ["run_id"])
    op.create_index(op.f("ix_project_agent_checkpoints_status"), "project_agent_checkpoints", ["status"])

    op.create_table(
        "project_agent_events",
        sa.Column("id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("run_id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("project_id", app.db.mixins.UUIDChar(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["project_agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_project_agent_event_sequence"),
    )
    for column in ("run_id", "project_id", "event_type"):
        op.create_index(op.f(f"ix_project_agent_events_{column}"), "project_agent_events", [column])


def downgrade() -> None:
    op.drop_table("project_agent_events")
    op.drop_table("project_agent_checkpoints")
    op.drop_table("project_agent_runs")
