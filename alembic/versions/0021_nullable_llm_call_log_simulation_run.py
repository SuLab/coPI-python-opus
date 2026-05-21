"""Make simulation_run_id nullable in llm_call_logs to support podcast pipeline logging

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-21 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("llm_call_logs", "simulation_run_id", nullable=True)


def downgrade() -> None:
    op.alter_column("llm_call_logs", "simulation_run_id", nullable=False)
