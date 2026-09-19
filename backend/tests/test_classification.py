"""The IFRS 18 classification engine, against the test vectors in docs/04 §12."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.data.rule_catalog import get_engine
from app.domain.classification import (
    ClassificationEngine,
    ClassificationRuleSpec,
    ClassificationSuggestion,
    Outcome,
    RuleSetError,
    RuleSource,
    RuleStatus,
)
from app.domain.enums import (
    ActivityType,
    ClassificationMethod,
    Ifrs18Category,
    RuleVerificationStatus,
)
from app.domain.rules import ClassifiableItem, EntityFacts

NON_FINANCIAL = EntityFacts(
    {
        ActivityType.INVESTING_IN_ASSETS: False,
        ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS: False,
    }
)
UNKNOWN = EntityFacts.unknown()


def item(
    code: str | None, label: str, amount: str = "100", *, aggregate: bool = False
) -> ClassifiableItem:
    return ClassifiableItem(
        line_id=label,
        raw_label=label,
        amount=Decimal(amount),
        normalized_account_code=code,
        account_ancestors=(code,) if code else (),
        is_aggregate=aggregate,
    )


@pytest.fixture
def engine() -> ClassificationEngine:
    return get_engine()


# ---------------------------------------------------------------------------
# The core property: operating is the residual category
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "label"),
    [
        ("REVENUE", "매출액"),
        ("COST_OF_SALES", "매출원가"),
        ("SELLING_AND_ADMIN_EXPENSES", "판매비와관리비"),
        ("EMPLOYEE_BENEFITS", "급여"),
        ("IMPAIRMENT_LOSS", "손상차손"),
    ],
)
def test_unclaimed_items_are_operating_by_definition(
    engine: ClassificationEngine, code: str, label: str
) -> None:
    """Not a fallback: IFRS 18 defines operating as everything not elsewhere."""
    decision = engine.classify(item(code, label), NON_FINANCIAL)

    assert decision.category is Ifrs18Category.OPERATING
    assert decision.method is ClassificationMethod.RESIDUAL_DEFAULT
    assert not decision.requires_human_review


def test_the_residual_still_cites_the_standard(engine: ClassificationEngine) -> None:
    decision = engine.classify(item("REVENUE", "매출액"), NON_FINANCIAL)

    assert decision.rule_id == "IFRS18-OPERATING-999"
    assert [evidence.reference for evidence in decision.evidence] == ["IFRS 18 paragraphs 52-68"]


def test_an_unrecognised_account_reaching_the_residual_is_reviewed(
    engine: ClassificationEngine,
) -> None:
    """Regression: the catch-all rule once gave these full confidence.

    An account the dictionary could not even recognise must not inherit a
    rule's certainty just because nothing claimed it.
    """
    decision = engine.classify(item(None, "정체불명의계정과목"), NON_FINANCIAL)

    assert decision.category is Ifrs18Category.OPERATING
    assert decision.confidence == Decimal("0.5000")
    assert decision.requires_human_review


# ---------------------------------------------------------------------------
# T16 — equity method ignores main business activity (docs/07 F3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "facts",
    [
        UNKNOWN,
        NON_FINANCIAL,
        EntityFacts({ActivityType.INVESTING_IN_ASSETS: True}),
    ],
)
def test_equity_method_is_always_investing(
    engine: ClassificationEngine, facts: EntityFacts
) -> None:
    """Test vector T16.

    A regression test for an error found during source verification: the
    pre-verification rule set would have moved 지분법손익 to operating for an
    entity whose main business activity is investing in assets. IFRS 18 does
    not permit that — the equity-method rule is unconditional.
    """
    decision = engine.classify(item("SHARE_OF_PROFIT_OF_ASSOCIATES", "지분법이익"), facts)

    assert decision.category is Ifrs18Category.INVESTING
    assert decision.rule_id == "IFRS18-INVESTING-001"
    assert decision.blocked_on_activity is None


def test_equity_method_loss_is_also_investing(engine: ClassificationEngine) -> None:
    decision = engine.classify(
        item("SHARE_OF_LOSS_OF_ASSOCIATES", "지분법손실", "-50"), NON_FINANCIAL
    )

    assert decision.category is Ifrs18Category.INVESTING


# ---------------------------------------------------------------------------
# T4 — an unknown entity fact blocks, it does not guess
# ---------------------------------------------------------------------------


def test_unknown_activity_blocks_classification(engine: ClassificationEngine) -> None:
    """Test vector T4: the rule raises the question, not a model."""
    decision = engine.classify(item("INTEREST_INCOME", "이자수익"), UNKNOWN)

    assert decision.method is ClassificationMethod.UNRESOLVED
    assert decision.category is Ifrs18Category.UNCLASSIFIED
    assert decision.blocked_on_activity is ActivityType.INVESTING_IN_ASSETS
    assert decision.requires_human_review
    assert not decision.is_resolved


def test_answering_no_yields_investing(engine: ClassificationEngine) -> None:
    decision = engine.classify(item("INTEREST_INCOME", "이자수익"), NON_FINANCIAL)

    assert decision.category is Ifrs18Category.INVESTING
    assert decision.method is ClassificationMethod.RULE


def test_answering_yes_yields_operating(engine: ClassificationEngine) -> None:
    """Test vector T3: a specified main business activity flips the answer."""
    facts = EntityFacts({ActivityType.INVESTING_IN_ASSETS: True})

    decision = engine.classify(item("INTEREST_INCOME", "이자수익"), facts)

    assert decision.category is Ifrs18Category.OPERATING
    assert decision.method is ClassificationMethod.RULE


def test_unknown_and_false_are_not_conflated(engine: ClassificationEngine) -> None:
    """The tri-state is the whole mechanism; collapsing it would skip the question."""
    blocked = engine.classify(item("INTEREST_EXPENSE", "이자비용", "-20"), UNKNOWN)
    answered = engine.classify(item("INTEREST_EXPENSE", "이자비용", "-20"), NON_FINANCIAL)

    assert blocked.method is ClassificationMethod.UNRESOLVED
    assert answered.category is Ifrs18Category.FINANCING


# ---------------------------------------------------------------------------
# Unverified citations are routed to a human
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "label", "expected"),
    [
        ("INTEREST_INCOME", "이자수익", Ifrs18Category.INVESTING),
        ("INTEREST_EXPENSE", "이자비용", Ifrs18Category.FINANCING),
    ],
)
def test_rules_with_unconfirmed_conditions_require_review(
    engine: ClassificationEngine, code: str, label: str, expected: Ifrs18Category
) -> None:
    """docs/07 remaining-verification items 4 and 5.

    Whether these two rules are genuinely conditional on a specified main
    business activity is unresolved against the issued standard, and both fire
    on ordinary non-financial corporates. Until that is checked, every match is
    put in front of a human.
    """
    decision = engine.classify(item(code, label), NON_FINANCIAL)

    assert decision.category is expected
    assert decision.requires_human_review


def test_confirmed_rules_do_not_require_review(engine: ClassificationEngine) -> None:
    decision = engine.classify(item("INCOME_TAX_EXPENSE", "법인세비용", "-26840"), NON_FINANCIAL)

    assert decision.category is Ifrs18Category.INCOME_TAX
    assert not decision.requires_human_review


# ---------------------------------------------------------------------------
# T14 — line-scoped facts (B65, B72)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "label", "question"),
    [
        ("FX_GAIN", "외환차익", "FX_UNDERLYING_ITEM"),
        ("FX_LOSS", "외환차손", "FX_UNDERLYING_ITEM"),
        ("DERIVATIVE_GAIN", "파생상품평가이익", "DERIVATIVE_RISK_MANAGED"),
        ("DERIVATIVE_LOSS", "파생상품평가손실", "DERIVATIVE_RISK_MANAGED"),
    ],
)
def test_inherited_categories_ask_per_line(
    engine: ClassificationEngine, code: str, label: str, question: str
) -> None:
    """B65 and B72 need a fact about *this* instrument or item.

    Two derivative lines in one statement can manage different risks, so the
    question cannot be answered once for the entity.
    """
    decision = engine.classify(item(code, label), NON_FINANCIAL)

    assert decision.method is ClassificationMethod.UNRESOLVED
    assert decision.blocked_on_line_fact == question
    assert decision.blocked_on_activity is None


def test_derivative_rule_cites_b72(engine: ClassificationEngine) -> None:
    decision = engine.classify(item("DERIVATIVE_GAIN", "통화선도평가이익"), NON_FINANCIAL)

    assert decision.rule_id == "IFRS18-DERIV-001"
    assert any("B72" in evidence.reference for evidence in decision.evidence)


def test_fx_rule_cites_b65(engine: ClassificationEngine) -> None:
    decision = engine.classify(item("FX_GAIN", "외환차익"), NON_FINANCIAL)

    assert decision.rule_id == "IFRS18-FX-001"
    assert any("B65" in evidence.reference for evidence in decision.evidence)


# ---------------------------------------------------------------------------
# Aggregates (Q5)
# ---------------------------------------------------------------------------


def test_aggregate_captions_are_not_classified(engine: ClassificationEngine) -> None:
    """One category cannot be right for a bucket holding items from several."""
    decision = engine.classify(item("OTHER_INCOME", "기타수익", aggregate=True), NON_FINANCIAL)

    assert decision.category is Ifrs18Category.UNCLASSIFIED
    assert decision.rule_id == "IFRS18-AGGREGATE-001"
    assert decision.requires_human_review


def test_a_decomposed_component_classifies_normally(engine: ClassificationEngine) -> None:
    """After decomposition the components are ordinary lines."""
    decision = engine.classify(item("INTEREST_INCOME", "이자수익"), NON_FINANCIAL)

    assert decision.category is Ifrs18Category.INVESTING
    assert decision.is_resolved


# ---------------------------------------------------------------------------
# The AI layer (spec §1)
# ---------------------------------------------------------------------------


class StubAdvisor:
    def __init__(self, category: Ifrs18Category | None, confidence: str) -> None:
        self.category = category
        self.confidence = Decimal(confidence)
        self.calls: list[str] = []

    def suggest(self, item: ClassifiableItem, facts: EntityFacts) -> ClassificationSuggestion:
        self.calls.append(item.raw_label)
        return ClassificationSuggestion(
            category=self.category,
            subcategory=None,
            confidence=self.confidence,
            reasoning="stub",
        )


def test_an_ai_classification_always_requires_review() -> None:
    """Spec §1: high confidence never makes an AI proposal final."""
    rules = _loaded_rules()
    advisor = StubAdvisor(Ifrs18Category.INVESTING, "0.99")
    engine = ClassificationEngine(rules, advisor=advisor)

    decision = engine.classify(item(None, "알수없는계정"), NON_FINANCIAL)

    assert decision.method is ClassificationMethod.AI
    assert decision.confidence == Decimal("0.99")
    assert decision.requires_human_review


def test_the_advisor_is_not_consulted_for_recognised_accounts() -> None:
    """Asking a model about a settled item adds cost and risk, not information."""
    rules = _loaded_rules()
    advisor = StubAdvisor(Ifrs18Category.FINANCING, "0.99")
    engine = ClassificationEngine(rules, advisor=advisor)

    engine.classify(item("REVENUE", "매출액"), NON_FINANCIAL)

    assert advisor.calls == []


def test_the_advisor_is_consulted_for_unrecognised_accounts() -> None:
    rules = _loaded_rules()
    advisor = StubAdvisor(Ifrs18Category.INVESTING, "0.99")
    engine = ClassificationEngine(rules, advisor=advisor)

    engine.classify(item(None, "알수없는계정"), NON_FINANCIAL)

    assert advisor.calls == ["알수없는계정"]


def test_advisor_can_be_disabled_per_call() -> None:
    rules = _loaded_rules()
    advisor = StubAdvisor(Ifrs18Category.INVESTING, "0.99")
    engine = ClassificationEngine(rules, advisor=advisor)

    decision = engine.classify(item(None, "알수없는계정"), NON_FINANCIAL, use_advisor=False)

    assert advisor.calls == []
    assert decision.method is ClassificationMethod.RESIDUAL_DEFAULT


def test_an_advisor_that_declines_falls_back_to_the_residual() -> None:
    rules = _loaded_rules()
    engine = ClassificationEngine(rules, advisor=StubAdvisor(None, "0"))

    decision = engine.classify(item(None, "알수없는계정"), NON_FINANCIAL)

    assert decision.method is ClassificationMethod.RESIDUAL_DEFAULT
    assert decision.requires_human_review


# ---------------------------------------------------------------------------
# T11 — rule-set consistency is checked at construction
# ---------------------------------------------------------------------------


def _loaded_rules() -> tuple[ClassificationRuleSpec, ...]:
    from app.data.rule_catalog import load_rules

    _, _, rules = load_rules()
    return rules


def _rule(rule_id: str, priority: int, category: Ifrs18Category) -> ClassificationRuleSpec:
    return ClassificationRuleSpec(
        rule_id=rule_id,
        priority=priority,
        description="test",
        condition={"field": {"name": "statement_section", "op": "eq", "value": "PL"}},
        outcome=Outcome(category=category),
        source=RuleSource(
            type="OTHER",
            reference="test",
            verification_status=RuleVerificationStatus.UNVERIFIED,
        ),
    )


def test_duplicate_rule_ids_are_rejected() -> None:
    with pytest.raises(RuleSetError, match="duplicate rule ids"):
        ClassificationEngine(
            (
                _rule("SAME", 1, Ifrs18Category.OPERATING),
                _rule("SAME", 2, Ifrs18Category.INVESTING),
            )
        )


def test_rules_sharing_a_priority_are_rejected() -> None:
    """Test vector T11: the engine refuses to start rather than pick one.

    Equal priorities make the outcome depend on list order, which is not
    something a reviewer can see in the data.
    """
    with pytest.raises(RuleSetError, match="share a priority"):
        ClassificationEngine(
            (
                _rule("A", 10, Ifrs18Category.OPERATING),
                _rule("B", 10, Ifrs18Category.INVESTING),
            )
        )


def test_first_match_wins(engine: ClassificationEngine) -> None:
    """Tax is checked before the residual, so it must win."""
    outcome = engine.evaluate(item("INCOME_TAX_EXPENSE", "법인세비용"), NON_FINANCIAL)

    assert outcome.status is RuleStatus.MATCH
    assert outcome.rule_id == "IFRS18-TAX-001"


# ---------------------------------------------------------------------------
# T6 — zero and negative amounts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("amount", ["0", "-70000", "0.000001"])
def test_amount_does_not_affect_category(engine: ClassificationEngine, amount: str) -> None:
    decision = engine.classify(item("REVENUE", "매출액", amount), NON_FINANCIAL)

    assert decision.category is Ifrs18Category.OPERATING


# ---------------------------------------------------------------------------
# T7 — duplicate captions classify independently
# ---------------------------------------------------------------------------


def test_duplicate_captions_are_classified_independently(
    engine: ClassificationEngine,
) -> None:
    first = ClassifiableItem(
        line_id="a",
        raw_label="기타수익",
        amount=Decimal("10"),
        normalized_account_code="OTHER_INCOME",
        account_ancestors=("OTHER_INCOME",),
        is_aggregate=True,
    )
    second = ClassifiableItem(
        line_id="b",
        raw_label="기타수익",
        amount=Decimal("20"),
        normalized_account_code="INTEREST_INCOME",
        account_ancestors=("INTEREST_INCOME",),
    )

    decisions = engine.classify_all((first, second), NON_FINANCIAL)

    assert decisions[0].category is Ifrs18Category.UNCLASSIFIED
    assert decisions[1].category is Ifrs18Category.INVESTING
    assert decisions[0].line_id == "a"
    assert decisions[1].line_id == "b"
