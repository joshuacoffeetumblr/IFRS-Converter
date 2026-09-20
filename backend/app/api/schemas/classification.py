"""Classification, review question and business activity payloads."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import Field

from app.api.schemas.common import ApiModel, Money, Ratio
from app.domain.enums import (
    ActivitySource,
    ActivityType,
    ClassificationMethod,
    ConfidenceBand,
    EvidenceProducer,
    EvidenceType,
    Ifrs18Category,
    Ifrs18Subcategory,
    QuestionAnswer,
    QuestionScope,
    ReviewAction,
    RuleVerificationStatus,
)

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class ClassifyRequest(ApiModel):
    """How to run the engine over this project's lines."""

    #: Consult the AI advisor for lines no rule positively identified. Whether
    #: one is configured at all is reported back as `ai_assistant_available`;
    #: an advisor can only ever propose (spec §1).
    use_ai_assistant: bool = False
    #: Keep human decisions. Turning it off replaces overrides with the
    #: engine's proposals, which is why it is opt-out rather than opt-in.
    preserve_user_overrides: bool = True


class EvidenceResponse(ApiModel):
    evidence_type: EvidenceType
    reference: str
    excerpt: str | None = None
    produced_by: EvidenceProducer


class ClassificationResponse(ApiModel):
    id: uuid.UUID
    line_id: uuid.UUID
    #: Snapshots taken when the decision was made, so the record stays readable
    #: even if the account dictionary is edited later (ERD §2).
    original_account: str
    normalized_account_code: str | None
    amount: Money
    current_category: str | None

    proposed_ifrs18_category: Ifrs18Category | None
    proposed_ifrs18_subcategory: Ifrs18Subcategory | None
    final_ifrs18_category: Ifrs18Category | None
    final_ifrs18_subcategory: Ifrs18Subcategory | None
    classification_method: ClassificationMethod

    rule_id: str | None = None
    #: The rule's citation, resolved from the rule set — what the "why?" panel
    #: shows (spec §23).
    rule_source_reference: str | None = None
    rule_verification_status: RuleVerificationStatus | None = None
    ai_model: str | None = None
    ai_confidence: Ratio | None = None
    ai_reasoning: str | None = None
    confidence_band: ConfidenceBand | None = None

    requires_human_review: bool
    #: Set while a rule is waiting on a fact only the entity can supply.
    blocked_on_question_id: uuid.UUID | None = None
    user_override: bool
    override_reason: str | None = None
    reviewed_at: dt.datetime | None = None
    reviewer_user_id: uuid.UUID | None = None

    #: Null where the source printed no operating subtotal: with no "before",
    #: the movement is unknown rather than zero.
    impact_on_operating_profit: Money | None = None
    evidence: list[EvidenceResponse] = Field(default_factory=list)


class ReviewResponse(ApiModel):
    """One human decision, from the append-only review log."""

    action: ReviewAction
    reviewer_user_id: uuid.UUID
    previous_category: Ifrs18Category | None = None
    new_category: Ifrs18Category | None = None
    reason: str | None = None
    reviewed_at: dt.datetime


class RuleResponse(ApiModel):
    """The rule that decided a line, as a reviewer needs to read it (spec §23)."""

    rule_id: str
    priority: int
    description: str
    source_type: str
    source_reference: str
    verification_status: RuleVerificationStatus
    source_url: str | None = None
    source_note: str | None = None


class ClassificationDetailResponse(ClassificationResponse):
    """The row-click drill-down of spec §7."""

    raw_label: str
    raw_value: str
    #: Back to the originating cell (spec §18).
    source_locator: dict[str, object] = Field(default_factory=dict)
    rule: RuleResponse | None = None
    question: QuestionResponse | None = None
    reviews: list[ReviewResponse] = Field(default_factory=list)


class ClassificationSummary(ApiModel):
    total: int
    by_method: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    requires_review: int = 0
    #: Needing review and not yet looked at — what blocks finalization.
    unreviewed: int = 0
    open_questions: int = 0


