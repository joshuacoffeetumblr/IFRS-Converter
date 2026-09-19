"""The shipped account catalog must be well-formed and actually cover statements."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.ingest.xlsx import read_income_statement
from app.data.catalog import (
    CatalogError,
    _definition,
    catalog_version,
    get_dictionary,
    load_catalog,
)
from app.domain.accounts import is_subtotal_caption, normalize_label
from app.domain.extraction import reconcile_extraction
from app.domain.normalization import normalize_statement
from tests.fixtures.korean_income_statement import (
    SignStyle,
    build_messy_workbook,
    build_workbook,
)

#: Phase 4 exit criterion (`docs/05-mvp-scope.md`).
COVERAGE_TARGET = Decimal("0.90")


def test_catalog_loads_and_is_versioned() -> None:
    version, definitions = load_catalog()

    assert version == catalog_version()
    assert len(definitions) >= 30


def test_account_codes_are_unique() -> None:
    _, definitions = load_catalog()
    codes = [definition.code for definition in definitions]

    assert len(codes) == len(set(codes))


def test_parent_references_resolve() -> None:
    _, definitions = load_catalog()
    known = {definition.code for definition in definitions}

    for definition in definitions:
        if definition.parent_code:
            assert definition.parent_code in known, definition.code


def test_no_two_accounts_claim_the_same_surface_form() -> None:
    """A surface form claimed twice makes matching depend on catalog order."""
    _, definitions = load_catalog()

    owners: dict[str, str] = {}
    collisions: list[tuple[str, str, str]] = []
    for definition in definitions:
        for form in definition.surface_forms:
            key = normalize_label(form)
            if not key:
                continue
            if key in owners and owners[key] != definition.code:
                collisions.append((form, owners[key], definition.code))
            owners.setdefault(key, definition.code)

    assert not collisions, f"surface forms claimed by two accounts: {collisions}"


def test_no_account_is_named_like_a_subtotal() -> None:
    """A caption denoting a subtotal must never resolve to an account."""
    _, definitions = load_catalog()

    offenders = [
        (definition.code, form)
        for definition in definitions
        for form in definition.surface_forms
        if is_subtotal_caption(form)
    ]

    assert not offenders, f"accounts using subtotal captions: {offenders}"


def test_aggregate_captions_are_marked_ambiguous() -> None:
    """These conceal the operating-profit effect until decomposed (Q5)."""
    dictionary = get_dictionary()

    for caption in ("기타수익", "기타비용", "영업외수익", "영업외비용", "금융수익", "금융비용"):
        result = dictionary.match(caption)
        assert result.matched, caption
        assert result.ambiguous, caption


def test_specific_accounts_are_not_ambiguous() -> None:
    dictionary = get_dictionary()

    for caption in ("이자수익", "배당금수익", "지분법이익", "법인세비용"):
        assert not dictionary.match(caption).ambiguous, caption


def test_malformed_entry_is_rejected() -> None:
    with pytest.raises(CatalogError):
        _definition({"code": "X", "label_ko": "테스트"})  # no label_en


# ---------------------------------------------------------------------------
# Coverage against a statement — the Phase 4 exit criterion
# ---------------------------------------------------------------------------


def test_canonical_statement_is_fully_covered(tmp_path: Path) -> None:
    path = build_workbook(tmp_path / "fs.xlsx", style=SignStyle.PARENTHESES)
    statement = read_income_statement(path)

    report = normalize_statement(statement, get_dictionary())

    assert report.coverage >= COVERAGE_TARGET
    assert not report.unresolved


def test_messy_statement_is_covered_without_ai(tmp_path: Path) -> None:
    """The real test: captions that are not in the catalog verbatim.

    The canonical fixture uses the catalog's own labels, so passing on it proves
    little. These captions are enumerated, spaced differently, annotated with
    note references, or use a different house term.
    """
    path = build_messy_workbook(tmp_path / "messy.xlsx")
    statement = read_income_statement(path)

    report = normalize_statement(statement, get_dictionary())

    assert report.coverage >= COVERAGE_TARGET, [item.line.raw_label for item in report.unresolved]
    assert not report.unresolved


def test_messy_statement_still_reconciles(tmp_path: Path) -> None:
    """Regression: enumerated subtotals were once read as ordinary lines.

    `Ⅲ. 매출총이익` was not recognised as a subtotal, so its amount was added to
    the very running total it was meant to verify.
    """
    path = build_messy_workbook(tmp_path / "messy.xlsx")

    report = reconcile_extraction(read_income_statement(path))

    assert report.passed, [(c.check, str(c.delta)) for c in report.failures]


def test_enumerated_subtotals_are_flagged_as_subtotals(tmp_path: Path) -> None:
    path = build_messy_workbook(tmp_path / "messy.xlsx")

    statement = read_income_statement(path)

    flagged = {line.raw_label for line in statement.lines if line.is_subtotal}
    assert flagged == {
        "Ⅲ. 매출총이익",
        "Ⅴ. 영업이익",
        "Ⅵ. 법인세비용차감전순이익",
        "Ⅶ. 당기순이익",
    }


def test_aggregates_in_the_fixture_are_flagged_for_decomposition(tmp_path: Path) -> None:
    path = build_workbook(tmp_path / "fs.xlsx", style=SignStyle.PARENTHESES)

    report = normalize_statement(read_income_statement(path), get_dictionary())

    assert {item.line.raw_label for item in report.needing_decomposition} == {
        "기타수익",
        "금융수익",
        "금융비용",
        "기타비용",
    }
