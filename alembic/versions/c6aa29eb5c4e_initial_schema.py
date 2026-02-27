"""initial_schema

Revision ID: c6aa29eb5c4e
Revises:
Create Date: 2026-02-27

Full initial schema for the Intelligent Adaptive Mock Platform.
Creates all tables from scratch; safe to run on an empty database.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6aa29eb5c4e"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── endpoints ──────────────────────────────────────────────────────────────
    op.create_table(
        "endpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("path_pattern", sa.String(), nullable=False),
        sa.Column("target_url", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.func.now(),
        ),
    )

    # ── endpoint_behavior ──────────────────────────────────────────────────────
    op.create_table(
        "endpoint_behavior",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("endpoints.id"), unique=True),
        sa.Column("latency_mean", sa.Float(), nullable=True, server_default="400.0"),
        sa.Column("latency_std", sa.Float(), nullable=True, server_default="100.0"),
        sa.Column("error_rate", sa.Float(), nullable=True, server_default="0.0"),
        sa.Column("status_code_distribution", sa.JSON(), nullable=True),
        sa.Column("response_schema", sa.JSON(), nullable=True),
        sa.Column("request_schema", sa.JSON(), nullable=True),
    )

    # ── chaos_config ───────────────────────────────────────────────────────────
    op.create_table(
        "chaos_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("endpoints.id"), unique=True),
        sa.Column("chaos_level", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default="false"),
    )

    # ── contract_drift ─────────────────────────────────────────────────────────
    op.create_table(
        "contract_drift",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("endpoints.id")),
        sa.Column(
            "detected_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column("drift_score", sa.Float(), nullable=True, server_default="0.0"),
        sa.Column("drift_summary", sa.String(), nullable=True),
        sa.Column("drift_details", sa.JSON(), nullable=True),
        sa.Column("is_resolved", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )

    # ── health_metrics ─────────────────────────────────────────────────────────
    op.create_table(
        "health_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("endpoints.id")),
        sa.Column(
            "recorded_at",
            sa.DateTime(),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column("latency_ms", sa.Float(), nullable=True, server_default="0.0"),
        sa.Column("status_code", sa.Integer(), nullable=True, server_default="200"),
        sa.Column("response_size_bytes", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("is_error", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("latency_anomaly", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("error_spike", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("size_anomaly", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("health_score", sa.Float(), nullable=True, server_default="100.0"),
        sa.Column("anomaly_reasons", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("health_metrics")
    op.drop_table("contract_drift")
    op.drop_table("chaos_config")
    op.drop_table("endpoint_behavior")
    op.drop_table("endpoints")
