"""Add pi_proposal_evaluations table for NIH-style PI proposal scoring

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-04 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pi_proposal_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("proposal_type", sa.String(20), nullable=False),
        sa.Column(
            "thread_decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("thread_decisions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "matchmaker_proposal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("matchmaker_proposals.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("score_significance", sa.SmallInteger(), nullable=False),
        sa.Column("score_innovation", sa.SmallInteger(), nullable=False),
        sa.Column("score_approach", sa.SmallInteger(), nullable=False),
        sa.Column("score_investigators", sa.SmallInteger(), nullable=False),
        sa.Column("score_environment", sa.SmallInteger(), nullable=False),
        sa.Column("score_overall_impact", sa.SmallInteger(), nullable=False),
        sa.Column("comments_significance", sa.Text(), nullable=True),
        sa.Column("comments_innovation", sa.Text(), nullable=True),
        sa.Column("comments_approach", sa.Text(), nullable=True),
        sa.Column("comments_investigators", sa.Text(), nullable=True),
        sa.Column("comments_environment", sa.Text(), nullable=True),
        sa.Column("comments_overall", sa.Text(), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Indexes
    op.create_index("ix_ppe_user_id", "pi_proposal_evaluations", ["user_id"])
    op.create_index("ix_ppe_user_type", "pi_proposal_evaluations", ["user_id", "proposal_type"])
    op.create_index("ix_ppe_thread_decision_id", "pi_proposal_evaluations", ["thread_decision_id"])
    op.create_index(
        "ix_ppe_matchmaker_proposal_id", "pi_proposal_evaluations", ["matchmaker_proposal_id"]
    )

    # Unique constraints
    op.create_unique_constraint(
        "uq_ppe_user_thread", "pi_proposal_evaluations", ["user_id", "thread_decision_id"]
    )
    op.create_unique_constraint(
        "uq_ppe_user_matchmaker",
        "pi_proposal_evaluations",
        ["user_id", "matchmaker_proposal_id"],
    )

    # Check constraints
    op.create_check_constraint(
        "ck_ppe_proposal_type",
        "pi_proposal_evaluations",
        "proposal_type IN ('agent', 'matchmaker')",
    )
    op.create_check_constraint(
        "ck_ppe_proposal_present",
        "pi_proposal_evaluations",
        "thread_decision_id IS NOT NULL OR matchmaker_proposal_id IS NOT NULL",
    )
    for col in ["significance", "innovation", "approach", "investigators", "environment", "overall_impact"]:
        op.create_check_constraint(
            f"ck_ppe_score_{col}",
            "pi_proposal_evaluations",
            f"score_{col} BETWEEN 1 AND 9",
        )


def downgrade() -> None:
    op.drop_table("pi_proposal_evaluations")
