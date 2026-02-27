"""add_unique_constraint_endpoint

Revision ID: d4e5f6a7b8c9
Revises: c6aa29eb5c4e
Create Date: 2026-02-27

Adds a UNIQUE constraint on (method, path_pattern) to the endpoints table.
This prevents duplicate rows caused by the race condition between
proxy.py's get_or_create_endpoint() and process_learning_buffer().

We first DELETE duplicate rows (keeping the lowest id per pair) before
adding the constraint, so this migration is safe to run on an existing
database that may already have duplicates.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c6aa29eb5c4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Step 1: Remove duplicates, keeping the lowest id per (method, path_pattern) ──
    # Identify duplicate endpoint ids to delete.
    dupes = conn.execute(sa.text("""
        SELECT id
        FROM endpoints
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM endpoints
            GROUP BY method, path_pattern
        )
    """)).fetchall()

    if dupes:
        dup_ids = [row[0] for row in dupes]
        id_list = ", ".join(str(i) for i in dup_ids)

        # Delete child rows first (FK constraints)
        for table in ("health_metrics", "contract_drift", "chaos_config", "endpoint_behavior"):
            conn.execute(sa.text(f"DELETE FROM {table} WHERE endpoint_id IN ({id_list})"))

        conn.execute(sa.text(f"DELETE FROM endpoints WHERE id IN ({id_list})"))

    # ── Step 2: Add the unique constraint ─────────────────────────────────────
    op.create_unique_constraint(
        "uq_endpoint_method_path",
        "endpoints",
        ["method", "path_pattern"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_endpoint_method_path", "endpoints", type_="unique")