class ClassificationsResponse(ApiModel):
    items: list[ClassificationResponse]
    summary: ClassificationSummary


class ClassifyResponse(ApiModel):
    summary: ClassificationSummary
    questions: list[QuestionResponse] = Field(default_factory=list)
    rule_set_version: str
    #: Human decisions this run left untouched.
    preserved_overrides: int = 0
    #: Decisions dropped because their line is no longer classifiable.
    discarded: int = 0
    #: Whether an AI advisor is actually configured. It is false in this build,
    #: so `use_ai_assistant` changes nothing — said plainly rather than left for
    #: a caller to infer from an empty result.
    ai_assistant_available: bool = False


class ReviewRequest(ApiModel):
    """A human decision about one classification (spec §1 layer 3)."""

    action: ReviewAction
    final_ifrs18_category: Ifrs18Category | None = None
    final_ifrs18_subcategory: Ifrs18Subcategory | None = None
    #: Required on an override, because an unexplained override is not an
    #: audit trail (spec §8).
    override_reason: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


class QuestionResponse(ApiModel):
    id: uuid.UUID
    scope: QuestionScope
    line_id: uuid.UUID | None
    question_key: str
    question_text_ko: str
    question_text_en: str
    help_ko: str | None = None
    help_en: str | None = None
    raised_by_rule_id: str | None = None
    #: The answers this particular question accepts. A per-line question has no
    #: "no" — the answer is a category, or the standard's undue-cost relief.
    options: list[QuestionAnswer] = Field(default_factory=list)
    allows_undue_cost_or_effort: bool = False

    answer: QuestionAnswer | None = None
    resolved_category: Ifrs18Category | None = None
    undue_cost_or_effort: bool = False
    note: str | None = None
    answered_at: dt.datetime | None = None
    #: NOT_SURE is stored but does not resolve the rule (spec §5).
    is_resolved: bool = False
    blocks_finalization: bool = True

    #: What turns on the answer, shown before it is given.
    affected_line_count: int = 0
    affected_amount: Money | None = None


class QuestionsResponse(ApiModel):
    items: list[QuestionResponse]
    open_count: int = 0


class AnswerRequest(ApiModel):
    answer: QuestionAnswer
    #: For a per-line question: which category the underlying item belongs to.
    resolved_category: Ifrs18Category | None = None
    resolved_subcategory: Ifrs18Subcategory | None = None
    #: IFRS 18's own relief (B65, B72): tracing the underlying item would
    #: require grossing up or is impracticable, so the line goes to operating.
    #: A complete answer, unlike "not sure".
    undue_cost_or_effort: bool = False
    note: str | None = Field(default=None, max_length=2000)


class AnswerResponse(ApiModel):
    question: QuestionResponse
    #: Answering re-runs classification, so the caller sees the effect at once.
    summary: ClassificationSummary


# ---------------------------------------------------------------------------
# Business activities
# ---------------------------------------------------------------------------


class ActivityResponse(ApiModel):
    id: uuid.UUID
    activity_type: ActivityType
    #: Three-valued on purpose: null is unknown, which blocks a rule. Never the
    #: same as false (spec §10).
    is_main_business_activity: bool | None
    description: str | None = None
    source: ActivitySource
    confirmed_by_user: bool
    confirmed_at: dt.datetime | None = None
    #: Whether IFRS 18 treats this as a *specified* main business activity,
    #: which is what can change a classification (F10, B30).
    is_specified: bool = False


class ActivitiesResponse(ApiModel):
    items: list[ActivityResponse]


class SetActivityRequest(ApiModel):
    """Confirm, deny, or withdraw a confirmation.

    ``null`` is accepted and means unknown: a user who no longer stands behind
    a confirmation must be able to withdraw it without asserting the opposite.
    """

    is_main_business_activity: bool | None
    description: str | None = Field(default=None, max_length=2000)
