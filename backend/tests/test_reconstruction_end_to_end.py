"""Extract → normalize → classify → reconstruct → validate, on the fixture.

The point of this file is the last two steps. Everything before it was already
covered; what is asserted here is that the gate holds on real output, and that
resolving the statement produces the operating-profit change the product exists
to explain.
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
from app.domain.enums import (
    ActivityType,
    ClassificationMethod,
    ConfidenceBand,
    Ifrs18Category,
    SubtotalKey,
)
from app.domain.extraction import ExtractedStatement, reconcile_extraction
from app.domain.impact import analyse
from app.domain.normalization import normalize_statement
from app.domain.rules import ClassifiableItem, EntityFacts
from app.domain.statement import ClassifiedLine, reconstruct
from app.domain.validation import validate
from tests.fixtures.korean_income_statement import SignStyle, build_workbook

NON_FINANCIAL = EntityFacts(
    {
        ActivityType.INVESTING_IN_ASSETS: False,
        ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS: False,
    }
)

#: Contents of each aggregate caption, as a preparer would read them from the
#: notes. Each set sums exactly to the caption it replaces.
NOTE_DETAIL: dict[str, tuple[tuple[str, str], ...]] = {
    "기타수익": (("유형자산처분이익", "12000"),),
    "금융수익": (("이자수익", "5000"), ("매출채권 외환차익", "3000")),
    "금융비용": (("이자비용", "-14000"),),
    "기타비용": (("유형자산처분손실", "-9000"),),
}


def _decompose_aggregates(source: ExtractedStatement) -> ExtractedStatement:
    lines = []
    next_ordinal = 100
    for extracted in source.lines:
        detail = NOTE_DETAIL.get(extracted.raw_label)
        if detail is None:
            lines.append(extracted)
            continue
        parent, children = decompose(
            extracted,
            tuple(Component(label, Decimal(amount)) for label, amount in detail),
            next_ordinal=next_ordinal,
        )
        next_ordinal += len(children)
        lines.extend([parent, *children])
    return ExtractedStatement(
        source_file=source.source_file,
        sheet=source.sheet,
        lines=tuple(lines),
        currency=source.currency,
        scale=source.scale,
    )


def _answer_fx_question(decision: ClassificationDecision) -> ClassificationDecision:
    """A reviewer confirms the FX difference arose on a trade receivable.

    IFRS 18 B65 then puts it in the same category as the receivable: operating.
    """
    return ClassificationDecision(
        line_id=decision.line_id,
        category=Ifrs18Category.OPERATING,
        subcategory=None,
        method=ClassificationMethod.USER,
        rule_id=decision.rule_id,
        confidence=Decimal(1),
        confidence_band=ConfidenceBand.HIGH,
        requires_human_review=False,
        evidence=decision.evidence,
        reasoning="Arose on a trade receivable, so follows it (IFRS 18 B65).",
    )


def _classify(source: ExtractedStatement, *, answer_questions: bool) -> tuple[ClassifiedLine, ...]:
    dictionary = get_dictionary()
    engine = get_engine()
    report = normalize_statement(source, dictionary)

    results = []
    for normalized in report.lines:
        if normalized.line.is_subtotal:
            continue
        definition = dictionary.get(normalized.code) if normalized.code else None
        ancestors: tuple[str, ...] = ()
        if definition:
            ancestors = (definition.code,)
            if definition.parent_code:
                ancestors += (definition.parent_code,)

        decision = engine.classify(
            ClassifiableItem(
                line_id=normalized.line.raw_label,
                raw_label=normalized.line.raw_label,
                amount=normalized.line.amount,
                normalized_account_code=normalized.code,
                account_ancestors=ancestors,
                is_aggregate=normalized.needs_decomposition,
            ),
            NON_FINANCIAL,
        )
        if answer_questions and decision.blocked_on_line_fact == "FX_UNDERLYING_ITEM":
            decision = _answer_fx_question(decision)
        results.append(
            ClassifiedLine(
                line=normalized.line,
                decision=decision,
                normalized_account_code=normalized.code,
            )
        )
    return tuple(results)


@pytest.fixture
def source(tmp_path: Path) -> ExtractedStatement:
    return read_income_statement(build_workbook(tmp_path / "fs.xlsx", style=SignStyle.PARENTHESES))


# ---------------------------------------------------------------------------
# The gate blocks while work remains
# ---------------------------------------------------------------------------


def test_aggregates_block_finalization(source: ExtractedStatement) -> None:
    """The statement cannot be finalized while captions remain undecomposed."""
    decisions = _classify(source, answer_questions=False)
    report = validate(source, decisions, reconstruct(source, decisions, facts=NON_FINANCIAL))

    assert not report.passed
    assert "CATEGORY_COMPLETENESS" in {f.check for f in report.blocking_failures}


def test_blocking_does_not_produce_spurious_failures(source: ExtractedStatement) -> None:
    """One failure, naming the real problem.

    Unclassified amounts are carried into profit before tax precisely so that
    check keeps passing; otherwise the user would see two failures and have to
    work out which one matters.
    """
    decisions = _classify(source, answer_questions=False)
    report = validate(source, decisions, reconstruct(source, decisions, facts=NON_FINANCIAL))

    assert {f.check for f in report.blocking_failures} == {"CATEGORY_COMPLETENESS"}


def test_total_invariance_holds_even_while_blocked(source: ExtractedStatement) -> None:
    decisions = _classify(source, answer_questions=False)
    report = validate(source, decisions, reconstruct(source, decisions, facts=NON_FINANCIAL))

    assert next(f for f in report.findings if f.check == "TOTAL_INVARIANCE").passed


# ---------------------------------------------------------------------------
# Fully resolved
# ---------------------------------------------------------------------------


@pytest.fixture
def resolved(source: ExtractedStatement) -> tuple[ExtractedStatement, tuple[ClassifiedLine, ...]]:
    decomposed = _decompose_aggregates(source)
    return decomposed, _classify(decomposed, answer_questions=True)


def test_extraction_still_reconciles_after_decomposition(
    resolved: tuple[ExtractedStatement, tuple[ClassifiedLine, ...]],
) -> None:
    decomposed, _ = resolved

    assert reconcile_extraction(decomposed).passed


def test_a_resolved_statement_passes_every_check(
    resolved: tuple[ExtractedStatement, tuple[ClassifiedLine, ...]],
) -> None:
    decomposed, decisions = resolved

    report = validate(
        decomposed, decisions, reconstruct(decomposed, decisions, facts=NON_FINANCIAL)
    )

    assert report.passed, [(f.check, str(f.delta)) for f in report.failures]


def test_operating_profit_changes_and_profit_before_tax_does_not(
    resolved: tuple[ExtractedStatement, tuple[ClassifiedLine, ...]],
) -> None:
    """The product's thesis, on a whole statement.

    Decomposing 기타수익 and 기타비용 brings the disposal gain and loss into
    operating, and B65 brings the FX on trade receivables with them. Operating
    profit moves; profit before tax and profit for the period do not.
    """
    decomposed, decisions = resolved
    result = reconstruct(decomposed, decisions, facts=NON_FINANCIAL)

    assert result.reported_operating_profit == Decimal("120000")
    assert result.amount(SubtotalKey.OPERATING_PROFIT) == Decimal("126000")
    # 12,000 disposal gain - 9,000 disposal loss + 3,000 FX on receivables.
    assert result.amount(SubtotalKey.OPERATING_PROFIT) - Decimal("120000") == Decimal("6000")

    assert result.amount(SubtotalKey.PROFIT_BEFORE_TAX) == Decimal("122000")
    assert result.amount(SubtotalKey.PROFIT_FOR_THE_PERIOD) == Decimal("95160")


def test_categories_land_where_the_standard_puts_them(
    resolved: tuple[ExtractedStatement, tuple[ClassifiedLine, ...]],
) -> None:
    decomposed, decisions = resolved
    result = reconstruct(decomposed, decisions, facts=NON_FINANCIAL)

    by_category = {
        section.category: {line.line.raw_label for line in section.lines}
        for section in result.sections
    }

    # Equity-method results are investing unconditionally (docs/07 F3).
    assert "지분법이익" in by_category[Ifrs18Category.INVESTING]
    assert "이자수익" in by_category[Ifrs18Category.INVESTING]
    assert "이자비용" in by_category[Ifrs18Category.FINANCING]
    assert "법인세비용" in by_category[Ifrs18Category.INCOME_TAX]
    # FX on a trade receivable follows the receivable (B65).
    assert "매출채권 외환차익" in by_category[Ifrs18Category.OPERATING]
    assert not by_category[Ifrs18Category.UNCLASSIFIED]


def test_the_operating_bridge_accounts_for_the_whole_change(
    resolved: tuple[ExtractedStatement, tuple[ClassifiedLine, ...]],
) -> None:
    """Nothing unexplained: the bridge is checked, not merely drawn."""
    decomposed, decisions = resolved
    result = reconstruct(decomposed, decisions, facts=NON_FINANCIAL)

    report = validate(decomposed, decisions, result)

    bridge = next(f for f in report.findings if f.check == "OPERATING_BRIDGE")
    assert bridge.passed
    assert bridge.delta == Decimal(0)


def test_decomposed_parents_are_not_double_counted(
    resolved: tuple[ExtractedStatement, tuple[ClassifiedLine, ...]],
) -> None:
    """Parent and children are both present; only the children count."""
    decomposed, decisions = resolved
    result = reconstruct(decomposed, decisions, facts=NON_FINANCIAL)

    labels = {line.line.raw_label for line in result.summable_lines}
    assert "금융수익" not in labels
    assert {"이자수익", "매출채권 외환차익"} <= labels
    assert result.total_of_all_lines == Decimal("95160")


# ---------------------------------------------------------------------------
# Impact analysis on the same resolved statement (Phase 7)
# ---------------------------------------------------------------------------


def test_the_impact_headline_matches_the_statement(
    resolved: tuple[ExtractedStatement, tuple[ClassifiedLine, ...]],
) -> None:
    decomposed, decisions = resolved
    result = reconstruct(decomposed, decisions, facts=NON_FINANCIAL)

    impact = analyse(decomposed, decisions, result)

    headline = impact.headline
    assert headline is not None
    assert headline.before == Decimal("120000")
    assert headline.after == Decimal("126000")
    assert headline.change == Decimal("6000")
    assert headline.change_pct == Decimal("5.0000")


def test_the_waterfall_balances_on_a_real_statement(
    resolved: tuple[ExtractedStatement, tuple[ClassifiedLine, ...]],
) -> None:
    decomposed, decisions = resolved
    impact = analyse(decomposed, decisions, reconstruct(decomposed, decisions, facts=NON_FINANCIAL))

    assert impact.comparable
    assert impact.waterfall_balances


def test_the_drivers_are_the_decomposed_items(
    resolved: tuple[ExtractedStatement, tuple[ClassifiedLine, ...]],
) -> None:
    """Every driver came out of an aggregate caption.

    That is the point of decomposition: left aggregated, none of these would
    have appeared, and the operating-profit change would have read as zero.
    """
    decomposed, decisions = resolved
    impact = analyse(decomposed, decisions, reconstruct(decomposed, decisions, facts=NON_FINANCIAL))

    drivers = {r.label: r.impact_on_operating_profit for r in impact.top_reclassifications}
    assert drivers == {
        "유형자산처분이익": Decimal("12000"),
        "유형자산처분손실": Decimal("-9000"),
        "매출채권 외환차익": Decimal("3000"),
    }


def test_measures_that_must_not_move_report_zero(
    resolved: tuple[ExtractedStatement, tuple[ClassifiedLine, ...]],
) -> None:
    """The user-visible proof that IFRS 18 changed presentation, not profit."""
    decomposed, decisions = resolved
    impact = analyse(decomposed, decisions, reconstruct(decomposed, decisions, facts=NON_FINANCIAL))

    for key in ("REVENUE", "PROFIT_BEFORE_TAX", "PROFIT_FOR_THE_PERIOD"):
        kpi = impact.kpi(key)
        assert kpi is not None, key
        assert kpi.change == Decimal(0), key


def test_the_margin_moves_with_operating_profit_only(
    resolved: tuple[ExtractedStatement, tuple[ClassifiedLine, ...]],
) -> None:
    decomposed, decisions = resolved
    impact = analyse(decomposed, decisions, reconstruct(decomposed, decisions, facts=NON_FINANCIAL))

    margin = impact.kpi("OPERATING_PROFIT_MARGIN")
    assert margin is not None
    assert margin.before == Decimal("12.0000")
    assert margin.after == Decimal("12.6000")
    assert margin.change_bps == Decimal("60.00")
