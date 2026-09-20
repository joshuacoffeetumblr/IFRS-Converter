"""The IFRS 18 classification engine (spec §1, §5).

Three layers, in order, and the order is the whole design:

1. **Deterministic rules** decide what they can, citing the standard.
2. **An AI advisor** is consulted only for what rules could not resolve, and
   can never settle a classification.
3. **A human** has final authority, and every override is recorded.

The engine is built around a property of IFRS 18 rather than around guessing:
**operating is the residual category**. Rules positively identify investing,
financing, income taxes and discontinued operations; whatever survives is
operating *by the standard's definition*, not by inference. That is why a small
deterministic rule set can cover most of a statement and the AI layer stays
small enough to audit.

A rule can return a third outcome besides match and no-match: ``NEEDS_FACT``,
when the rule applies but depends on something only the entity knows. That is
what makes the user questions in spec §5 deterministic — a rule raises them,
not a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.domain.enums import (
    ActivityType,
    ClassificationMethod,
    ConfidenceBand,
    Ifrs18Category,
    Ifrs18Subcategory,
    RuleVerificationStatus,
)
from app.domain.rules import (
    ClassifiableItem,
    EntityFacts,
    RuleDefinitionError,
    evaluate_condition,
    validate_condition,
)

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    category: Ifrs18Category
    subcategory: Ifrs18Subcategory | None = None


@dataclass(frozen=True, slots=True)
class RuleSource:
    type: str
    reference: str
    verification_status: RuleVerificationStatus = RuleVerificationStatus.UNVERIFIED
    url: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ClassificationRuleSpec:
    """One rule, as loaded from the rule set."""

    rule_id: str
    priority: int
    description: str
    condition: dict[str, object]
    source: RuleSource
    outcome: Outcome | None = None
    #: When set, the rule's outcome depends on a confirmed entity fact.
    requires_activity_fact: ActivityType | None = None
    outcome_when_fact_true: Outcome | None = None
    outcome_when_fact_false: Outcome | None = None
    #: When set, the rule depends on a fact about *this line* — which risk a
    #: derivative manages (B72), or which item produced an FX difference (B65).
    requires_line_fact: str | None = None
    inherits_category: bool = False
    #: Where the standard's "undue cost or effort" relief lands.
    undue_cost_outcome: Outcome | None = None
    #: Route every match to a human even though the rule matched. Used where a
    #: citation is not yet confirmed against the issued standard.
    requires_human_review: bool = False
    #: The catch-all that expresses "operating is the residual category". It
    #: matches everything, so the engine must not treat reaching it as a
    #: positive rule match: confidence depends on whether the account was
    #: recognised, and the AI advisor is consulted first when it was not.
    is_residual: bool = False
    confidence_ceiling: Decimal | None = None

    def validate(self) -> None:
        validate_condition(self.condition, path=f"{self.rule_id}.condition")

        if self.requires_activity_fact is not None:
            if self.outcome_when_fact_true is None or self.outcome_when_fact_false is None:
                raise RuleDefinitionError(
                    f"{self.rule_id}: a fact-dependent rule needs both "
                    "outcome_when_fact_true and outcome_when_fact_false"
                )
        elif self.requires_line_fact is not None:
            if not self.inherits_category:
                raise RuleDefinitionError(
                    f"{self.rule_id}: a line-fact rule must inherit its category"
                )
        elif self.outcome is None:
            raise RuleDefinitionError(f"{self.rule_id}: needs an outcome")


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


class RuleStatus(StrEnum):
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    NEEDS_FACT = "NEEDS_FACT"


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    status: RuleStatus
    rule_id: str | None = None
    is_residual: bool = False
    category: Ifrs18Category | None = None
    subcategory: Ifrs18Subcategory | None = None
    #: The entity fact a ``NEEDS_FACT`` outcome is waiting on.
    required_activity: ActivityType | None = None
    #: The per-line question key a ``NEEDS_FACT`` outcome is waiting on.
    required_line_fact: str | None = None
    requires_human_review: bool = False


@dataclass(frozen=True, slots=True)
class Evidence:
    type: str
    reference: str
    produced_by: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ClassificationDecision:
    line_id: str
    category: Ifrs18Category
    subcategory: Ifrs18Subcategory | None
    method: ClassificationMethod
    rule_id: str | None
    confidence: Decimal
    confidence_band: ConfidenceBand
    requires_human_review: bool
    evidence: tuple[Evidence, ...] = field(default=())
    reasoning: str | None = None
    #: Set when the decision is blocked on an unanswered question.
    blocked_on_activity: ActivityType | None = None
    blocked_on_line_fact: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.method is not ClassificationMethod.UNRESOLVED


# ---------------------------------------------------------------------------
# Confidence (spec §11)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfidenceThresholds:
    high_min: Decimal = Decimal("0.95")
    medium_min: Decimal = Decimal("0.80")

    def band(self, confidence: Decimal) -> ConfidenceBand:
        if confidence >= self.high_min:
            return ConfidenceBand.HIGH
        if confidence >= self.medium_min:
            return ConfidenceBand.MEDIUM
        return ConfidenceBand.LOW


#: A rule citing the standard is certain, because a human wrote and cited it.
#: The reviewable artefact is the rule, exposed for inspection — not the
#: individual decision.
RULE_CONFIDENCE = Decimal("1.0000")
#: The residual outcome is certain when the account was recognised, because
#: operating is the standard's definition of "everything else".
RESIDUAL_CONFIDENCE = Decimal("0.9000")
#: An unrecognised account reaching the residual is a much weaker position.
RESIDUAL_UNKNOWN_ACCOUNT_CONFIDENCE = Decimal("0.5000")


# ---------------------------------------------------------------------------
# The AI boundary (spec §1, §12)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClassificationSuggestion:
    category: Ifrs18Category | None
    subcategory: Ifrs18Subcategory | None
    confidence: Decimal
    reasoning: str
    evidence: tuple[Evidence, ...] = field(default=())


class ClassificationAdvisor(Protocol):
    """The boundary the AI layer sits behind.

    A protocol, so the domain never imports a model client and the whole suite
    runs offline and deterministically (architecture §3).
    """

    def suggest(self, item: ClassifiableItem, facts: EntityFacts) -> ClassificationSuggestion: ...


class NullClassificationAdvisor:
    """The default: no AI. Unresolved items go straight to a human."""

    def suggest(self, item: ClassifiableItem, facts: EntityFacts) -> ClassificationSuggestion:
        return ClassificationSuggestion(
            category=None,
            subcategory=None,
            confidence=Decimal(0),
            reasoning="no advisor configured",
        )


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


class RuleSetError(ValueError):
    """The rule set is internally inconsistent."""


class ClassificationEngine:
    """Applies an ordered rule set to items, first match wins."""

    def __init__(
        self,
        rules: tuple[ClassificationRuleSpec, ...],
        *,
        thresholds: ConfidenceThresholds | None = None,
        advisor: ClassificationAdvisor | None = None,
    ) -> None:
        for rule in rules:
            rule.validate()

        duplicates = {
            rule.rule_id for rule in rules if [r.rule_id for r in rules].count(rule.rule_id) > 1
        }
        if duplicates:
            raise RuleSetError(f"duplicate rule ids: {sorted(duplicates)}")

        priorities = [rule.priority for rule in rules]
        if len(priorities) != len(set(priorities)):
            # Equal priorities make the outcome depend on list order, which is
            # not something a reviewer can see in the data.
            clashing = sorted({p for p in priorities if priorities.count(p) > 1})
            raise RuleSetError(f"rules share a priority, so order is ambiguous: {clashing}")

        self._rules = tuple(sorted(rules, key=lambda rule: rule.priority))
        self._thresholds = thresholds or ConfidenceThresholds()
        self._advisor = advisor or NullClassificationAdvisor()

    @property
    def rules(self) -> tuple[ClassificationRuleSpec, ...]:
        return self._rules

    @property
    def has_advisor(self) -> bool:
        """Whether an AI advisor is actually wired up.

        Worth asking: with the null advisor in place, requesting AI assistance
        changes nothing, and an API that accepted the request silently would
        leave a caller believing a model had looked at their statement.
        """
        return not isinstance(self._advisor, NullClassificationAdvisor)

    # -- rule evaluation ---------------------------------------------------

    def evaluate(self, item: ClassifiableItem, facts: EntityFacts) -> RuleOutcome:
        """Run the rule set over one item. First match wins."""
        for rule in self._rules:
            if not evaluate_condition(rule.condition, item, facts):
                continue

            if rule.requires_activity_fact is not None:
                known = facts.is_main(rule.requires_activity_fact)
                if known is None:
                    return RuleOutcome(
                        status=RuleStatus.NEEDS_FACT,
                        rule_id=rule.rule_id,
                        required_activity=rule.requires_activity_fact,
                    )
                outcome = rule.outcome_when_fact_true if known else rule.outcome_when_fact_false
                assert outcome is not None  # guaranteed by validate()
                return RuleOutcome(
                    status=RuleStatus.MATCH,
                    rule_id=rule.rule_id,
                    category=outcome.category,
                    subcategory=outcome.subcategory,
                    requires_human_review=rule.requires_human_review,
                )

            if rule.requires_line_fact is not None:
                fact = facts.line_fact(item.line_id, rule.requires_line_fact)
                if fact is None:
                    return RuleOutcome(
                        status=RuleStatus.NEEDS_FACT,
                        rule_id=rule.rule_id,
                        required_line_fact=rule.requires_line_fact,
                    )
                # The standard's relief, not a shrug: the user has said that
                # tracing the underlying item would require grossing up or is
                # impracticable, and B65/B72 send exactly that case to
                # operating.
                outcome = rule.undue_cost_outcome if fact.undue_cost_or_effort else None
                if outcome is None:
                    assert fact.category is not None  # guaranteed by is_answered
                    outcome = Outcome(category=fact.category, subcategory=fact.subcategory)
                return RuleOutcome(
                    status=RuleStatus.MATCH,
                    rule_id=rule.rule_id,
                    category=outcome.category,
                    subcategory=outcome.subcategory,
                    requires_human_review=rule.requires_human_review,
                )

            assert rule.outcome is not None  # guaranteed by validate()
            return RuleOutcome(
                status=RuleStatus.MATCH,
                rule_id=rule.rule_id,
                is_residual=rule.is_residual,
                category=rule.outcome.category,
                subcategory=rule.outcome.subcategory,
                requires_human_review=rule.requires_human_review,
            )

        return RuleOutcome(status=RuleStatus.NO_MATCH)

    def rule(self, rule_id: str) -> ClassificationRuleSpec | None:
        return next((rule for rule in self._rules if rule.rule_id == rule_id), None)

    # -- classification ----------------------------------------------------

    def classify(
        self,
        item: ClassifiableItem,
        facts: EntityFacts,
        *,
        use_advisor: bool = True,
    ) -> ClassificationDecision:
        outcome = self.evaluate(item, facts)

        if outcome.status is RuleStatus.NEEDS_FACT:
            return self._unresolved(item, outcome)

        if outcome.status is RuleStatus.MATCH and not outcome.is_residual:
            return self._from_rule(item, outcome)

        # Either no rule matched, or only the residual did. Both mean no rule
        # positively identified a non-operating category, so the AI advisor is
        # consulted before falling back on the residual — but only where there
        # is genuine doubt. An account the dictionary recognised, with no rule
        # claiming it, *is* operating by the standard's definition, and asking a
        # model about it would add cost and risk without adding information.
        if use_advisor and self._is_doubtful(item):
            suggestion = self._advisor.suggest(item, facts)
            if suggestion.category is not None:
                return self._from_advisor(item, suggestion)

        return self._residual(item, rule_id=outcome.rule_id)

    def classify_all(
        self,
        items: tuple[ClassifiableItem, ...],
        facts: EntityFacts,
        *,
        use_advisor: bool = True,
    ) -> tuple[ClassificationDecision, ...]:
        return tuple(self.classify(item, facts, use_advisor=use_advisor) for item in items)

    # -- decision construction --------------------------------------------

    def _evidence_for(self, rule_id: str | None) -> tuple[Evidence, ...]:
        rule = self.rule(rule_id) if rule_id else None
        if rule is None:
            return ()
        return (
            Evidence(
                type="RULE_SOURCE",
                reference=rule.source.reference,
                produced_by="RULE",
                note=rule.source.note,
            ),
        )

    def _from_rule(self, item: ClassifiableItem, outcome: RuleOutcome) -> ClassificationDecision:
        rule = self.rule(outcome.rule_id) if outcome.rule_id else None
        confidence = RULE_CONFIDENCE
        if rule and rule.confidence_ceiling is not None:
            confidence = min(confidence, rule.confidence_ceiling)

        assert outcome.category is not None
        return ClassificationDecision(
            line_id=item.line_id,
            category=outcome.category,
            subcategory=outcome.subcategory,
            method=ClassificationMethod.RULE,
            rule_id=outcome.rule_id,
            confidence=confidence,
            confidence_band=self._thresholds.band(confidence),
            requires_human_review=outcome.requires_human_review,
            evidence=self._evidence_for(outcome.rule_id),
            reasoning=rule.description if rule else None,
        )

    def _unresolved(self, item: ClassifiableItem, outcome: RuleOutcome) -> ClassificationDecision:
        return ClassificationDecision(
            line_id=item.line_id,
            category=Ifrs18Category.UNCLASSIFIED,
            subcategory=None,
            method=ClassificationMethod.UNRESOLVED,
            rule_id=outcome.rule_id,
            confidence=Decimal(0),
            confidence_band=ConfidenceBand.LOW,
            requires_human_review=True,
            evidence=self._evidence_for(outcome.rule_id),
            blocked_on_activity=outcome.required_activity,
            blocked_on_line_fact=outcome.required_line_fact,
        )

    def _from_advisor(
        self, item: ClassifiableItem, suggestion: ClassificationSuggestion
    ) -> ClassificationDecision:
        assert suggestion.category is not None
        return ClassificationDecision(
            line_id=item.line_id,
            category=suggestion.category,
            subcategory=suggestion.subcategory,
            method=ClassificationMethod.AI,
            rule_id=None,
            confidence=suggestion.confidence,
            confidence_band=self._thresholds.band(suggestion.confidence),
            # Spec §1: forced true regardless of what the model returned. The
            # model's own view of its uncertainty is kept as a signal, never as
            # a control.
            requires_human_review=True,
            evidence=suggestion.evidence,
            reasoning=suggestion.reasoning,
        )

    @staticmethod
    def _is_doubtful(item: ClassifiableItem) -> bool:
        """Whether an item reaching the residual deserves a second opinion."""
        return item.normalized_account_code is None or item.is_aggregate

    def _residual(
        self, item: ClassifiableItem, *, rule_id: str | None = None
    ) -> ClassificationDecision:
        """Operating, by the standard's definition of the category.

        This is not a fallback for "we could not tell". IFRS 18 defines
        operating as everything not classified elsewhere, so reaching here is a
        positive answer — provided we actually recognised the account. If we did
        not, the confidence says so and a human looks.
        """
        recognised = item.normalized_account_code is not None
        confidence = RESIDUAL_CONFIDENCE if recognised else RESIDUAL_UNKNOWN_ACCOUNT_CONFIDENCE
        return ClassificationDecision(
            line_id=item.line_id,
            category=Ifrs18Category.OPERATING,
            subcategory=None,
            method=ClassificationMethod.RESIDUAL_DEFAULT,
            rule_id=rule_id,
            confidence=confidence,
            confidence_band=self._thresholds.band(confidence),
            requires_human_review=not recognised,
            evidence=self._evidence_for(rule_id),
            reasoning=(
                "Not classified into investing, financing, income taxes or "
                "discontinued operations, so operating by the definition of the "
                "operating category."
            ),
        )
