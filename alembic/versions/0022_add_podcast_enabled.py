"""Add podcast_enabled flag to podcast_preferences (default false — users opt in)

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-21 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "podcast_preferences",
        sa.Column("podcast_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("podcast_preferences", "podcast_enabled")
