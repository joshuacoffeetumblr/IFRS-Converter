"""Classifying the components a note breaks an aggregate caption into.

The statement-level captions are only half the vocabulary. IFRS 18's whole
point is that `기타수익` and `금융수익` hide investing and financing items, so
the lines that actually get reclassified are the ones *inside* the notes — and
those carry a different, finer set of captions.

A real filing showed the cost of getting this wrong. The dictionary already had
every account the rules key on — `DERIVATIVE_GAIN`, `DIVIDEND_INCOME`,
`RENTAL_INCOME` and the rest — but its English synonyms did not include the
spellings the IFRS taxonomy actually uses. `Gain from derivatives` did not
match `Gain on derivatives`; `Lent income` did not match `Rental income`. The
lines then fell through to the operating residual, silently, and the
reclassification this product exists to measure simply did not happen.

So these tests assert the **rule fires**, not merely that a caption matched.
A synonym that resolves to an account no rule looks at would be no better than
no synonym at all.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.data.catalog import get_dictionary
from app.data.rule_catalog import get_engine
from app.domain.enums import ActivityType, Ifrs18Category
from app.domain.extraction import _looks_like_expense
from app.domain.rules import ClassifiableItem, EntityFacts

#: Not a main business activity, which is what sends investment returns out of
#: operating under IFRS 18 paragraphs 49-50.
MANUFACTURER = EntityFacts(
    {
        ActivityType.INVESTING_IN_ASSETS: False,
        ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS: False,
    }
)


def _classify(caption: str) -> tuple[str | None, str | None, Ifrs18Category]:
    """Caption → (account code, rule id, category), the way the pipeline does."""
    code = get_dictionary().match(caption).code
    ancestors: tuple[str, ...] = (code,) if code else ()
    decision = get_engine().classify(
        ClassifiableItem(
            line_id=caption,
            raw_label=caption,
            amount=Decimal("1000"),
            normalized_account_code=code,
            account_ancestors=ancestors,
        ),
        MANUFACTURER,
    )
    return code, decision.rule_id, decision.category


# ---------------------------------------------------------------------------
# The captions a filing's note actually carries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("caption", "code"),
    [
        ("Dividend income non-operating", "DIVIDEND_INCOME"),
        ("Lent income", "RENTAL_INCOME"),
        ("Gains on disposals of property, plant and equipment", "GAIN_ON_DISPOSAL_OF_PPE"),
        ("Losses on disposals of property, plant and equipment", "LOSS_ON_DISPOSAL_OF_PPE"),
        ("Interest income, finance income", "INTEREST_INCOME"),
        ("Interest expense, finance expense", "INTEREST_EXPENSE"),
        ("Foreign exchange gain", "FX_GAIN"),
        ("Foreign exchange loss", "FX_LOSS"),
        ("Gain from derivatives", "DERIVATIVE_GAIN"),
        ("Losses from derivatives", "DERIVATIVE_LOSS"),
        ("Miscellaneous Income", "MISCELLANEOUS_INCOME"),
        ("Miscellaneous Losses", "MISCELLANEOUS_LOSS"),
        ("Donations", "DONATIONS"),
    ],
)
def test_note_component_captions_resolve(caption: str, code: str) -> None:
    assert _classify(caption)[0] == code


@pytest.mark.parametrize(
    ("caption", "rule_id"),
    [
        # Paragraphs 49-50: a return on an investment, for an entity that does
        # not invest as a main business activity.
        ("Dividend income non-operating", "IFRS18-INVESTING-002"),
        ("Lent income", "IFRS18-INVESTING-003"),
        ("Interest income, finance income", "IFRS18-INVESTING-002"),
        # Financing.
        ("Interest expense, finance expense", "IFRS18-FINANCING-001"),
        # B65 — the answer depends on the underlying item, so the rule's job is
        # to *ask*, and the test is that it is reached at all.
        ("Foreign exchange gain", "IFRS18-FX-001"),
        ("Foreign exchange loss", "IFRS18-FX-001"),
        # B72 — likewise, on the risk the derivative manages.
        ("Gain from derivatives", "IFRS18-DERIV-001"),
        ("Losses from derivatives", "IFRS18-DERIV-001"),
    ],
)
def test_the_rule_actually_fires(caption: str, rule_id: str) -> None:
    """The assertion that matters. A caption resolving to an account no rule
    looks at is indistinguishable, in the output, from a caption nothing
    recognised — both end up operating by the residual."""
    assert _classify(caption)[1] == rule_id


@pytest.mark.parametrize(
    "caption",
    ["Foreign exchange gain", "Gain from derivatives", "Losses from derivatives"],
)
def test_b65_and_b72_ask_rather_than_guess(caption: str) -> None:
    """Neither the caption nor the account can say which item gave rise to an
    exchange difference, or which risk a derivative manages. Unanswered, the
    line stays UNCLASSIFIED and blocks — it is never quietly called operating.
    """
    assert _classify(caption)[2] is Ifrs18Category.UNCLASSIFIED


# ---------------------------------------------------------------------------
# The three leaves added for note-level captions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "caption", ["Miscellaneous Income", "Miscellaneous Losses", "Donations", "잡이익", "잡손실"]
)
def test_note_leaves_are_operating_and_do_not_block(caption: str) -> None:
    """`잡이익`, `잡손실` and `기부금` are already the residual inside a note, so
    there is nothing further to decompose. Mapped to the aggregate captions
    instead, they stayed permanently UNCLASSIFIED — which meant a fully
    decomposed real filing could never finalize, whatever the reviewer did."""
    code, _, category = _classify(caption)
    assert code is not None
    assert get_dictionary().is_ambiguous(code) is False
    assert category is Ifrs18Category.OPERATING


def test_the_aggregate_captions_are_still_aggregates() -> None:
    """Moving 잡이익 off 기타수익 must not have made 기타수익 itself a leaf: it is
    exactly the caption IFRS 18 wants looked inside."""
    dictionary = get_dictionary()
    for caption in ("기타수익", "기타비용", "Other gains", "Other losses"):
        code = dictionary.match(caption).code
        assert code is not None, caption
        assert dictionary.is_ambiguous(code) is True, caption


# ---------------------------------------------------------------------------
# Signs
# ---------------------------------------------------------------------------


def test_donations_reads_as_a_deduction() -> None:
    """A filing prints every figure unsigned, and the sign hypothesis is
    all-or-nothing: one unrecognised deduction makes the subtotals fail to
    reconcile, and the whole statement is refused as unreadable."""
    assert _looks_like_expense("Donations") is True
    assert _looks_like_expense("Donation") is True
