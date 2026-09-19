"""Review questions and the human decisions they produce (ERD §2)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EnumText, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from app.domain.enums import (
    Ifrs18Category,
    QuestionAnswer,
    QuestionScope,
    ReviewAction,
)


class ReviewQuestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A fact the rule engine needs before it can decide.

    Makes step 5 of the spec §4 flow a first-class, auditable object rather than
    transient UI state.

    **Scope** (open question Q4, resolved 2026-09-19). A specified main business
    activity is a fact about the *entity*, so one answer settles every affected
    line (``COMPANY``). The risk a derivative manages, which IFRS 18 B72 needs in
    order to assign a category, is a fact about *that instrument* — two
    derivative lines in one statement can manage different risks — so it is
    asked per line (``LINE``).
    """

    __tablename__ = "review_questions"
    __table_args__ = (
        enum_check("scope", QuestionScope),
        enum_check("answer", QuestionAnswer, nullable=True),
        enum_check("resolved_category", Ifrs18Category, nullable=True),
        UniqueConstraint("project_id", "question_key", "line_id"),
        CheckConstraint(
            "(scope = 'LINE') = (line_id IS NOT NULL)",
            name="line_scope_requires_line",
        ),
        CheckConstraint(
            "answer IS NULL OR (answered_by IS NOT NULL AND answered_at IS NOT NULL)",
            name="answer_records_answerer",
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[EnumText]
    line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("financial_statement_lines.id", ondelete="CASCADE")
    )
    #: Stable key, e.g. ``SMBA_INVESTING_IN_ASSETS``, ``DERIVATIVE_RISK_MANAGED``.
    question_key: Mapped[str] = mapped_column(String(100))
    question_text_ko: Mapped[str] = mapped_column(Text)
    question_text_en: Mapped[str] = mapped_column(Text)
    raised_by_rule_id: Mapped[str | None] = mapped_column(String(100))

    answer: Mapped[EnumText | None]
    #: For a choice question such as B72's "which risk does this manage?", the
    #: chosen category. ``answer`` alone is insufficient there.
    resolved_category: Mapped[EnumText | None]
    #: Set when the user answers "this would require grossing up, or involve
    #: undue cost or effort" — routing the line to operating under B72.
    undue_cost_or_effort: Mapped[bool] = mapped_column(default=False)

    answered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    answered_at: Mapped[dt.datetime | None]
    note: Mapped[str | None] = mapped_column(Text)
    resulting_activity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("business_activities.id", ondelete="SET NULL")
    )
    blocks_finalization: Mapped[bool] = mapped_column(default=True)

    @property
    def is_resolved(self) -> bool:
        """``NOT_SURE`` is persisted but does not resolve the rule (spec §5)."""
        return self.answer is not None and self.answer != QuestionAnswer.NOT_SURE


class UserReview(UUIDPrimaryKeyMixin, Base):
    """Append-only record of each human decision (spec §8).

    ``ACCEPTED`` is recorded as well as ``OVERRIDDEN``: "a human looked at this
    and agreed" is itself audit-relevant information.
    """

    __tablename__ = "user_reviews"
    __table_args__ = (
        enum_check("action", ReviewAction),
        enum_check("previous_category", Ifrs18Category, nullable=True),
        enum_check("new_category", Ifrs18Category, nullable=True),
    )

    classification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ifrs18_classifications.id", ondelete="CASCADE"), index=True
    )
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[EnumText]
    previous_category: Mapped[EnumText | None]
    new_category: Mapped[EnumText | None]
    reason: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[dt.datetime]
