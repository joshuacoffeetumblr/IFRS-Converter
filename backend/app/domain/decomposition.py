"""Breaking an aggregate caption into its components (open question Q5).

Korean statements routinely present only an aggregate — 영업외수익, 금융수익 —
with the detail relegated to a note. That matters more than it looks:

* IFRS 18 makes **operating** the residual category, so much of what sits inside
  a 영업외수익 bucket becomes operating once visible.
* IFRS 18 **B65** sends a foreign exchange difference to the category of the
  item that produced it, and FX on a trade receivable is operating.

So an undecomposed caption can conceal a genuine change in operating profit —
the number this product exists to explain. Decomposition is offered rather than
forced: a user who cannot decompose may accept the aggregate, and the resulting
limitation is recorded and surfaced rather than buried.

The one hard rule is arithmetic: components must sum to the caption they came
from. Otherwise the statement silently stops adding up.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from app.domain.enums import DecompositionStatus
from app.domain.extraction import ExtractedLine, SourceLocator


class DecompositionError(ValueError):
    """The proposed components cannot replace the caption."""


@dataclass(frozen=True, slots=True)
class Component:
    """One item the user read out of a note."""

    label: str
    #: Signed profit-or-loss effect, same convention as everywhere else.
    amount: Decimal
    note_reference: str | None = None


@dataclass(frozen=True, slots=True)
class DecompositionCheck:
    parent_label: str
    parent_amount: Decimal
    components_total: Decimal
    component_count: int

    @property
    def delta(self) -> Decimal:
        return self.components_total - self.parent_amount

    @property
    def balances(self) -> bool:
        """Exact, with no tolerance.

        A tolerance here would be a different thing from the rounding tolerance
        applied to a *printed* subtotal: these figures are being entered now,
        against a caption already extracted, so any difference is a data-entry
        error rather than a presentation artefact.
        """
        return self.delta == 0


def check_decomposition(
    parent: ExtractedLine,
    components: tuple[Component, ...],
) -> DecompositionCheck:
    total = sum((component.amount for component in components), start=Decimal(0))
    return DecompositionCheck(
        parent_label=parent.raw_label,
        parent_amount=parent.amount,
        components_total=total,
        component_count=len(components),
    )


def decompose(
    parent: ExtractedLine,
    components: tuple[Component, ...],
    *,
    next_ordinal: int,
) -> tuple[ExtractedLine, tuple[ExtractedLine, ...]]:
    """Replace a caption with its components.

    Returns the parent marked ``DECOMPOSED`` — which excludes it from every
    category sum, exactly as a subtotal is excluded — together with the child
    lines that now carry its amount.

    Each child inherits the parent's locator and records the note it came from,
    so provenance still reaches a real place in the source document.
    """
    if parent.is_subtotal:
        raise DecompositionError(
            f"{parent.raw_label!r} is a subtotal; subtotals aggregate other lines "
            "and are never decomposed"
        )
    if not components:
        raise DecompositionError("decomposition requires at least one component")

    check = check_decomposition(parent, components)
    if not check.balances:
        raise DecompositionError(
            f"components of {parent.raw_label!r} total {check.components_total} "
            f"but the caption reports {parent.amount} (difference {check.delta})"
        )

    children = tuple(
        ExtractedLine(
            ordinal=next_ordinal + index,
            raw_label=component.label,
            raw_value=str(component.amount),
            amount=component.amount,
            sign_normalization=parent.sign_normalization,
            depth=parent.depth + 1,
            locator=_child_locator(parent.locator, component),
            note_references=(
                (component.note_reference,) if component.note_reference else parent.note_references
            ),
        )
        for index, component in enumerate(components)
    )

    decomposed_parent = replace(parent, decomposition_status=DecompositionStatus.DECOMPOSED)

    return decomposed_parent, children


def _child_locator(parent: SourceLocator, component: Component) -> SourceLocator:
    """A component's provenance is the note, anchored to the caption's cell."""
    return SourceLocator(
        source_file=parent.source_file,
        sheet=parent.sheet,
        row=parent.row,
        column=parent.column,
        cell=parent.cell,
        page=parent.page,
    )


def status_after(components_accepted: bool, decomposed: bool) -> DecompositionStatus:
    """The state a caption ends in after the user has dealt with it."""
    if decomposed:
        return DecompositionStatus.DECOMPOSED
    if components_accepted:
        return DecompositionStatus.ACCEPTED_AGGREGATE
    return DecompositionStatus.REQUIRED
