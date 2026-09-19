"""IFRS 18 classifications and their supporting evidence (ERD §2, spec §8)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, EnumText, Ratio, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from app.domain.enums import (
    ClassificationMethod,
    ConfidenceBand,
    EvidenceProducer,
    EvidenceType,
    Ifrs18Category,
    Ifrs18Subcategory,
)


class Ifrs18Classification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One live classification per classifiable line.

    History lives in ``user_reviews`` and ``audit_logs`` rather than in
    versioned rows here, so every read query stays free of a "latest" filter.

    ``original_account``, ``normalized_account_code`` and ``amount`` are
    deliberate denormalized snapshots: an audit record must stay readable even
    if the account dictionary is later edited.
    """

    __tablename__ = "ifrs18_classifications"
    __table_args__ = (
        enum_check("proposed_ifrs18_category", Ifrs18Category, nullable=True),
        enum_check("proposed_ifrs18_subcategory", Ifrs18Subcategory, nullable=True),
        enum_check("final_ifrs18_category", Ifrs18Category, nullable=True),
        enum_check("final_ifrs18_subcategory", Ifrs18Subcategory, nullable=True),
        enum_check("classification_method", ClassificationMethod),
        enum_check("confidence_band", ConfidenceBand, nullable=True),
        Index("ix_classification_review_queue", "project_id", "requires_human_review"),
        CheckConstraint(
            "ai_confidence IS NULL OR (ai_confidence >= 0 AND ai_confidence <= 1)",
            name="ai_confidence_in_range",
        ),
        # Spec §1: an AI proposal is never final on its own.
        CheckConstraint(
            "classification_method <> 'AI' OR requires_human_review",
            name="ai_always_requires_review",
        ),
        # A human decision must identify the human and when.
        CheckConstraint(
            "NOT user_override OR (reviewer_user_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="override_records_reviewer",
        ),
        CheckConstraint(
            "NOT user_override OR classification_method = 'USER'",
            name="override_sets_user_method",
        ),
        # A rule-derived decision must say which rule.
        CheckConstraint(
            "classification_method <> 'RULE' OR rule_id IS NOT NULL",
            name="rule_method_records_rule_id",
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("financial_statement_lines.id", ondelete="CASCADE"),
        unique=True,
    )

    # --- snapshots (spec §8) ------------------------------------------------
    original_account: Mapped[str] = mapped_column(String(500))
    normalized_account_code: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[Decimal]
    current_category: Mapped[str | None] = mapped_column(String(100))

    # --- proposal -----------------------------------------------------------
    proposed_ifrs18_category: Mapped[EnumText | None]
    proposed_ifrs18_subcategory: Mapped[EnumText | None]

    # --- outcome ------------------------------------------------------------
    final_ifrs18_category: Mapped[EnumText | None]
    final_ifrs18_subcategory: Mapped[EnumText | None]
    classification_method: Mapped[EnumText]

    # --- provenance of the decision ----------------------------------------
    rule_id: Mapped[str | None] = mapped_column(String(100))
    rule_set_version: Mapped[str | None] = mapped_column(String(50))
    ai_model: Mapped[str | None] = mapped_column(String(100))
    ai_confidence: Mapped[Ratio | None]
    ai_reasoning: Mapped[str | None] = mapped_column(Text)
    #: The model's exact output, retained for audit even on success.
    ai_raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    confidence_band: Mapped[EnumText | None]

    # --- review -------------------------------------------------------------
    requires_human_review: Mapped[bool] = mapped_column(default=False)
    #: Set when a ``NEEDS_FACT`` rule is waiting on an unanswered question.
    blocked_on_question_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_questions.id", ondelete="SET NULL")
    )
    user_override: Mapped[bool] = mapped_column(default=False)
    override_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[dt.datetime | None]
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    #: Computed server-side from one definition (ERD §5), so the account-level
    #: impact table and the waterfall cannot disagree.
    impact_on_operating_profit: Mapped[Decimal | None]

    evidence: Mapped[list[ClassificationEvidence]] = relationship(
        back_populates="classification", cascade="all, delete-orphan"
    )


class ClassificationEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "classification_evidence"
    __table_args__ = (
        enum_check("evidence_type", EvidenceType),
        enum_check("produced_by", EvidenceProducer),
    )

    classification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ifrs18_classifications.id", ondelete="CASCADE")
    )
    evidence_type: Mapped[EnumText]
    #: e.g. ``Note 12``, ``IFRS 18 paragraph B72``.
    reference: Mapped[str] = mapped_column(String(300))
    #: Bounded length; redacted from application logs (spec §32).
    excerpt: Mapped[str | None] = mapped_column(String(2000))
    #: Points back to the source page or cell.
    locator: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    produced_by: Mapped[EnumText]

    classification: Mapped[Ifrs18Classification] = relationship(back_populates="evidence")
