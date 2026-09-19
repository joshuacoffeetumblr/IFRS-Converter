"""The normalization pipeline, including where the AI layer sits (spec §1, §4)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.accounts import AccountDefinition, AccountDictionary
from app.domain.enums import NormalizationMethod, SignNormalization, SubtotalKind
from app.domain.extraction import ExtractedLine, ExtractedStatement, SourceLocator
from app.domain.normalization import (
    AccountSuggestion,
    NullAccountAdvisor,
    normalize_statement,
)

LOCATOR = SourceLocator(source_file="fs.xlsx", sheet="손익계산서")


def line(
    ordinal: int, label: str, amount: str, *, subtotal: SubtotalKind | None = None
) -> ExtractedLine:
    return ExtractedLine(
        ordinal=ordinal,
        raw_label=label,
        raw_value=amount,
        amount=Decimal(amount),
        sign_normalization=SignNormalization.AS_IS,
        locator=LOCATOR,
        is_subtotal=subtotal is not None,
        subtotal_kind=subtotal,
    )


@pytest.fixture
def dictionary() -> AccountDictionary:
    return AccountDictionary(
        (
            AccountDefinition(code="REVENUE", label_ko="매출액", label_en="Revenue"),
            AccountDefinition(code="COST_OF_SALES", label_ko="매출원가", label_en="Cost of sales"),
            AccountDefinition(
                code="OTHER_INCOME",
                label_ko="기타수익",
                label_en="Other income",
                ambiguous_by_default=True,
            ),
        )
    )


@pytest.fixture
def statement() -> ExtractedStatement:
    return ExtractedStatement(
        source_file="fs.xlsx",
        lines=(
            line(0, "매출액", "1000"),
            line(1, "매출원가", "-700"),
            line(2, "매출총이익", "300", subtotal=SubtotalKind.GROSS_PROFIT),
            line(3, "기타수익", "50"),
            line(4, "알수없는계정", "10"),
        ),
    )


class StubAdvisor:
    """A stand-in for the AI layer. The domain never imports a model client."""

    def __init__(self, code: str | None, confidence: str) -> None:
        self.code = code
        self.confidence = Decimal(confidence)
        self.calls: list[str] = []

    def suggest(self, label: str, *, context: str | None = None) -> AccountSuggestion:
        self.calls.append(label)
        return AccountSuggestion(self.code, self.confidence, "stub reasoning")


def test_subtotals_are_never_normalized(
    statement: ExtractedStatement, dictionary: AccountDictionary
) -> None:
    """A subtotal is a reconciliation target; mapping it invites double counting."""
    report = normalize_statement(statement, dictionary)

    subtotal = next(item for item in report.lines if item.line.raw_label == "매출총이익")
    assert subtotal.code is None
    assert not subtotal.requires_human_review
    assert subtotal not in report.classifiable


def test_dictionary_matches_do_not_need_review(
    statement: ExtractedStatement, dictionary: AccountDictionary
) -> None:
    report = normalize_statement(statement, dictionary)

    revenue = next(item for item in report.classifiable if item.line.raw_label == "매출액")
    assert revenue.code == "REVENUE"
    assert revenue.method is NormalizationMethod.EXACT
    assert not revenue.requires_human_review


def test_aggregate_accounts_are_flagged_for_decomposition(
    statement: ExtractedStatement, dictionary: AccountDictionary
) -> None:
    report = normalize_statement(statement, dictionary)

    assert [item.line.raw_label for item in report.needing_decomposition] == ["기타수익"]


def test_without_an_advisor_unknown_captions_go_to_a_human(
    statement: ExtractedStatement, dictionary: AccountDictionary
) -> None:
    report = normalize_statement(statement, dictionary, advisor=NullAccountAdvisor())

    unknown = next(item for item in report.classifiable if item.line.raw_label == "알수없는계정")
    assert not unknown.matched
    assert unknown.requires_human_review


def test_advisor_is_consulted_only_for_unresolved_captions(
    statement: ExtractedStatement, dictionary: AccountDictionary
) -> None:
    """Keeping AI usage small is what keeps it cheap and auditable."""
    advisor = StubAdvisor("REVENUE", "0.95")

    normalize_statement(statement, dictionary, advisor=advisor)

    assert advisor.calls == ["알수없는계정"]


def test_an_ai_mapping_always_requires_review(
    statement: ExtractedStatement, dictionary: AccountDictionary
) -> None:
    """Spec §1: high confidence never makes an AI proposal final."""
    advisor = StubAdvisor("COST_OF_SALES", "0.99")

    report = normalize_statement(statement, dictionary, advisor=advisor)

    suggested = next(item for item in report.classifiable if item.line.raw_label == "알수없는계정")
    assert suggested.code == "COST_OF_SALES"
    assert suggested.method is NormalizationMethod.AI
    assert suggested.requires_human_review


def test_low_confidence_suggestions_are_discarded(
    statement: ExtractedStatement, dictionary: AccountDictionary
) -> None:
    advisor = StubAdvisor("COST_OF_SALES", "0.40")

    report = normalize_statement(statement, dictionary, advisor=advisor)

    suggested = next(item for item in report.classifiable if item.line.raw_label == "알수없는계정")
    assert not suggested.matched
    assert suggested.requires_human_review


def test_coverage_excludes_ai_matches(
    statement: ExtractedStatement, dictionary: AccountDictionary
) -> None:
    """The Phase 4 target measures the dictionary, not the model."""
    with_ai = normalize_statement(
        statement, dictionary, advisor=StubAdvisor("COST_OF_SALES", "0.99")
    )
    without_ai = normalize_statement(statement, dictionary)

    # 3 of 4 detail lines resolve from the dictionary either way.
    assert with_ai.coverage == without_ai.coverage == Decimal("0.7500")
    assert with_ai.matched_count == 4
    assert with_ai.dictionary_matched_count == 3


def test_coverage_of_an_empty_statement_is_zero(dictionary: AccountDictionary) -> None:
    empty = ExtractedStatement(source_file="fs.xlsx", lines=())

    assert normalize_statement(empty, dictionary).coverage == Decimal(0)


def test_null_advisor_suggests_nothing() -> None:
    suggestion = NullAccountAdvisor().suggest("무엇이든")

    assert suggestion.code is None
    assert suggestion.confidence == Decimal(0)
