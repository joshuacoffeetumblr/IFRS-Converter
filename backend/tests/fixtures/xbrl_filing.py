"""A synthetic DART-shaped XBRL instance, built as code.

**Synthetic.** It imitates the structure of a real DART filing — both bases,
four periods, and a note breakdown tagged under an extra axis — but the figures
are invented. It validates the reader, not the account dictionary.

The structure is what matters, because it is what the reader depends on:

* the same concept is reported under a consolidated *and* a separate context,
  so choosing between them cannot be skipped;
* four periods overlap, so "the first one" and "the latest one" are different
  answers and only the asked-for one is right;
* note components carry the caption's period and basis plus one extra
  dimension, which is how a filing says "this is the note's own table";
* a segment breakdown reuses a *statement* concept under an extra dimension,
  which must not be mistaken for the figure on the face of the statement.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

BASIS_AXIS = "ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis"
NOTE_AXIS = (
    "ifrs-full:CarryingAmountAccumulatedDepreciation"
    "AmortisationAndImpairmentAndGrossCarryingAmountAxis"
)
NOTE_MEMBER = "dart:ReportedAmountMember"

NAMESPACES = {
    "xbrli": "http://www.xbrl.org/2003/instance",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
    "link": "http://www.xbrl.org/2003/linkbase",
    "iso4217": "http://www.xbrl.org/2003/iso4217",
    # The two the reader maps. A filing picks its own prefixes; the reader
    # matches on the namespace, so these deliberately are not "ifrs-full"/"dart".
    "ifrs-full": "https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full",
    "dart": "http://dart.fss.or.kr/taxonomy/2026-01-31/ifrs/dart",
}

CURRENT = (dt.date(2026, 1, 1), dt.date(2026, 6, 30))
QUARTER = (dt.date(2026, 4, 1), dt.date(2026, 6, 30))
PRIOR = (dt.date(2025, 1, 1), dt.date(2025, 6, 30))


@dataclass(frozen=True)
class Fact:
    concept: str
    value: Decimal
    #: Dimensions beyond consolidated-or-separate. Empty means the figure on
    #: the face of the statement.
    dimensions: tuple[tuple[str, str], ...] = ()


#: One consolidated half-year that reconciles: the detail lines re-sum to every
#: printed subtotal, so a reader that drops a line is caught by arithmetic.
#:
#:   Revenue            1,000
#:   Cost of sales       (600)
#:   Gross profit         400
#:   SG&A                (150)
#:   Operating profit     250
#:   Finance income        90   = interest 50 + FX gain 30 + derivative gain 10
#:   Finance costs        (40)  = interest 25 + FX loss 15
#:   Profit before tax    300
#:   Income tax           (70)
#:   Profit               230
FACE: tuple[Fact, ...] = (
    Fact("ifrs-full:Revenue", Decimal(1000)),
    Fact("ifrs-full:CostOfSales", Decimal(600)),
    Fact("ifrs-full:GrossProfit", Decimal(400)),
    Fact("dart:TotalSellingGeneralAdministrativeExpenses", Decimal(150)),
    Fact("dart:OperatingIncomeLoss", Decimal(250)),
    Fact("ifrs-full:FinanceIncome", Decimal(90)),
    Fact("ifrs-full:FinanceCosts", Decimal(40)),
    Fact("ifrs-full:ProfitLossBeforeTax", Decimal(300)),
    Fact("ifrs-full:IncomeTaxExpenseContinuingOperations", Decimal(70)),
    Fact("ifrs-full:ProfitLoss", Decimal(230)),
)

NOTE_DIMENSIONS = ((NOTE_AXIS, NOTE_MEMBER),)

NOTES: tuple[Fact, ...] = (
    Fact("dart:InterestIncomeFinanceIncome", Decimal(50), NOTE_DIMENSIONS),
    Fact("ifrs-full:ForeignExchangeGain", Decimal(30), NOTE_DIMENSIONS),
    Fact("dart:GainFromDerivatives", Decimal(10), NOTE_DIMENSIONS),
    Fact("dart:InterestExpenseFinanceExpense", Decimal(25), NOTE_DIMENSIONS),
    Fact("ifrs-full:ForeignExchangeLoss", Decimal(15), NOTE_DIMENSIONS),
)

#: Revenue again, under a segment dimension. A reader that takes any context of
#: the right period would report the segment's revenue as the company's.
SEGMENT: tuple[Fact, ...] = (
    Fact(
        "ifrs-full:Revenue",
        Decimal(7),
        ((("ifrs-full:SegmentsAxis"), "dart:SemiconductorMember"),),
    ),
)


@dataclass
class Filing:
    """The facts of a filing, keyed by the context they belong to."""

    #: (start, end, basis member) -> the facts reported there
    sections: dict[tuple[dt.date, dt.date, str], tuple[Fact, ...]] = field(default_factory=dict)
    currency: str = "KRW"

    def add(
        self,
        period: tuple[dt.date, dt.date],
        basis: str,
        facts: tuple[Fact, ...],
    ) -> Filing:
        start, end = period
        self.sections[(start, end, basis)] = facts
        return self


def default_filing() -> Filing:
    """Both bases, three periods, with notes and a segment on the current one."""
    filing = Filing()
    filing.add(CURRENT, "ifrs-full:ConsolidatedMember", (*FACE, *NOTES, *SEGMENT))
    filing.add(CURRENT, "ifrs-full:SeparateMember", FACE)
    filing.add(QUARTER, "ifrs-full:ConsolidatedMember", FACE)
    filing.add(PRIOR, "ifrs-full:ConsolidatedMember", FACE)
    return filing


def _context_id(start: dt.date, end: dt.date, basis: str, fact: Fact) -> str:
    parts = [f"D{start:%Y%m%d}_{end:%Y%m%d}", basis.replace(":", "_")]
    parts += [
        f"{axis.replace(':', '_')}_{member.replace(':', '_')}" for axis, member in fact.dimensions
    ]
    return "_".join(parts)


def _member(axis: str, member: str) -> str:
    return f'<xbrldi:explicitMember dimension="{axis}">{member}</xbrldi:explicitMember>'


def build_xbrl(path: Path, filing: Filing | None = None) -> Path:
    """Write `filing` as an XBRL instance document."""
    filing = filing or default_filing()

    declarations = " ".join(f'xmlns:{prefix}="{uri}"' for prefix, uri in NAMESPACES.items())
    contexts: dict[str, str] = {}
    facts: list[str] = []

    for (start, end, basis), section in filing.sections.items():
        for fact in section:
            identifier = _context_id(start, end, basis, fact)
            members = [_member(BASIS_AXIS, basis)]
            members += [_member(axis, member) for axis, member in fact.dimensions]
            contexts.setdefault(
                identifier,
                f'<xbrli:context id="{identifier}">'
                "<xbrli:entity><xbrli:identifier "
                'scheme="http://dart.fss.or.kr">00000000</xbrli:identifier>'
                f"<xbrli:segment>{''.join(members)}</xbrli:segment></xbrli:entity>"
                f"<xbrli:period><xbrli:startDate>{start:%Y-%m-%d}</xbrli:startDate>"
                f"<xbrli:endDate>{end:%Y-%m-%d}</xbrli:endDate></xbrli:period>"
                "</xbrli:context>",
            )
            facts.append(
                f'<{fact.concept} contextRef="{identifier}" unitRef="UNIT" '
                f'decimals="0">{fact.value}</{fact.concept}>'
            )

    unit = (
        '<xbrli:unit id="UNIT"><xbrli:measure>'
        f"iso4217:{filing.currency}</xbrli:measure></xbrli:unit>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<xbrli:xbrl {declarations}>"
        f"{''.join(contexts.values())}{unit}{''.join(facts)}"
        "</xbrli:xbrl>"
    )
    path.write_text(document, encoding="utf-8")
    return path
