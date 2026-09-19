"""The pipeline service — the one place that knows the stage order."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.ingest.xlsx import read_income_statement
from app.domain.enums import ActivityType, Ifrs18Category, SubtotalKey
from app.domain.extraction import ExtractedStatement
from app.domain.rules import EntityFacts
from app.services.pipeline import classify_statement, run
from scripts.demo_export import answer_open_questions, decompose_aggregates
from tests.fixtures.korean_income_statement import SignStyle, build_workbook

NON_FINANCIAL = EntityFacts(
    {
        ActivityType.INVESTING_IN_ASSETS: False,
        ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS: False,
    }
)


@pytest.fixture
def source(tmp_path: Path) -> ExtractedStatement:
    return read_income_statement(build_workbook(tmp_path / "fs.xlsx", style=SignStyle.PARENTHESES))


def test_subtotals_never_reach_classification(source: ExtractedStatement) -> None:
    classified = classify_statement(source, NON_FINANCIAL, use_advisor=False)

    labels = {item.line.raw_label for item in classified}
    assert "영업이익" not in labels
    assert "당기순이익" not in labels


def test_the_normalized_account_is_carried_through(source: ExtractedStatement) -> None:
    """Revenue is identified by account downstream, so it must survive."""
    classified = classify_statement(source, NON_FINANCIAL, use_advisor=False)

    revenue = next(item for item in classified if item.line.raw_label == "매출액")
    assert revenue.normalized_account_code == "REVENUE"


def test_an_unresolved_question_blocks_the_run(source: ExtractedStatement) -> None:
    """Without a reviewer the run stops at the gate, which is the point.

    Decomposing a caption and identifying what produced a foreign exchange
    difference are decisions only a person can make, so an unattended run must
    not quietly produce a finalized statement.
    """
    result = run(decompose_aggregates(source), NON_FINANCIAL, use_advisor=False)

    assert not result.reconciled
    assert "NO_UNANSWERED_QUESTIONS" in {f.check for f in result.validation.blocking_failures}


def test_the_run_completes_once_the_questions_are_answered(
    source: ExtractedStatement,
) -> None:
    decomposed = decompose_aggregates(source)
    classified = answer_open_questions(
        classify_statement(decomposed, NON_FINANCIAL, use_advisor=False)
    )

    from app.domain.impact import analyse
    from app.domain.statement import reconstruct
    from app.domain.validation import validate

    reconstructed = reconstruct(decomposed, classified, facts=NON_FINANCIAL)
    report = validate(decomposed, classified, reconstructed)
    impact = analyse(decomposed, classified, reconstructed)

    assert report.passed
    assert reconstructed.amount(SubtotalKey.OPERATING_PROFIT) == Decimal("126000")
    assert impact.waterfall_balances


def test_undecomposed_aggregates_block_the_run(source: ExtractedStatement) -> None:
    result = run(source, NON_FINANCIAL, use_advisor=False)

    assert not result.reconciled
    assert "CATEGORY_COMPLETENESS" in {f.check for f in result.validation.blocking_failures}


def test_the_run_never_changes_the_total(source: ExtractedStatement) -> None:
    """Holds whether or not the run reconciled."""
    result = run(source, NON_FINANCIAL, use_advisor=False)

    before = sum((line.amount for line in source.detail_lines), start=Decimal(0))
    assert result.reconstructed.total_of_all_lines == before


def test_equity_method_lands_in_investing(source: ExtractedStatement) -> None:
    result = run(source, NON_FINANCIAL, use_advisor=False)

    equity = next(item for item in result.classified if item.line.raw_label == "지분법이익")
    assert equity.category is Ifrs18Category.INVESTING
