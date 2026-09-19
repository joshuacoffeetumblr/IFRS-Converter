"""Account normalization. Pure — definitions built in the test, no catalog file."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.accounts import (
    AccountDefinition,
    AccountDictionary,
    is_subtotal_caption,
    normalize_label,
)
from app.domain.enums import AccountNature, NormalizationMethod


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("매출액", "매출액"),
        ("  매출액  ", "매출액"),
        ("매출 액", "매출액"),
        # Enumeration is position in the document, not part of the identity.
        ("Ⅰ. 매출액", "매출액"),
        ("1. 매출액", "매출액"),
        ("(1) 매출액", "매출액"),
        ("가. 매출액", "매출액"),
        ("①매출액", "매출액"),
        # A trailing parenthetical is a cross-reference.
        ("매출액(주석 21)", "매출액"),
        ("매출액（주석 21）", "매출액"),
        # English folds to lower case.
        ("Revenue", "revenue"),
        ("  Cost of Sales ", "costofsales"),
    ],
)
def test_labels_reduce_to_a_canonical_form(printed: str, expected: str) -> None:
    assert normalize_label(printed) == expected


def test_a_wholly_parenthetical_label_is_preserved() -> None:
    """수익(매출액) is the whole caption; stripping it would leave nothing useful."""
    assert normalize_label("수익(매출액)") == "수익"


@pytest.fixture
def dictionary() -> AccountDictionary:
    return AccountDictionary(
        (
            AccountDefinition(
                code="REVENUE",
                label_ko="매출액",
                label_en="Revenue",
                nature=AccountNature.INCOME,
                synonyms_ko=("매출", "영업수익"),
                synonyms_en=("Sales",),
            ),
            AccountDefinition(
                code="INTEREST_INCOME",
                label_ko="이자수익",
                label_en="Interest income",
                nature=AccountNature.INCOME,
            ),
            AccountDefinition(
                code="OTHER_INCOME",
                label_ko="기타수익",
                label_en="Other income",
                nature=AccountNature.INCOME,
                ambiguous_by_default=True,
            ),
            AccountDefinition(
                code="NON_OPERATING_INCOME",
                label_ko="영업외수익",
                label_en="Non-operating income",
                nature=AccountNature.INCOME,
                synonyms_ko=("영업외이익",),
            ),
        )
    )


def test_exact_label_matches_exactly(dictionary: AccountDictionary) -> None:
    result = dictionary.match("매출액")

    assert result.code == "REVENUE"
    assert result.method is NormalizationMethod.EXACT
    assert result.score == Decimal(1)


def test_synonym_matches(dictionary: AccountDictionary) -> None:
    result = dictionary.match("영업수익")

    assert result.code == "REVENUE"
    assert result.method is NormalizationMethod.SYNONYM


def test_enumerated_and_annotated_labels_match(dictionary: AccountDictionary) -> None:
    for printed in ("Ⅰ. 매출액", "1. 매출 액", "매출액(주석 21)"):
        assert dictionary.match(printed).code == "REVENUE", printed


def test_english_matches_case_insensitively(dictionary: AccountDictionary) -> None:
    assert dictionary.match("SALES").code == "REVENUE"
    assert dictionary.match("interest income").code == "INTEREST_INCOME"


def test_aggregate_accounts_are_flagged(dictionary: AccountDictionary) -> None:
    """An aggregate caption needs decomposition before it can be classified."""
    assert dictionary.match("기타수익").ambiguous
    assert not dictionary.match("이자수익").ambiguous


def test_unknown_label_does_not_match(dictionary: AccountDictionary) -> None:
    result = dictionary.match("완전히다른계정과목입니다")

    assert not result.matched
    assert result.method is None


# ---------------------------------------------------------------------------
# The dangerous cases
# ---------------------------------------------------------------------------


def test_operating_profit_is_never_matched_to_non_operating_income(
    dictionary: AccountDictionary,
) -> None:
    """영업이익 and 영업외이익 differ by one character and mean opposite things.

    A regression test for a real defect: with a 0.88 threshold the pair scored
    0.889 and the subtotal 영업이익 was silently mapped to non-operating income.
    """
    for printed in ("영업이익", "Ⅴ. 영업이익", "영업손실"):
        result = dictionary.match(printed)
        assert result.code != "NON_OPERATING_INCOME", printed
        assert not result.matched, printed


@pytest.mark.parametrize(
    "caption",
    [
        "매출총이익",
        "영업이익",
        "Ⅴ. 영업이익",
        "법인세차감전순이익",
        "Ⅵ. 법인세비용차감전순이익",
        "당기순이익",
        "Ⅶ. 당기순이익",
    ],
)
def test_subtotal_captions_are_recognised(caption: str) -> None:
    assert is_subtotal_caption(caption)


@pytest.mark.parametrize("caption", ["매출액", "이자수익", "기타수익", "판매비와관리비"])
def test_account_captions_are_not_subtotals(caption: str) -> None:
    assert not is_subtotal_caption(caption)


def test_dictionary_refuses_subtotal_captions(dictionary: AccountDictionary) -> None:
    """A subtotal is not an account; matching one would double count it."""
    assert not dictionary.match("당기순이익").matched
    assert not dictionary.match("매출총이익").matched


def test_ambiguous_fuzzy_candidates_are_declined() -> None:
    """When two different accounts are equally close, choosing is a coin flip."""
    ambiguous = AccountDictionary(
        (
            AccountDefinition(code="A", label_ko="파생상품평가이익", label_en="Derivative gain"),
            AccountDefinition(code="B", label_ko="파생상품평가손실", label_en="Derivative loss"),
        ),
        fuzzy_threshold=Decimal("0.80"),
        fuzzy_margin=Decimal("0.10"),
    )

    result = ambiguous.match("파생상품평가")

    assert not result.matched
    assert len(result.candidates) >= 2


def test_near_misses_are_reported_even_when_declined(dictionary: AccountDictionary) -> None:
    """A reviewer should see what the engine considered, not just that it failed."""
    result = dictionary.match("영업외수")

    assert not result.matched
    assert result.candidates
    assert result.candidates[0].code == "NON_OPERATING_INCOME"


def test_fuzzy_accepts_a_clear_winner() -> None:
    clear = AccountDictionary(
        (
            AccountDefinition(code="REVENUE", label_ko="매출액", label_en="Revenue"),
            AccountDefinition(code="TAX", label_ko="법인세비용", label_en="Income tax"),
        ),
        fuzzy_threshold=Decimal("0.80"),
    )

    result = clear.match("법인세비용액")

    assert result.code == "TAX"
    assert result.method is NormalizationMethod.FUZZY
    assert result.score >= Decimal("0.80")


def test_empty_label_does_not_match(dictionary: AccountDictionary) -> None:
    assert not dictionary.match("").matched
    assert not dictionary.match("   ").matched
