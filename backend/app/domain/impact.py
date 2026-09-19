"""Impact analysis: what changed, by how much, and why (spec §5, §6, §22, §23).

Pure arithmetic over a reconstruction. Nothing here re-decides anything; it
reports the consequences of decisions already made and already validated.

Two design points carry most of the weight:

**The waterfall is derived from the same per-line movement the reconciliation
gate checks**, so it cannot disagree with the statement. It is not drawn from
the figures and hoped to balance — it is the balance, rendered.

**A KPI with no "before" says so.** Profit before financing and income taxes,
and the investing and financing results, are categories IFRS 18 introduced;
under the entity's previous presentation they did not exist. Reporting a
"before" of zero for them would imply a change from nothing to something, which
is a different and false claim from "this did not previously exist".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.domain.enums import Ifrs18Category, SubtotalKey, SubtotalKind
from app.domain.extraction import ExtractedStatement
from app.domain.statement import (
    ClassifiedLine,
    Ifrs18Statement,
    operating_profit_before,
)

#: Percentages and margins are reported to four decimal places.
_RATIO_PRECISION = Decimal("0.0001")

#: How many named steps the waterfall shows before grouping the remainder.
DEFAULT_MAX_WATERFALL_STEPS = 5


class Unit(StrEnum):
    AMOUNT = "AMOUNT"
    PERCENT = "PERCENT"


class ReportedPlacement(StrEnum):
    """Where a line sat in the statement the entity actually published.

    Deliberately coarse. All the reported statement tells us is whether an item
    was inside the operating subtotal; it does not carry IFRS 18 categories, and
    inventing one for it would be a fabrication.
    """

    INSIDE_OPERATING = "INSIDE_OPERATING"
    OUTSIDE_OPERATING = "OUTSIDE_OPERATING"
    UNKNOWN = "UNKNOWN"


class StepKind(StrEnum):
    START = "START"
    DELTA = "DELTA"
    #: The part of reported operating profit that could not be attributed to
    #: any extracted line (open question Q2).
    UNATTRIBUTED = "UNATTRIBUTED"
    END = "END"


def _ratio(numerator: Decimal, denominator: Decimal | None) -> Decimal | None:
    """Percentage change, or ``None`` where it is not defined.

    Guarding division by zero matters here: a KPI that moved from zero has an
    undefined percentage change, and rendering it as 0% or ∞ would both be
    wrong. The magnitude of the base is used so a loss narrowing from -100 to
    -50 reports +50%, not -50%.
    """
    if denominator is None or denominator == 0:
        return None
    return ((numerator / abs(denominator)) * 100).quantize(_RATIO_PRECISION)


@dataclass(frozen=True, slots=True)
class Kpi:
    key: str
    after: Decimal
    #: ``None`` where the measure did not exist under the previous presentation.
    before: Decimal | None = None
    unit: Unit = Unit.AMOUNT
    #: For margins, the change expressed in basis points.
    change_bps: Decimal | None = None

    @property
    def change(self) -> Decimal | None:
        if self.before is None:
            return None
        return self.after - self.before

    @property
    def change_pct(self) -> Decimal | None:
        change = self.change
        if change is None:
            return None
        return _ratio(change, self.before)

    @property
    def is_new_measure(self) -> bool:
        return self.before is None


@dataclass(frozen=True, slots=True)
class Reclassification:
    """One line whose treatment moved it into or out of operating profit."""

    line_id: str
    label: str
    amount: Decimal
    reported_placement: ReportedPlacement
    ifrs18_category: Ifrs18Category
    impact_on_operating_profit: Decimal
    rule_id: str | None = None
    rationale: str | None = None

    @property
    def moved_into_operating(self) -> bool:
        return self.impact_on_operating_profit > 0


@dataclass(frozen=True, slots=True)
class WaterfallStep:
    kind: StepKind
    key: str
    label_ko: str
    label_en: str
    value: Decimal
    #: Populated on DELTA steps where every contributing line moved the same way.
    reported_placement: ReportedPlacement | None = None
    to_category: Ifrs18Category | None = None
    line_ids: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class ImpactAnalysis:
    kpis: tuple[Kpi, ...]
    waterfall: tuple[WaterfallStep, ...]
    reclassifications: tuple[Reclassification, ...]
    currency: str = "KRW"
    scale: int = 0
    #: False when the source printed no operating subtotal, so there is nothing
    #: to compare against and no bridge to draw. Stated rather than implied, so
    #: a caller renders "no reported operating profit" instead of "no change".
    comparable: bool = True

    def kpi(self, key: str) -> Kpi | None:
        return next((item for item in self.kpis if item.key == key), None)

    @property
    def headline(self) -> Kpi | None:
        return self.kpi("OPERATING_PROFIT")

    @property
    def top_reclassifications(self) -> tuple[Reclassification, ...]:
        return tuple(
            sorted(
                self.reclassifications,
                key=lambda item: abs(item.impact_on_operating_profit),
                reverse=True,
            )
        )

    @property
    def waterfall_balances(self) -> bool:
        """Every step from the start must land exactly on the end.

        Asserted rather than assumed: a waterfall that does not add up is the
        single most misleading thing this product could render.
        """
        if not self.waterfall:
            # Nothing to bridge, which is not the same as a broken bridge.
            return True
        start = next((s for s in self.waterfall if s.kind is StepKind.START), None)
        end = next((s for s in self.waterfall if s.kind is StepKind.END), None)
        if start is None or end is None:
            return False
        movement = sum(
            (s.value for s in self.waterfall if s.kind in (StepKind.DELTA, StepKind.UNATTRIBUTED)),
            start=Decimal(0),
        )
        return start.value + movement == end.value


# ---------------------------------------------------------------------------
# Per-line movement
# ---------------------------------------------------------------------------


def line_impact_on_operating_profit(source: ExtractedStatement, item: ClassifiedLine) -> Decimal:
    """How one line moved operating profit (ERD §5).

    ``after`` is its amount if IFRS 18 puts it in operating, otherwise zero.
    ``before`` is its amount if the entity presented it above its own operating
    subtotal, otherwise zero. The difference is the movement.

    Where the source printed **no** operating subtotal there is no "before" at
    all, so the movement is unknown rather than total. Returning the full
    amount here would report every operating line as newly reclassified and
    describe an unchanged statement as completely restructured.
    """
    if not item.is_summable or not _has_reported_operating_profit(source):
        return Decimal(0)
    after = item.amount if item.category is Ifrs18Category.OPERATING else Decimal(0)
    return after - operating_profit_before(source, item.line)


def _has_reported_operating_profit(source: ExtractedStatement) -> bool:
    return source.subtotal(SubtotalKind.REPORTED_OPERATING_PROFIT) is not None


def reported_placement(source: ExtractedStatement, item: ClassifiedLine) -> ReportedPlacement:
    from app.domain.enums import SubtotalKind

    subtotal = source.subtotal(SubtotalKind.REPORTED_OPERATING_PROFIT)
    if subtotal is None:
        return ReportedPlacement.UNKNOWN
    return (
        ReportedPlacement.INSIDE_OPERATING
        if item.line.ordinal < subtotal.ordinal
        else ReportedPlacement.OUTSIDE_OPERATING
    )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def analyse(
    source: ExtractedStatement,
    classified: tuple[ClassifiedLine, ...],
    reconstructed: Ifrs18Statement,
    *,
    max_waterfall_steps: int = DEFAULT_MAX_WATERFALL_STEPS,
) -> ImpactAnalysis:
    """Compute KPIs, the waterfall and the account-level detail."""
    comparable = _has_reported_operating_profit(source)
    return ImpactAnalysis(
        kpis=_kpis(source, reconstructed),
        waterfall=(
            _waterfall(source, classified, reconstructed, max_steps=max_waterfall_steps)
            if comparable
            else ()
        ),
        reclassifications=_reclassifications(source, classified),
        currency=reconstructed.currency,
        scale=reconstructed.scale,
        comparable=comparable,
    )


#: Accounts that constitute revenue for margin purposes.
REVENUE_ACCOUNT_CODES: frozenset[str] = frozenset({"REVENUE"})


def _revenue(reconstructed: Ifrs18Statement) -> Decimal:
    """Revenue, identified by account rather than by sign.

    A margin measured against "every positive operating line" would include
    disposal gains and foreign exchange differences, inflating the denominator
    and understating the margin — and it would move whenever an unrelated item
    was reclassified into operating, which is exactly the kind of spurious
    movement this screen exists to rule out.
    """
    return sum(
        (
            line.amount
            for line in reconstructed.summable_lines
            if line.normalized_account_code in REVENUE_ACCOUNT_CODES
        ),
        start=Decimal(0),
    )


def _kpis(source: ExtractedStatement, reconstructed: Ifrs18Statement) -> tuple[Kpi, ...]:
    reported_pbt = source.subtotal(SubtotalKind.PROFIT_BEFORE_TAX)
    reported_net = source.subtotal(SubtotalKind.PROFIT_FOR_THE_PERIOD)
    reported_op = reconstructed.reported_operating_profit

    operating_profit = reconstructed.amount(SubtotalKey.OPERATING_PROFIT)
    revenue = _revenue(reconstructed)

    margin_after = _ratio(operating_profit, revenue)
    margin_before = _ratio(reported_op, revenue) if reported_op is not None else None
    change_bps = (
        ((margin_after - margin_before) * 100).quantize(Decimal("0.01"))
        if margin_after is not None and margin_before is not None
        else None
    )

    kpis: list[Kpi] = [
        Kpi(key="REVENUE", after=revenue, before=revenue),
        Kpi(key="OPERATING_PROFIT", after=operating_profit, before=reported_op),
        Kpi(
            key="OPERATING_PROFIT_MARGIN",
            after=margin_after if margin_after is not None else Decimal(0),
            before=margin_before,
            unit=Unit.PERCENT,
            change_bps=change_bps,
        ),
        # Introduced by IFRS 18: there is no prior figure to compare against,
        # and inventing a zero would imply a change that did not happen.
        Kpi(
            key="PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES",
            after=reconstructed.amount(SubtotalKey.PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES),
            before=None,
        ),
        Kpi(
            key="PROFIT_BEFORE_TAX",
            after=reconstructed.amount(SubtotalKey.PROFIT_BEFORE_TAX),
            before=reported_pbt.amount if reported_pbt else None,
        ),
        Kpi(
            key="PROFIT_FOR_THE_PERIOD",
            after=reconstructed.amount(SubtotalKey.PROFIT_FOR_THE_PERIOD),
            before=reported_net.amount if reported_net else None,
        ),
        Kpi(
            key="INVESTING_RESULT",
            after=reconstructed.total(Ifrs18Category.INVESTING),
            before=None,
        ),
        Kpi(
            key="FINANCING_RESULT",
            after=reconstructed.total(Ifrs18Category.FINANCING),
            before=None,
        ),
    ]
    return tuple(kpis)


def _reclassifications(
    source: ExtractedStatement, classified: tuple[ClassifiedLine, ...]
) -> tuple[Reclassification, ...]:
    results: list[Reclassification] = []
    for item in classified:
        impact = line_impact_on_operating_profit(source, item)
        if impact == 0:
            continue
        results.append(
            Reclassification(
                line_id=item.decision.line_id,
                label=item.line.raw_label,
                amount=item.amount,
                reported_placement=reported_placement(source, item),
                ifrs18_category=item.category,
                impact_on_operating_profit=impact,
                rule_id=item.decision.rule_id,
                rationale=item.decision.reasoning,
            )
        )
    return tuple(results)


def _waterfall(
    source: ExtractedStatement,
    classified: tuple[ClassifiedLine, ...],
    reconstructed: Ifrs18Statement,
    *,
    max_steps: int,
) -> tuple[WaterfallStep, ...]:
    """Bridge reported operating profit to the IFRS 18 figure.

    Built so the arithmetic closes by construction rather than by luck, which
    is why it still balances when the reconciliation gate has failed — the
    impact screen is shown in a degraded state in that case (spec §19), and a
    bridge that silently did not add up there would be worse than no bridge.
    """
    end_value = reconstructed.amount(SubtotalKey.OPERATING_PROFIT)
    reported = reconstructed.reported_operating_profit
    start_value = reported if reported is not None else Decimal(0)

    movements = _reclassifications(source, classified)
    attributed = sum((item.impact_on_operating_profit for item in movements), start=Decimal(0))

    steps: list[WaterfallStep] = [
        WaterfallStep(
            kind=StepKind.START,
            key="REPORTED_OPERATING_PROFIT",
            label_ko="기존 영업이익",
            label_en="Reported operating profit",
            value=start_value,
        )
    ]

    # Anything the movements cannot explain is shown, not absorbed (Q2).
    unattributed = end_value - start_value - attributed
    if unattributed != 0:
        steps.append(
            WaterfallStep(
                kind=StepKind.UNATTRIBUTED,
                key="UNATTRIBUTED",
                label_ko="미귀속 차이",
                label_en="Unattributed difference",
                value=unattributed,
            )
        )

    ranked = sorted(movements, key=lambda item: abs(item.impact_on_operating_profit), reverse=True)
    named, remainder = ranked[:max_steps], ranked[max_steps:]

    for item in named:
        steps.append(
            WaterfallStep(
                kind=StepKind.DELTA,
                key=f"RECLASS_{item.line_id}",
                label_ko=item.label,
                label_en=item.label,
                value=item.impact_on_operating_profit,
                reported_placement=item.reported_placement,
                to_category=item.ifrs18_category,
                line_ids=(item.line_id,),
            )
        )

    if remainder:
        steps.append(
            WaterfallStep(
                kind=StepKind.DELTA,
                key="OTHER_RECLASSIFICATIONS",
                label_ko=f"기타 재분류 {len(remainder)}건",
                label_en=f"Other reclassifications ({len(remainder)})",
                value=sum(
                    (item.impact_on_operating_profit for item in remainder),
                    start=Decimal(0),
                ),
                line_ids=tuple(item.line_id for item in remainder),
            )
        )

    steps.append(
        WaterfallStep(
            kind=StepKind.END,
            key="IFRS18_OPERATING_PROFIT",
            label_ko="IFRS 18 영업이익",
            label_en="IFRS 18 operating profit",
            value=end_value,
        )
    )
    return tuple(steps)
