"""Reading a statement captioned in English (spec: "Korean + English").

Every caption table in the ingest layer was Korean-only, and the three of them
fail in a chain. A real DART filing carries an English label linkbase, and a
statement captioned from it hit all three at once:

1. `classify_subtotal` recognised no English subtotal, so the statement had
   **no subtotals** — and a statement whose own arithmetic cannot be checked is
   refused outright (§17), however perfectly it was extracted.
2. The four subtotals then went through account matching as if they were
   ordinary lines, so dictionary coverage read 46% when the real figure was
   100%.
3. `_looks_like_expense` recognised no English deduction, so a filing printing
   every figure unsigned — the Korean convention, which the English captions do
   not change — could never have its signs derived.

The trap in fixing (3): IFRS labels use a parenthetical to make a caption
sign-neutral. `Share of profit (loss) of associates` is **income**, and
matching `loss` inside that parenthetical flips a gain into a deduction.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.ingest.grid import classify_subtotal
from app.domain.accounts import is_subtotal_caption
from app.domain.enums import SubtotalKind
from app.domain.extraction import _looks_like_expense
from app.services.dry_run import dry_run

#: The consolidated income statement of a real half-year DART filing, captioned
#: from the IFRS taxonomy exactly as its English label linkbase spells them,
#: and — as the filing does — with every figure printed positive.
FILING_ROWS: tuple[tuple[str, str, int], ...] = (
    ("Revenue", "3", 305372914000000),
    ("Cost of sales", "", 104159030000000),
    ("Gross profit", "", 201213884000000),
    ("Selling general administrative expenses", "19", 54488675000000),
    ("Operating income(loss)", "", 146725209000000),
    ("Other gains", "20", 916374000000),
    ("Other losses", "20", 837090000000),
    (
        "Share of profit (loss) of associates and joint ventures accounted for using equity method",
        "9",
        483842000000,
    ),
    ("Finance income", "21", 14475364000000),
    ("Finance costs", "21", 8495460000000),
    ("Profit (loss) before tax", "", 153268239000000),
    ("Tax expense (income)", "22", 34418506000000),
    ("Profit (loss)", "", 118849733000000),
)


@pytest.fixture
def filing(tmp_path: Path) -> Path:
    path = tmp_path / "filing.csv"
    body = "\n".join(f'"{label}",{note},{amount}' for label, note, amount in FILING_ROWS)
    path.write_text(f"﻿과목,주석,제 58 기 반기\n{body}\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Subtotals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("caption", "kind"),
    [
        ("Gross profit", SubtotalKind.GROSS_PROFIT),
        ("Operating income(loss)", SubtotalKind.REPORTED_OPERATING_PROFIT),
        ("Operating profit", SubtotalKind.REPORTED_OPERATING_PROFIT),
        ("Profit (loss) before tax", SubtotalKind.PROFIT_BEFORE_TAX),
        ("Profit before income tax", SubtotalKind.PROFIT_BEFORE_TAX),
        ("Profit (loss)", SubtotalKind.PROFIT_FOR_THE_PERIOD),
        ("Profit for the period", SubtotalKind.PROFIT_FOR_THE_PERIOD),
    ],
)
def test_english_subtotals_are_recognised(caption: str, kind: SubtotalKind) -> None:
    assert classify_subtotal(caption) is kind


@pytest.mark.parametrize(
    "caption",
    [
        "Profit from disposal of investments",
        "Gross profit margin",
        "Operating lease expense",
        "Profit sharing bonus",
    ],
)
def test_an_ordinary_account_is_not_swallowed_as_a_subtotal(caption: str) -> None:
    """`Profit (loss)` reduces to `profit`, so English subtotals are matched
    exactly. Matching them as prefixes would make a subtotal of every one of
    these — silently adding a detail line to the total it was there to verify.
    """
    assert classify_subtotal(caption) is None
    assert is_subtotal_caption(caption) is False


def test_korean_subtotals_still_match_their_suffixed_forms() -> None:
    """Korean carries its qualifiers as suffixes, so prefix matching is what
    recognises them, and that behaviour has to survive the English addition."""
    assert classify_subtotal("영업이익(손실)") is SubtotalKind.REPORTED_OPERATING_PROFIT
    assert classify_subtotal("Ⅲ. 매출총이익") is SubtotalKind.GROSS_PROFIT


# ---------------------------------------------------------------------------
# 3. Signs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "caption",
    [
        "Cost of sales",
        "Selling general administrative expenses",
        "Other losses",
        "Finance costs",
        "Tax expense (income)",
        "Impairment loss on trade receivables",
    ],
)
def test_english_deductions_are_recognised(caption: str) -> None:
    assert _looks_like_expense(caption) is True


@pytest.mark.parametrize(
    "caption",
    [
        "Revenue",
        "Other gains",
        "Finance income",
        # The trap: `(loss)` is a sign-neutral qualifier, not a deduction.
        "Share of profit (loss) of associates and joint ventures accounted for using equity method",
        "Gain (loss) on disposal of property",
    ],
)
def test_a_parenthetical_qualifier_does_not_make_income_a_deduction(caption: str) -> None:
    assert _looks_like_expense(caption) is False


# ---------------------------------------------------------------------------
# End to end, on the filing
# ---------------------------------------------------------------------------


def test_the_filing_reproduces_its_own_arithmetic(filing: Path) -> None:
    """The check everything rests on. Read correctly — subtotals recognised and
    signs derived — a real filing reconciles to the won against figures it
    printed entirely unsigned."""
    run = dry_run(filing)

    assert run.signs_inferred
    assert run.extraction_passed
    assert len(run.extraction.checks) == 5
    assert all(check.computed == check.reported for check in run.extraction.checks)


def test_the_filings_captions_are_all_recognised(filing: Path) -> None:
    run = dry_run(filing)

    assert run.subtotal_lines == 4
    assert run.detail_lines == 9
    assert run.unresolved_captions == ()
    assert run.normalization.coverage == Decimal(1)


def test_equity_method_income_keeps_its_sign(filing: Path) -> None:
    """The one line where the parenthetical trap would have done real damage:
    a gain of ~484bn turned into a deduction of the same size, which moves
    profit before tax by twice that."""
    run = dry_run(filing)

    equity = next(line for line in run.source.lines if "associates" in line.raw_label)
    assert equity.amount == Decimal("483842000000")


def test_aggregate_captions_still_block_finalization(filing: Path) -> None:
    """Reading the file correctly is not the same as being able to report on
    it. `Other gains`, `Other losses`, `Finance income` and `Finance costs` are
    exactly the captions IFRS 18 exists to look inside, and they stay
    UNCLASSIFIED until somebody decomposes them (§19)."""
    run = dry_run(filing)

    assert run.verdict == "BLOCKED"
    assert not run.gate_passed
    assert {line.line.raw_label for line in run.unclassified} == {
        "Other gains",
        "Other losses",
        "Finance income",
        "Finance costs",
    }
