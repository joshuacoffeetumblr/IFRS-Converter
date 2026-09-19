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
        enum_check("fact_true_category", Ifrs18Category, nullable=True),
        enum_check("fact_true_subcategory", Ifrs18Subcategory, nullable=True),
        enum_check("undue_cost_category", Ifrs18Category, nullable=True),
        enum_check("requires_activity_fact", ActivityType, nullable=True),
        enum_check("source_type", RuleSourceType),
        enum_check("verification_status", RuleVerificationStatus),
        UniqueConstraint("rule_id", "version"),
        CheckConstraint(
            "confidence_ceiling IS NULL OR (confidence_ceiling > 0 AND confidence_ceiling <= 1)",
            name="confidence_ceiling_in_range",
        ),
        # A rule that assigns no category must inherit one (FX, derivatives)
        # or depend on an entity fact that decides between two outcomes.
        CheckConstraint(
            "outcome_category IS NOT NULL OR inherits_category OR fact_true_category IS NOT NULL",
            name="rule_must_assign_or_inherit",
        ),
        # A fact-dependent rule needs both branches, or one answer has no answer.
        CheckConstraint(
            "requires_activity_fact IS NULL "
            "OR (fact_true_category IS NOT NULL "
            "AND outcome_category IS NOT NULL)",
            name="fact_rule_defines_both_outcomes",
        ),
        # A line-scoped rule takes its category from the answer, so it must say
        # where "undue cost or effort" lands.
        CheckConstraint(
            "requires_line_fact IS NULL OR undue_cost_category IS NOT NULL",
            name="line_fact_needs_undue_cost",
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

    #: A fact-dependent rule has two outcomes, one per answer. Storing both
    #: keeps the rule a single reviewable row rather than a pair that must be
    #: kept in step by hand. The "false" branch lives in ``outcome_category``.
    #:
    #: Named tersely because PostgreSQL truncates identifiers at 63 characters
    #: and the generated constraint name is "ck_classification_rules_<column>_valid".
    fact_true_category: Mapped[EnumText | None]
    fact_true_subcategory: Mapped[EnumText | None]

    #: Where the standard's "undue cost or effort" relief lands (B65, B72).
    undue_cost_category: Mapped[EnumText | None]

    #: A fact about *this line* rather than the entity: which risk a derivative
    #: manages (B72), or which item produced an FX difference (B65).
    requires_line_fact: Mapped[str | None] = mapped_column(String(100))

    #: Route every match to a human even though the rule matched. Set where a
    #: citation is not yet confirmed against the issued standard.
    requires_human_review: Mapped[bool] = mapped_column(default=False)

    #: The catch-all expressing "operating is the residual category". Recorded
    #: so the engine can grade confidence by whether the account was recognised
    #: instead of granting a rule's certainty to anything unclaimed.
    is_residual: Mapped[bool] = mapped_column(default=False)

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
