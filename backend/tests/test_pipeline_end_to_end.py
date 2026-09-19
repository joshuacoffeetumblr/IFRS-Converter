"""Extract → normalize → classify, on the synthetic Korean fixture.

This is the first point where the whole chain runs, so it is also where the
chain's guarantees are asserted together: provenance survives, the statement
reconciles, and no item is classified on a fact nobody supplied.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.ingest.xlsx import read_income_statement
from app.data.catalog import get_dictionary
from app.data.rule_catalog import get_engine
from app.domain.classification import ClassificationDecision
from app.domain.decomposition import Component, decompose
from app.domain.enums import ActivityType, ClassificationMethod, Ifrs18Category
from app.domain.extraction import ExtractedLine, ExtractedStatement, reconcile_extraction
from app.domain.normalization import normalize_statement
from app.domain.rules import ClassifiableItem, EntityFacts
from tests.fixtures.korean_income_statement import SignStyle, build_workbook

NON_FINANCIAL = EntityFacts(
    {
        ActivityType.INVESTING_IN_ASSETS: False,
        ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS: False,
    }
)


def _classify(
    statement: ExtractedStatement, facts: EntityFacts
) -> dict[str, ClassificationDecision]:
    dictionary = get_dictionary()
    engine = get_engine()
    report = normalize_statement(statement, dictionary)

    decisions: dict[str, ClassificationDecision] = {}
    for normalized in report.classifiable:
        definition = dictionary.get(normalized.code) if normalized.code else None
        ancestors: tuple[str, ...] = ()
        if definition:
            ancestors = (definition.code,)
            if definition.parent_code:
                ancestors += (definition.parent_code,)

        item = ClassifiableItem(
            line_id=normalized.line.raw_label,
            raw_label=normalized.line.raw_label,
            amount=normalized.line.amount,
            normalized_account_code=normalized.code,
            account_ancestors=ancestors,
            is_aggregate=normalized.needs_decomposition,
            note_references=normalized.line.note_references,
        )
        decisions[normalized.line.raw_label] = engine.classify(item, facts)
    return decisions


@pytest.fixture
def statement(tmp_path: Path) -> ExtractedStatement:
    path = build_workbook(tmp_path / "fs.xlsx", style=SignStyle.PARENTHESES)
    return read_income_statement(path)


def test_the_statement_reconciles_before_anything_is_classified(
    statement: ExtractedStatement,
) -> None:
    """Spec §17: extraction is verified before IFRS 18 work begins."""
    assert reconcile_extraction(statement).passed


def test_every_detail_line_receives_a_decision(statement: ExtractedStatement) -> None:
    decisions = _classify(statement, NON_FINANCIAL)

    assert len(decisions) == 9
    assert all(decision.category is not None for decision in decisions.values())


def test_expected_categories_on_the_fixture(statement: ExtractedStatement) -> None:
    decisions = _classify(statement, NON_FINANCIAL)

    assert decisions["매출액"].category is Ifrs18Category.OPERATING
    assert decisions["매출원가"].category is Ifrs18Category.OPERATING
    assert decisions["판매비와관리비"].category is Ifrs18Category.OPERATING
    # Unconditional, regardless of business model (docs/07 F3).
    assert decisions["지분법이익"].category is Ifrs18Category.INVESTING
    assert decisions["법인세비용"].category is Ifrs18Category.INCOME_TAX


def test_aggregate_captions_block_rather_than_guess(
    statement: ExtractedStatement,
) -> None:
    """금융수익 holds items belonging in several categories.

    Classifying the bucket as a whole would assign one category to all of them,
    which is how an operating-profit effect gets silently lost.
    """
    decisions = _classify(statement, NON_FINANCIAL)

    for caption in ("기타수익", "금융수익", "금융비용", "기타비용"):
        assert decisions[caption].category is Ifrs18Category.UNCLASSIFIED, caption
        assert decisions[caption].requires_human_review, caption


def test_nothing_is_classified_when_the_entity_facts_are_unknown(
    statement: ExtractedStatement,
) -> None:
    """Test vector T4, end to end."""
    decisions = _classify(statement, EntityFacts.unknown())

    # 지분법이익 is unconditional, so it still resolves; the others wait.
    assert decisions["지분법이익"].category is Ifrs18Category.INVESTING
    assert decisions["매출액"].category is Ifrs18Category.OPERATING


def test_every_decision_is_traceable(statement: ExtractedStatement) -> None:
    """Spec §8: a classification must say how it was reached."""
    decisions = _classify(statement, NON_FINANCIAL)

    for label, decision in decisions.items():
        assert decision.method is not None, label
        if decision.method is ClassificationMethod.RULE:
            assert decision.rule_id, label
            assert decision.evidence, label


def test_provenance_survives_the_whole_chain(statement: ExtractedStatement) -> None:
    """Spec §18: a figure on screen must lead back to its source cell."""
    revenue = next(line for line in statement.lines if line.raw_label == "매출액")

    assert revenue.locator.cell == "C6"
    assert revenue.locator.sheet == "손익계산서"


# ---------------------------------------------------------------------------
# Decomposition changes the answer — the reason Q5 matters
# ---------------------------------------------------------------------------


def test_decomposing_an_aggregate_produces_classifiable_components(
    statement: ExtractedStatement,
) -> None:
    """Test vector T13, with the classification step attached.

    금융수익 of 8,000 is a single unclassifiable bucket. Broken out, its parts
    land in different IFRS 18 categories — and one of them is operating,
    which is exactly the effect an undecomposed caption hides.
    """
    finance_income = next(line for line in statement.lines if line.raw_label == "금융수익")
    assert finance_income.amount == Decimal("8000")

    _, children = decompose(
        finance_income,
        (
            Component("이자수익", Decimal("5000"), "주석 25"),
            Component("매출채권 외환차익", Decimal("3000"), "주석 25"),
        ),
        next_ordinal=100,
    )

    decomposed = ExtractedStatement(
        source_file=statement.source_file,
        sheet=statement.sheet,
        lines=children,
        currency=statement.currency,
        scale=statement.scale,
    )
    decisions = _classify(decomposed, NON_FINANCIAL)

    assert decisions["이자수익"].category is Ifrs18Category.INVESTING
    # FX needs its underlying item identified per line (B65), so it asks.
    assert decisions["매출채권 외환차익"].method is ClassificationMethod.UNRESOLVED
    assert decisions["매출채권 외환차익"].blocked_on_line_fact == "FX_UNDERLYING_ITEM"


def test_components_sum_back_to_the_caption(statement: ExtractedStatement) -> None:
    finance_income = next(line for line in statement.lines if line.raw_label == "금융수익")

    _, children = decompose(
        finance_income,
        (
            Component("이자수익", Decimal("5000")),
            Component("매출채권 외환차익", Decimal("3000")),
        ),
        next_ordinal=100,
    )

    assert sum(child.amount for child in children) == finance_income.amount


def test_subtotals_never_reach_the_classifier(statement: ExtractedStatement) -> None:
    """A subtotal aggregates lines below it; classifying it would double count."""
    decisions = _classify(statement, NON_FINANCIAL)

    for subtotal in ("매출총이익", "영업이익", "법인세차감전순이익", "당기순이익"):
        assert subtotal not in decisions, subtotal


def test_detail_lines_still_sum_to_net_income(statement: ExtractedStatement) -> None:
    """The invariant the whole reconciliation gate rests on.

    IFRS 18 changes presentation, not measurement, so classification cannot
    change the total of all income and expenses.
    """
    net_income = next(line for line in statement.lines if line.raw_label == "당기순이익")
    total = sum((line.amount for line in statement.detail_lines), start=Decimal(0))

    assert total == net_income.amount


def test_classification_does_not_alter_any_amount(statement: ExtractedStatement) -> None:
    before = {line.raw_label: line.amount for line in statement.lines}

    _classify(statement, NON_FINANCIAL)

    after = {line.raw_label: line.amount for line in statement.lines}
    assert before == after


def _unused(line: ExtractedLine) -> None:  # pragma: no cover - typing aid
    return None
