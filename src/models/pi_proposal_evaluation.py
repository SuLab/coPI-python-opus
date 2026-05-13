"""PiProposalEvaluation model.

NIH-style 1-9 evaluations submitted by PIs through the /proposals tab.
Separate from ProposalReview (the 1-4 agent-blocking system).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class PiProposalEvaluation(Base):
    __tablename__ = "pi_proposal_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "agent" | "matchmaker" — stored for admin analysis, never shown to the PI
    proposal_type: Mapped[str] = mapped_column(String(20), nullable=False)
    thread_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("thread_decisions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    matchmaker_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matchmaker_proposals.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # NIH criterion scores (1–9) — nullable; currently hidden from the PI form
    score_significance: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    score_innovation: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    score_approach: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    score_investigators: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    score_environment: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Overall impact is holistic — not an average of the five criteria
    score_overall_impact: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    comments_significance: Mapped[str | None] = mapped_column(Text, nullable=True)
    comments_innovation: Mapped[str | None] = mapped_column(Text, nullable=True)
    comments_approach: Mapped[str | None] = mapped_column(Text, nullable=True)
    comments_investigators: Mapped[str | None] = mapped_column(Text, nullable=True)
    comments_environment: Mapped[str | None] = mapped_column(Text, nullable=True)
    comments_overall: Mapped[str | None] = mapped_column(Text, nullable=True)

    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    thread_decision: Mapped["ThreadDecision | None"] = relationship("ThreadDecision")
    matchmaker_proposal: Mapped["MatchmakerProposal | None"] = relationship("MatchmakerProposal")

    __table_args__ = (
        CheckConstraint("proposal_type IN ('agent', 'matchmaker')", name="ck_ppe_proposal_type"),
        CheckConstraint(
            "thread_decision_id IS NOT NULL OR matchmaker_proposal_id IS NOT NULL",
            name="ck_ppe_proposal_present",
        ),
        CheckConstraint("score_significance BETWEEN 1 AND 9", name="ck_ppe_score_significance"),
        CheckConstraint("score_innovation BETWEEN 1 AND 9", name="ck_ppe_score_innovation"),
        CheckConstraint("score_approach BETWEEN 1 AND 9", name="ck_ppe_score_approach"),
        CheckConstraint("score_investigators BETWEEN 1 AND 9", name="ck_ppe_score_investigators"),
        CheckConstraint("score_environment BETWEEN 1 AND 9", name="ck_ppe_score_environment"),
        CheckConstraint("score_overall_impact BETWEEN 1 AND 9", name="ck_ppe_score_overall_impact"),
        # One evaluation per user per proposal (upsert replaces rather than duplicates)
        UniqueConstraint("user_id", "thread_decision_id", name="uq_ppe_user_thread"),
        UniqueConstraint("user_id", "matchmaker_proposal_id", name="uq_ppe_user_matchmaker"),
        {},
    )

    def __repr__(self) -> str:
        return f"<PiProposalEvaluation user={self.user_id} impact={self.score_overall_impact}>"
