"""The reconciliation gate (spec §19, architecture §7).

Not a warning banner bolted on at the end. A project cannot be finalized while
a blocking check fails, and a failing analysis is never rendered as if it were
a normal result.

The checks divide into two kinds, and the distinction is the point:

**Blocking, with no tolerance.** Total invariance is the backbone. IFRS 18
changes presentation, not measurement, so reclassifying an item moves it between
categories without altering the sum of all income and expenses. If that sum
changes, the software has a bug — not a rounding artefact — and no tolerance
can make it acceptable. The same applies to structural checks: an item with no
category, or a subtotal the standard forbids, is a defect on the face of the
output.

**Tolerant, because the source document rounds.** Agreement with figures the
entity *printed* may legitimately differ by a presentation unit when a statement
is stated in 백만원. Those get a configurable tolerance — and any non-zero
difference is still reported with its magnitude, never silently absorbed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.domain.enums import Ifrs18Category, SubtotalKey, SubtotalKind
from app.domain.extraction import ExtractedStatement
from app.domain.statement import ClassifiedLine, Ifrs18Statement, operating_profit_before


class Severity(StrEnum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    passed: bool
    severity: Severity
    detail: str
    expected: Decimal | None = None
    actual: Decimal | None = None
    tolerance: Decimal = Decimal(0)

    @property
    def delta(self) -> Decimal | None:
        if self.expected is None or self.actual is None:
            return None
        return self.actual - self.expected


@dataclass(frozen=True, slots=True)
class ValidationReport:
    findings: tuple[Finding, ...] = field(default=())

    @property
    def failures(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.passed)

    @property
    def blocking_failures(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.failures if f.severity is Severity.BLOCKING)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.failures if f.severity is Severity.WARNING)

    @property
    def passed(self) -> bool:
        """Whether the reconstruction may be presented as a normal result."""
        return not self.blocking_failures


@dataclass(frozen=True, slots=True)
class Tolerances:
    """Spec §19. Total invariance is deliberately absent: it has none."""

    profit_before_tax: Decimal = Decimal(1)
    subtotal: Decimal = Decimal(1)


def _numeric(
    check: str,
    expected: Decimal,
    actual: Decimal,
    *,
    tolerance: Decimal,
    severity: Severity,
    detail: str,
) -> Finding:
    return Finding(
        check=check,
        passed=abs(actual - expected) <= tolerance,
        severity=severity,
        detail=detail,
        expected=expected,
        actual=actual,
        tolerance=tolerance,
    )


def validate(
    source: ExtractedStatement,
    classified: tuple[ClassifiedLine, ...],
    reconstructed: Ifrs18Statement,
    *,
    tolerances: Tolerances | None = None,
) -> ValidationReport:
    """Run every reconciliation check over a reconstruction."""
    tolerances = tolerances or Tolerances()
    findings: list[Finding] = [
        _check_total_invariance(source, reconstructed),
        _check_every_line_has_a_category(classified),
        _check_no_unresolved_classifications(classified),
        _check_pbfit_is_permitted(reconstructed),
    ]

    findings.extend(_check_reported_subtotals(source, reconstructed, tolerances))
    bridge = _check_operating_bridge(source, classified, reconstructed)
    if bridge is not None:
        findings.append(bridge)

    return ValidationReport(findings=tuple(findings))


# ---------------------------------------------------------------------------
# Blocking checks
# ---------------------------------------------------------------------------


def _check_total_invariance(source: ExtractedStatement, reconstructed: Ifrs18Statement) -> Finding:
    """The backbone invariant: classification cannot change the total.

    Exact, and not configurable. IFRS 18 changes presentation, not measurement.
    """
    before = sum((line.amount for line in source.detail_lines), start=Decimal(0))
    after = reconstructed.total_of_all_lines

    return _numeric(
        "TOTAL_INVARIANCE",
        before,
        after,
        tolerance=Decimal(0),
        severity=Severity.BLOCKING,
        detail=(
            "The sum of all income and expenses must be identical before and "
            "after classification. IFRS 18 changes presentation, not "
            "measurement, so a difference here is a defect, not rounding."
        ),
    )


def _check_every_line_has_a_category(classified: tuple[ClassifiedLine, ...]) -> Finding:
    unclassified = [
        item.line.raw_label
        for item in classified
        if item.is_summable and item.category is Ifrs18Category.UNCLASSIFIED
    ]
    return Finding(
        check="CATEGORY_COMPLETENESS",
        passed=not unclassified,
        severity=Severity.BLOCKING,
        detail=(
            "Every line must carry an IFRS 18 category. UNCLASSIFIED is a "
            "technical state, never a category, and must not reach a finalized "
            f"statement. Outstanding: {', '.join(unclassified)}"
            if unclassified
            else "Every line carries an IFRS 18 category."
        ),
    )


def _check_no_unresolved_classifications(
    classified: tuple[ClassifiedLine, ...],
) -> Finding:
    blocked = [
        item.line.raw_label
        for item in classified
        if item.is_summable and not item.decision.is_resolved
    ]
    return Finding(
        check="NO_UNANSWERED_QUESTIONS",
        passed=not blocked,
        severity=Severity.BLOCKING,
        detail=(
            "A rule is waiting on a fact nobody has supplied, so these lines "
            "were never actually decided: " + ", ".join(blocked)
            if blocked
            else "No classification is waiting on an unanswered question."
        ),
    )


def _check_pbfit_is_permitted(reconstructed: Ifrs18Statement) -> Finding:
    """IFRS 18 paragraph 73 (open question Q3).

    Where the standard forbids the subtotal, the entity is outside the MVP's
    validated scope. Blocking is the honest outcome: producing a statement that
    presents a prohibited subtotal would be a defect on its face, and
    suppressing it silently would apply a rule set never validated for such
    entities.
    """
    subtotal = reconstructed.subtotal(SubtotalKey.PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES)
    prohibited = subtotal is not None and not subtotal.presented
    return Finding(
        check="OUT_OF_VALIDATED_SCOPE",
        passed=not prohibited,
        severity=Severity.BLOCKING,
        detail=(
            (subtotal.suppressed_reason or "")
            + " Entities with that main business activity are outside this "
            "tool's validated scope."
            if prohibited and subtotal
            else "Both required subtotals may be presented."
        ),
    )


# ---------------------------------------------------------------------------
# Checks against figures the source printed
# ---------------------------------------------------------------------------

#: The printed subtotals that have an IFRS 18 counterpart worth comparing.
_COMPARABLE: tuple[tuple[SubtotalKind, SubtotalKey, str], ...] = (
    (SubtotalKind.PROFIT_BEFORE_TAX, SubtotalKey.PROFIT_BEFORE_TAX, "PROFIT_BEFORE_TAX"),
    (
        SubtotalKind.PROFIT_FOR_THE_PERIOD,
        SubtotalKey.PROFIT_FOR_THE_PERIOD,
        "PROFIT_FOR_THE_PERIOD",
    ),
)


def _check_reported_subtotals(
    source: ExtractedStatement,
    reconstructed: Ifrs18Statement,
    tolerances: Tolerances,
) -> list[Finding]:
    """Profit before tax and profit for the period must survive unchanged.

    These are the user-visible proof that IFRS 18 moved items between
    categories without changing profit. A tolerance applies only because the
    source document may itself round.
    """
    findings: list[Finding] = []
    for kind, key, name in _COMPARABLE:
        printed = source.subtotal(kind)
        if printed is None:
            continue
        findings.append(
            _numeric(
                f"REPORTED_{name}",
                printed.amount,
                reconstructed.amount(key),
                tolerance=tolerances.profit_before_tax,
                severity=Severity.BLOCKING,
                detail=(
                    f"The IFRS 18 {name.lower().replace('_', ' ')} must equal the "
                    "figure the entity reported: reclassification moves items "
                    "between categories without changing profit."
                ),
            )
        )
    return findings


def _check_operating_bridge(
    source: ExtractedStatement,
    classified: tuple[ClassifiedLine, ...],
    reconstructed: Ifrs18Statement,
) -> Finding | None:
    """Reported operating profit plus every reclassification equals the new one.

    This is what makes the waterfall in the impact screen guaranteed to add up
    rather than merely plausible: the bridge is not drawn from the figures, the
    figures are checked against the bridge.
    """
    reported = reconstructed.reported_operating_profit
    if reported is None:
        return None

    movement = sum(
        (
            (item.amount if item.category is Ifrs18Category.OPERATING else Decimal(0))
            - operating_profit_before(source, item.line)
            for item in classified
            if item.is_summable
        ),
        start=Decimal(0),
    )

    return _numeric(
        "OPERATING_BRIDGE",
        reconstructed.amount(SubtotalKey.OPERATING_PROFIT),
        reported + movement,
        tolerance=Decimal(0),
        severity=Severity.BLOCKING,
        detail=(
            "Reported operating profit plus the effect of every "
            "reclassification must equal IFRS 18 operating profit, or the "
            "waterfall would not add up."
        ),
    )
