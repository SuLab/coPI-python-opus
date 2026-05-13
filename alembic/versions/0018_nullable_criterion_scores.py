"""Make categorical criterion scores nullable in pi_proposal_evaluations

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-06 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = [
    "score_significance",
    "score_innovation",
    "score_approach",
    "score_investigators",
    "score_environment",
]


def upgrade() -> None:
    for col in _COLS:
        op.alter_column("pi_proposal_evaluations", col, nullable=True)


def downgrade() -> None:
    for col in _COLS:
        op.alter_column("pi_proposal_evaluations", col, nullable=False)
