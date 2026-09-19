"""Classification rules (ERD §2, spec §25).

Rules are **data, not code**. Each carries its authoritative citation and
effective date, so the rule set is exportable, diffable, reviewable by an
accountant who does not read Python, and versionable independently of releases.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EnumText, Ratio, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from app.domain.enums import (
    ActivityType,
    Ifrs18Category,
    Ifrs18Subcategory,
    RuleSourceType,
    RuleVerificationStatus,
)


class ClassificationRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "classification_rules"
    __table_args__ = (
        enum_check("outcome_category", Ifrs18Category, nullable=True),
        enum_check("outcome_subcategory", Ifrs18Subcategory, nullable=True),
        enum_check("requires_activity_fact", ActivityType, nullable=True),
        enum_check("source_type", RuleSourceType),
        enum_check("verification_status", RuleVerificationStatus),
        UniqueConstraint("rule_id", "version"),
        CheckConstraint(
            "confidence_ceiling IS NULL OR (confidence_ceiling > 0 AND confidence_ceiling <= 1)",
            name="confidence_ceiling_in_range",
        ),
        # A rule that assigns no category must inherit one (FX, derivatives).
        CheckConstraint(
            "outcome_category IS NOT NULL OR inherits_category",
            name="rule_must_assign_or_inherit",
        ),
    )

    #: Stable identifier such as ``IFRS18-INVESTING-001``.
    rule_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(50))
    #: Lower runs first; first match wins.
    priority: Mapped[int] = mapped_column(index=True)
    description: Mapped[str] = mapped_column(Text)

    #: Declarative predicate evaluated by a small total interpreter.
    #: No ``eval``, no code loaded from the database.
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB)

    outcome_category: Mapped[EnumText | None]
    outcome_subcategory: Mapped[EnumText | None]
    #: True for IFRS 18 B65 (foreign exchange) and B72 (derivatives), where the
    #: category is taken from another item or from the risk being managed.
    inherits_category: Mapped[bool] = mapped_column(default=False)

    #: When set, the rule returns ``NEEDS_FACT`` until this activity is
    #: confirmed, which is what makes the spec §5 user question deterministic.
    requires_activity_fact: Mapped[EnumText | None]

    source_type: Mapped[EnumText]
    #: e.g. ``IFRS 18 paragraph B72``.
    source_reference: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    verification_status: Mapped[EnumText] = mapped_column(
        default=RuleVerificationStatus.UNVERIFIED.value
    )
    verification_note: Mapped[str | None] = mapped_column(Text)

    effective_date: Mapped[dt.date]
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("classification_rules.id", ondelete="SET NULL")
    )
    #: Caps the confidence a rule may claim when its support is weaker than the
    #: standard itself (spec §25 ranks authority).
    confidence_ceiling: Mapped[Ratio | None]
    is_active: Mapped[bool] = mapped_column(default=True)
