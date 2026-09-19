"""Reconstructing the statement of profit or loss under IFRS 18.

Pure arithmetic. Every figure here is a plain sum, which is possible only
because of the sign convention fixed in architecture §5: every amount is the
signed effect on profit or loss, income positive and expense negative. With
that, no subtotal needs per-account sign logic, and the largest class of bug in
tools of this kind simply cannot occur.

Two properties of the result matter more than the presentation:

* **Operating profit changes; profit before tax does not.** IFRS 18 changes how
  income and expenses are *presented*, not how they are measured, so
  reclassifying an item moves it between categories without altering the total.
* **Subtotals and decomposed parents never participate.** A subtotal already
  aggregates the lines above it, and a decomposed caption's amount is carried by
  its children. Counting either would double count.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.classification import ClassificationDecision
from app.domain.enums import (
    ActivityType,
    Ifrs18Category,
    SubtotalKey,
    SubtotalKind,
)
from app.domain.extraction import ExtractedLine, ExtractedStatement
from app.domain.rules import EntityFacts

#: The order categories are presented in.
CATEGORY_ORDER: tuple[Ifrs18Category, ...] = (
    Ifrs18Category.OPERATING,
    Ifrs18Category.INVESTING,
    Ifrs18Category.FINANCING,
    Ifrs18Category.INCOME_TAX,
    Ifrs18Category.DISCONTINUED_OPERATION,
    Ifrs18Category.UNCLASSIFIED,
)

CATEGORY_LABELS: dict[Ifrs18Category, tuple[str, str]] = {
    Ifrs18Category.OPERATING: ("Operating", "영업"),
    Ifrs18Category.INVESTING: ("Investing", "투자"),
    Ifrs18Category.FINANCING: ("Financing", "재무"),
    Ifrs18Category.INCOME_TAX: ("Income taxes", "법인세"),
    Ifrs18Category.DISCONTINUED_OPERATION: ("Discontinued operations", "중단영업"),
    Ifrs18Category.UNCLASSIFIED: ("Unclassified", "미분류"),
}

SUBTOTAL_LABELS: dict[SubtotalKey, tuple[str, str]] = {
    SubtotalKey.OPERATING_PROFIT: ("Operating profit", "영업이익"),
    SubtotalKey.PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES: (
        "Profit before financing and income taxes",
        "재무·법인세차감전이익",
    ),
    SubtotalKey.PROFIT_BEFORE_TAX: ("Profit before tax", "법인세차감전순이익"),
    SubtotalKey.PROFIT_FROM_CONTINUING_OPERATIONS: (
        "Profit from continuing operations",
        "계속영업이익",
    ),
    SubtotalKey.PROFIT_FOR_THE_PERIOD: ("Profit for the period", "당기순이익"),
}


class ReconstructionError(ValueError):
    """The statement cannot be reconstructed from what was supplied."""


@dataclass(frozen=True, slots=True)
class ClassifiedLine:
    """An extracted line paired with the decision made about it."""

    line: ExtractedLine
    decision: ClassificationDecision

    @property
    def amount(self) -> Decimal:
        return self.line.amount

    @property
    def category(self) -> Ifrs18Category:
        return Ifrs18Category(self.decision.category)

    @property
    def is_summable(self) -> bool:
        return self.line.is_summable


@dataclass(frozen=True, slots=True)
class Section:
    category: Ifrs18Category
    label_en: str
    label_ko: str
    lines: tuple[ClassifiedLine, ...]
    total: Decimal

    @property
    def is_empty(self) -> bool:
        return not self.lines


@dataclass(frozen=True, slots=True)
class Subtotal:
    key: SubtotalKey
    label_en: str
    label_ko: str
    amount: Decimal
    #: False where IFRS 18 forbids presenting this subtotal.
    presented: bool = True
    suppressed_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Ifrs18Statement:
    sections: tuple[Section, ...]
    subtotals: tuple[Subtotal, ...]
    currency: str = "KRW"
    scale: int = 0
    #: Operating profit as the entity itself reported it (open question Q2,
    #: resolved: the headline "before" is the published figure, not ours).
    reported_operating_profit: Decimal | None = None

    def section(self, category: Ifrs18Category) -> Section | None:
        return next((s for s in self.sections if s.category is category), None)

    def total(self, category: Ifrs18Category) -> Decimal:
        section = self.section(category)
        return section.total if section else Decimal(0)

    def subtotal(self, key: SubtotalKey) -> Subtotal | None:
        return next((s for s in self.subtotals if s.key is key), None)

    def amount(self, key: SubtotalKey) -> Decimal:
        subtotal = self.subtotal(key)
        if subtotal is None:  # pragma: no cover - every key is always computed
            raise ReconstructionError(f"subtotal {key} was not computed")
        return subtotal.amount

    @property
    def summable_lines(self) -> tuple[ClassifiedLine, ...]:
        return tuple(line for section in self.sections for line in section.lines)

    @property
    def total_of_all_lines(self) -> Decimal:
        """The invariant total: unchanged by any reclassification."""
        return sum((line.amount for line in self.summable_lines), start=Decimal(0))


def reported_operating_profit(statement: ExtractedStatement) -> Decimal | None:
    """The 영업이익 subtotal the entity published, if it printed one."""
    subtotal = statement.subtotal(SubtotalKind.REPORTED_OPERATING_PROFIT)
    return subtotal.amount if subtotal else None


def operating_profit_before(statement: ExtractedStatement, line: ExtractedLine) -> Decimal:
    """A line's contribution to the **reported** operating profit.

    Defined by position: everything the entity presented above its own
    operating subtotal was inside it (ERD §5). Anything below contributed zero,
    whatever category IFRS 18 now assigns it.
    """
    subtotal = statement.subtotal(SubtotalKind.REPORTED_OPERATING_PROFIT)
    if subtotal is None or not line.is_summable:
        return Decimal(0)
    return line.amount if line.ordinal < subtotal.ordinal else Decimal(0)


def _pbfit_is_prohibited(facts: EntityFacts) -> bool:
    """IFRS 18 paragraph 73.

    An entity whose specified main business activity is providing financing to
    customers, and which classifies the interest expense on liabilities
    unrelated to that activity in the operating category, is **not permitted**
    to present "profit or loss before financing and income taxes". It is a
    prohibition, not an exemption (docs/07 F9).

    Such entities are outside the MVP's validated scope, so the statement marks
    the subtotal unpresented and the validation gate blocks finalization rather
    than emitting a statement that presents a subtotal the standard forbids.
    """
    return facts.is_main(ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS) is True


def reconstruct(
    statement: ExtractedStatement,
    decisions: tuple[ClassifiedLine, ...],
    *,
    facts: EntityFacts | None = None,
) -> Ifrs18Statement:
    """Build the IFRS 18 statement from classified lines.

    Both required subtotals are presented **even when they are equal** — an
    entity with no investing items still presents operating profit and profit
    before financing and income taxes (docs/07 F9).
    """
    facts = facts or EntityFacts.unknown()

    buckets: dict[Ifrs18Category, list[ClassifiedLine]] = {
        category: [] for category in CATEGORY_ORDER
    }
    for classified in decisions:
        if not classified.is_summable:
            # A subtotal or a decomposed parent. Its amount is already carried
            # by other lines; including it would double count.
            continue
        buckets[classified.category].append(classified)

    sections: list[Section] = []
    for category in CATEGORY_ORDER:
        lines = tuple(buckets[category])
        label_en, label_ko = CATEGORY_LABELS[category]
        sections.append(
            Section(
                category=category,
                label_en=label_en,
                label_ko=label_ko,
                lines=lines,
                total=sum((line.amount for line in lines), start=Decimal(0)),
            )
        )

    totals = {section.category: section.total for section in sections}

    # With the sign convention every subtotal is a plain sum: income tax is
    # already negative, so it is added rather than subtracted.
    operating = totals[Ifrs18Category.OPERATING]
    investing = totals[Ifrs18Category.INVESTING]
    financing = totals[Ifrs18Category.FINANCING]
    tax = totals[Ifrs18Category.INCOME_TAX]
    discontinued = totals[Ifrs18Category.DISCONTINUED_OPERATION]
    unclassified = totals[Ifrs18Category.UNCLASSIFIED]

    pbfit = operating + investing
    # Items still UNCLASSIFIED are pre-tax — we do not yet know *which*
    # category, but we do know it is not income tax or discontinued
    # operations, since a rule identifies both of those positively. Placing
    # them here keeps profit before tax reconcilable with the reported figure,
    # so the only check that fails is the one naming the real problem
    # (CATEGORY_COMPLETENESS) rather than a second, misleading one. They are
    # deliberately excluded from operating profit and from PBFIT: those
    # subtotals must not silently absorb an item nobody has classified.
    profit_before_tax = pbfit + financing + unclassified
    profit_continuing = profit_before_tax + tax
    profit_for_period = profit_continuing + discontinued

    prohibited = _pbfit_is_prohibited(facts)
    amounts: dict[SubtotalKey, tuple[Decimal, bool, str | None]] = {
        SubtotalKey.OPERATING_PROFIT: (operating, True, None),
        SubtotalKey.PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES: (
            pbfit,
            not prohibited,
            (
                "IFRS 18 paragraph 73 does not permit this subtotal for an entity "
                "whose main business activity is providing financing to customers."
                if prohibited
                else None
            ),
        ),
        SubtotalKey.PROFIT_BEFORE_TAX: (profit_before_tax, True, None),
        SubtotalKey.PROFIT_FROM_CONTINUING_OPERATIONS: (profit_continuing, True, None),
        SubtotalKey.PROFIT_FOR_THE_PERIOD: (profit_for_period, True, None),
    }

    subtotals = tuple(
        Subtotal(
            key=key,
            label_en=SUBTOTAL_LABELS[key][0],
            label_ko=SUBTOTAL_LABELS[key][1],
            amount=amount,
            presented=presented,
            suppressed_reason=reason,
        )
        for key, (amount, presented, reason) in amounts.items()
    )

    return Ifrs18Statement(
        sections=tuple(sections),
        subtotals=subtotals,
        currency=statement.currency,
        scale=statement.scale,
        reported_operating_profit=reported_operating_profit(statement),
    )
