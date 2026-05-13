"""Make comments_overall nullable in pi_proposal_evaluations

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-13 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("pi_proposal_evaluations", "comments_overall", nullable=True)


def downgrade() -> None:
    op.alter_column("pi_proposal_evaluations", "comments_overall", nullable=False)
