"""add_drift_narration

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-05

Adds the drift_narration column to the contract_drift table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6g7h8i9j0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("contract_drift", sa.Column("drift_narration", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("contract_drift", "drift_narration")
