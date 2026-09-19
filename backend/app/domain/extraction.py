"""Extracted statements, reconciliation, and sign inference.

Pure domain logic: no I/O, no ORM, no file formats. An ingest adapter produces
an :class:`ExtractedStatement`; everything here operates on that alone, so the
same reconciliation applies whether the source was XLSX, CSV or PDF.

Spec §17 requires extracted data to be validated against the source *before*
any IFRS 18 work begins. That is what :func:`reconcile_extraction` does: it
re-derives each subtotal the document printed and compares. If we cannot
reproduce a statement's own arithmetic, we have misread it, and saying so is
the only honest outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from decimal import Decimal

from app.domain.enums import DecompositionStatus, SignNormalization, SubtotalKind

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceLocator:
    """Where a value came from (spec §18)."""

    source_file: str
    sheet: str | None = None
    row: int | None = None
    column: str | None = None
    cell: str | None = None
    page: int | None = None

    def as_dict(self) -> dict[str, object]:
        """The locator as JSON, omitting what does not apply to the format.

        Built from the dataclass fields rather than ``__dict__``: this class is
        ``slots=True`` and so has no instance dictionary at all.
        """
        return {
            name: value
            for name, value in ((f.name, getattr(self, f.name)) for f in fields(self))
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class ExtractedLine:
    ordinal: int
    raw_label: str
    raw_value: str
    amount: Decimal
    sign_normalization: SignNormalization
    locator: SourceLocator
    depth: int = 0
    is_subtotal: bool = False
    subtotal_kind: SubtotalKind | None = None
    is_nil: bool = False
    note_references: tuple[str, ...] = ()
    #: Set on an aggregate caption that has been broken into components (Q5).
    decomposition_status: DecompositionStatus = DecompositionStatus.NOT_REQUIRED

    @property
    def is_summable(self) -> bool:
        """Whether this line contributes to a total.

        Two exclusions, both to prevent double counting. A subtotal already
        aggregates the lines above it and is a reconciliation target, not an
        input. A decomposed caption is a container whose amount is carried by
        its children.
        """
        return (
            not self.is_subtotal and self.decomposition_status is not DecompositionStatus.DECOMPOSED
        )


@dataclass(frozen=True, slots=True)
class ExtractedStatement:
    source_file: str
    lines: tuple[ExtractedLine, ...]
    currency: str = "KRW"
    #: Power of ten the figures are stated in: 6 means 백만원.
    scale: int = 0
    sheet: str | None = None
    period_label: str | None = None

    @property
    def detail_lines(self) -> tuple[ExtractedLine, ...]:
        return tuple(line for line in self.lines if line.is_summable)

    @property
    def subtotal_lines(self) -> tuple[ExtractedLine, ...]:
        return tuple(line for line in self.lines if line.is_subtotal)

    def subtotal(self, kind: SubtotalKind) -> ExtractedLine | None:
        for line in self.lines:
            if line.is_subtotal and line.subtotal_kind is kind:
                return line
        return None


@dataclass(frozen=True, slots=True)
class ReconciliationCheck:
    """One comparison between what we computed and what the document printed."""

    check: str
    reported: Decimal
    computed: Decimal
    tolerance: Decimal
    locator: SourceLocator | None = None

    @property
    def delta(self) -> Decimal:
        return self.computed - self.reported

    @property
    def passed(self) -> bool:
        return abs(self.delta) <= self.tolerance


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    checks: tuple[ReconciliationCheck, ...] = ()
    #: Problems that prevent checking at all, e.g. no subtotals were found.
    blockers: tuple[str, ...] = field(default=())

    @property
    def passed(self) -> bool:
        return not self.blockers and all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[ReconciliationCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def reconcile_extraction(
    statement: ExtractedStatement,
    *,
    tolerance: Decimal = Decimal(0),
) -> ReconciliationReport:
    """Re-derive every printed subtotal and compare (spec §17).

    An income statement is a running total: each subtotal equals the previous
    subtotal plus the detail lines between them. That is the model used here.

    After each comparison the running total is reset to the **reported** value
    rather than the computed one. Without that reset a single misread line makes
    every later subtotal fail too, hiding where the defect actually is. Resetting
    localises the failure to the section that contains it.

    A final ``TOTAL`` check compares the sum of *all* detail lines against the
    last printed subtotal, which catches a line dropped entirely — something the
    sectional checks can miss when the dropped line sits after the last subtotal.
    """
    if not statement.lines:
        return ReconciliationReport(blockers=("statement has no lines",))

    subtotals = statement.subtotal_lines
    if not subtotals:
        return ReconciliationReport(
            blockers=("statement printed no subtotals, so extraction cannot be verified",)
        )

    checks: list[ReconciliationCheck] = []
    running = Decimal(0)

    for line in statement.lines:
        if line.is_subtotal:
            checks.append(
                ReconciliationCheck(
                    check=(line.subtotal_kind or SubtotalKind.OTHER).value,
                    reported=line.amount,
                    computed=running,
                    tolerance=tolerance,
                    locator=line.locator,
                )
            )
            # Reset to the reported figure so one bad line does not cascade.
            running = line.amount
        elif line.is_summable:
            running += line.amount
        # A decomposed caption is skipped: it is neither a subtotal nor an
        # input, because its amount is carried by the children that follow it.
        # Adding it here would double count them against the next subtotal.

    total_of_details = sum((line.amount for line in statement.detail_lines), start=Decimal(0))
    last_subtotal = subtotals[-1]
    checks.append(
        ReconciliationCheck(
            check="TOTAL_OF_ALL_DETAIL_LINES",
            reported=last_subtotal.amount,
            computed=total_of_details,
            tolerance=tolerance,
            locator=last_subtotal.locator,
        )
    )

    return ReconciliationReport(checks=tuple(checks))


# ---------------------------------------------------------------------------
# Sign inference
# ---------------------------------------------------------------------------

#: Label fragments that mark a deduction in a Korean income statement. Used
#: only by the single hypothesis below, and never on its own authority — the
#: result is accepted only if the statement's own subtotals then reconcile.
_EXPENSE_MARKERS: tuple[str, ...] = (
    "매출원가",
    "판매비와관리비",
    "판매비및관리비",
    "판매관리비",
    "영업외비용",
    "금융비용",
    "법인세비용",
    "기타비용",
    "비용",
    "원가",
    "손실",
    "상각비",
    "충당금전입",
)


def _looks_like_expense(label: str) -> bool:
    compact = label.replace(" ", "")
    return any(marker in compact for marker in _EXPENSE_MARKERS)


def infer_signs_from_subtotals(
    statement: ExtractedStatement,
    *,
    tolerance: Decimal = Decimal(0),
) -> tuple[ExtractedStatement, bool]:
    """Derive signs a statement never printed, and prove the result.

    Many Korean statements present every figure unsigned and expect the reader
    to know which captions are deductions. Reading those as printed makes the
    statement fail its own arithmetic.

    This tests exactly **one** hypothesis — "captions that read like deductions
    are negative" — and accepts it **only if the statement's own subtotals then
    reconcile**. It is not a search: no combination of flips is explored, so the
    function cannot manufacture a reconciliation that happens to add up.

    Returns the statement unchanged and ``False`` when the hypothesis does not
    apply or does not resolve the discrepancy. A caller must then report an
    extraction failure rather than proceed on a guess.
    """
    if reconcile_extraction(statement, tolerance=tolerance).passed:
        return statement, False

    # The hypothesis only makes sense when nothing was printed negative.
    if any(line.amount < 0 for line in statement.lines):
        return statement, False

    candidates = [line for line in statement.detail_lines if _looks_like_expense(line.raw_label)]
    if not candidates:
        return statement, False

    flipped_ordinals = {line.ordinal for line in candidates}
    adjusted = ExtractedStatement(
        source_file=statement.source_file,
        sheet=statement.sheet,
        currency=statement.currency,
        scale=statement.scale,
        period_label=statement.period_label,
        lines=tuple(
            replace(
                line,
                amount=-abs(line.amount),
                sign_normalization=SignNormalization.INFERRED_FROM_SUBTOTAL,
            )
            if line.ordinal in flipped_ordinals
            else line
            for line in statement.lines
        ),
    )

    if reconcile_extraction(adjusted, tolerance=tolerance).passed:
        return adjusted, True

    # The hypothesis did not explain the statement. Report the original, so the
    # failure the user sees is the real one and not one we introduced.
    return statement, False
