"""remove legacy research tables

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-05-16 13:05:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


LEGACY_TABLES = (
    "human_evaluations",
    "self_correction_iterations",
    "code_evaluations",
    "ablation_results",
    "self_correction_runs",
    "benchmark_results",
    "ablation_experiments",
    "benchmark_runs",
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    for table in LEGACY_TABLES:
        if table in existing:
            op.drop_table(table)


def downgrade() -> None:
    # These tables were unused legacy research artifacts and are intentionally
    # not recreated by downgrade. Restore from an earlier schema backup if needed.
    pass
