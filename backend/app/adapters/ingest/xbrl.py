"""Reading a DART XBRL filing (spec §3).

Every other reader in this package spends most of its effort *guessing*: which
column holds the figures, which row is a subtotal, whether an unsigned number
is a deduction, what unit the page is in. Each of those guesses has been a
defect at least once.

An XBRL instance states all of it. So this adapter does not guess:

| What the other readers infer | What the filing declares |
|---|---|
| which caption is a subtotal | the concept, via the taxonomy |
| whether a figure is a deduction | the concept's nature |
| which period a column is | the context's period |
| consolidated or separate | the context's dimension |
| what the caption means | the concept id, in any language |

The cost of that is narrowness: a concept this product does not know is
reported, not silently dropped, because a filing whose figures we only partly
read is exactly the situation the extraction reconciliation exists to catch.
"""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.adapters.ingest.grid import ExtractOptions, StatementNotFoundError
from app.data.xbrl_catalog import ConceptCatalog, ConceptDefinition, load_concepts
from app.domain.enums import SignNormalization
from app.domain.extraction import ExtractedLine, ExtractedStatement, SourceLocator

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"

#: The dimension that separates consolidated from separate statements. Every
#: DART filing carries both, so choosing between them is not optional.
BASIS_AXIS = "ConsolidatedAndSeparateFinancialStatementsAxis"
CONSOLIDATED_MEMBER = "ConsolidatedMember"
SEPARATE_MEMBER = "SeparateMember"

#: Roughly how large an instance may be before we refuse to parse it. An XBRL
#: document is XML, and XML parsers are a classic denial-of-service surface
#: (spec §31); the upload layer caps the byte size, and this caps the element
#: count so a small file cannot expand into an enormous tree.
MAX_ELEMENTS = 2_000_000


@dataclass(frozen=True, slots=True)
class Context:
    """One XBRL context: who, when, and under which dimensions."""

    id: str
    start: dt.date | None
    end: dt.date | None
    instant: dt.date | None
    dimensions: dict[str, str]

    @property
    def is_duration(self) -> bool:
        return self.start is not None and self.end is not None

    @property
    def basis(self) -> str | None:
        return self.dimensions.get(BASIS_AXIS)

    @property
    def is_plain(self) -> bool:
        """No dimension beyond consolidated-or-separate.

        A filing tags the same concept many times — by segment, by product, by
        measurement basis. Only the undimensioned fact is the figure that
        appears on the face of the statement.
        """
        return set(self.dimensions) <= {BASIS_AXIS}


def _local(qname: str) -> str:
    return qname.rsplit(":", 1)[-1] if ":" in qname else qname


def _date(text: str | None) -> dt.date | None:
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text.strip())
    except ValueError:
        return None


def _read_contexts(root: ET.Element) -> dict[str, Context]:
    contexts: dict[str, Context] = {}
    for element in root.findall(f"{XBRLI}context"):
        identifier = element.get("id")
        if not identifier:
            continue
        period = element.find(f"{XBRLI}period")
        segment = element.find(f"{XBRLI}entity/{XBRLI}segment")
        dimensions: dict[str, str] = {}
        if segment is not None:
            for member in segment.findall(f"{XBRLDI}explicitMember"):
                axis = member.get("dimension")
                if axis:
                    dimensions[_local(axis)] = _local((member.text or "").strip())
        contexts[identifier] = Context(
            id=identifier,
            start=_date(period.findtext(f"{XBRLI}startDate") if period is not None else None),
            end=_date(period.findtext(f"{XBRLI}endDate") if period is not None else None),
            instant=_date(period.findtext(f"{XBRLI}instant") if period is not None else None),
            dimensions=dimensions,
        )
    return contexts


def _wanted_basis(options: ExtractOptions) -> str:
    return SEPARATE_MEMBER if options.basis == "SEPARATE" else CONSOLIDATED_MEMBER


def choose_context(contexts: dict[str, Context], options: ExtractOptions) -> Context:
    """Pick the one context the statement is being read for.

    A filing carries the current half-year, the current quarter, the prior
    half-year, the prior quarter, each on both a consolidated and a separate
    basis — and that is before any segment breakdown. Reading "the first one"
    would silently produce a different statement from the one the user asked
    for, so the project's own basis and reporting period decide it, and a
    period that is not in the filing is an error rather than a fallback.
    """
    basis = _wanted_basis(options)
    candidates = [
        context
        for context in contexts.values()
        if context.is_duration and context.is_plain and context.basis == basis
    ]
    if not candidates:
        raise StatementNotFoundError(
            f"The filing has no undimensioned {basis} figures for a reporting period. "
            "It may be a document this tool does not read, or the basis may not be filed."
        )

    if options.period_start and options.period_end:
        exact = [
            context
            for context in candidates
            if context.start == options.period_start and context.end == options.period_end
        ]
        if exact:
            return exact[0]
        available = ", ".join(sorted(f"{c.start} to {c.end}" for c in candidates))
        raise StatementNotFoundError(
            f"The filing has no {basis} figures for "
            f"{options.period_start} to {options.period_end}. It reports: {available}. "
            "Set the project's period to one the filing covers."
        )

    # No period asked for: the longest span ending latest, which is the
    # cumulative figure a statement presents rather than a single quarter.
    return max(
        candidates,
        key=lambda c: ((c.end or dt.date.min), (c.end or dt.date.min) - (c.start or dt.date.min)),
    )


def _amount(text: str | None) -> Decimal | None:
    if text is None or not text.strip():
        return None
    try:
        return Decimal(text.strip())
    except InvalidOperation:
        return None


@dataclass(frozen=True, slots=True)
class Fact:
    definition: ConceptDefinition
    reported: Decimal
    context_id: str


def _same_period_contexts(contexts: dict[str, Context], chosen: Context) -> set[str]:
    """Every context covering the same period and basis as the one chosen.

    A filing tags the note that breaks a caption down under the same period
    and the same basis as the caption itself, adding one dimension that says
    "this is the note's own table". Those contexts, and only those, hold
    figures that belong to the statement being read.
    """
    return {
        context.id
        for context in contexts.values()
        if context.start == chosen.start
        and context.end == chosen.end
        and context.basis == chosen.basis
    }


def _collect(root: ET.Element, chosen: Context, siblings: set[str]) -> dict[str, Fact]:
    """The facts of this statement: its captions, and the notes explaining them.

    A caption is taken only from the undimensioned context, because that is
    the figure on the face of the statement — a segment's revenue is tagged
    with the same concept and must not be mistaken for the company's. A note
    component is by definition not on the face, so it is taken from any
    context of the same period and basis; where two such contexts disagree
    about it the component is dropped rather than picked between, and its
    caption then simply fails to decompose.
    """
    catalog = load_concepts()
    statement: dict[str, Fact] = {}
    components: dict[str, Fact | None] = {}
    seen = 0
    for element in root.iter():
        seen += 1
        if seen > MAX_ELEMENTS:
            raise StatementNotFoundError(
                "The filing is larger than this tool will parse. "
                "Upload the statement as XLSX or CSV instead."
            )
        context_id = element.get("contextRef")
        if context_id is None or context_id not in siblings:
            continue
        qname = _qname(element.tag, root)
        if qname is None:
            continue
        definition = catalog.get(qname)
        if definition is None:
            continue
        value = _amount(element.text)
        if value is None:
            continue
        if definition.parent is None:
            if context_id != chosen.id:
                continue
            # A concept reported twice in one context is the same figure; the
            # first is kept so the result does not depend on document order.
            statement.setdefault(qname, Fact(definition, value, context_id))
            continue
        previous = components.get(qname, ...)
        if previous is ...:
            components[qname] = Fact(definition, value, context_id)
        elif previous is not None and previous.reported != value:
            components[qname] = None

    facts = dict(statement)
    facts.update({qname: fact for qname, fact in components.items() if fact is not None})
    return facts


def _signed(fact: Fact) -> Decimal:
    """The figure as a profit effect: income positive, expense negative."""
    return -abs(fact.reported) if fact.definition.is_deduction else fact.reported


def _decompose(facts: dict[str, Fact]) -> set[str]:
    """Which captions may be replaced by the note components that explain them.

    Only where the components add up to the caption exactly. A breakdown that
    does not reconcile is not one we have understood, and these are precisely
    the lines IFRS 18 turns on — 기타수익 and 금융수익 are where ¶49-50, B65 and
    B72 do their work — so an approximate split would be worse than none.
    """
    catalog = load_concepts()
    replaced: set[str] = set()
    for qname, fact in facts.items():
        if fact.definition.parent is not None:
            continue
        parts = [
            facts[item.concept] for item in catalog.components_of(qname) if item.concept in facts
        ]
        if parts and sum((_signed(part) for part in parts), Decimal(0)) == _signed(fact):
            replaced.add(qname)
    return replaced


def _qname(tag: str, root: ET.Element) -> str | None:
    """Turn `{namespace}Local` back into the `prefix:Local` the taxonomy uses."""
    if not tag.startswith("{"):
        return None
    namespace, _, local = tag[1:].partition("}")
    prefix = _PREFIXES.get(namespace)
    return f"{prefix}:{local}" if prefix else None


#: The two taxonomies whose concepts this product maps. Matched by namespace
#: rather than by the prefix a filing happens to declare, because a prefix is
#: the filer's choice and the namespace is the taxonomy's identity.
_PREFIXES = {
    "https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full": "ifrs-full",
    "https://xbrl.ifrs.org/taxonomy/2023-03-23/ifrs-full": "ifrs-full",
    "http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full": "ifrs-full",
    "http://dart.fss.or.kr/taxonomy/2026-01-31/ifrs/dart": "dart",
    "http://dart.fss.or.kr/taxonomy/2025-01-31/ifrs/dart": "dart",
    "http://dart.fss.or.kr/taxonomy/2024-01-31/ifrs/dart": "dart",
}


def read_income_statement(
    path: Path, *, options: ExtractOptions | None = None
) -> ExtractedStatement:
    """Read the income statement out of an XBRL instance."""
    options = options or ExtractOptions()
    try:
        tree = ET.parse(path)  # noqa: S314 - see MAX_ELEMENTS and the upload size cap
    except ET.ParseError as exc:
        raise StatementNotFoundError(f"This is not a readable XBRL document: {exc}") from exc

    root = tree.getroot()
    contexts = _read_contexts(root)
    if not contexts:
        raise StatementNotFoundError(
            "The file has no XBRL contexts. It may be a taxonomy schema or a "
            "linkbase rather than the filing's instance document — upload the "
            "one ending in .xbrl."
        )

    context = choose_context(contexts, options)
    facts = _collect(root, context, _same_period_contexts(contexts, context))
    if not facts:
        raise StatementNotFoundError(
            "No income statement figures were found in the filing for that period and basis."
        )

    catalog = load_concepts()
    # Where the notes break a caption down and the parts reconcile to it, the
    # parts are kept and the caption dropped — keeping both would count it
    # twice. This is what IFRS 18 needs: 기타수익 and 금융수익 are exactly the
    # captions the standard exists to look inside, and the filing already
    # carries the breakdown.
    decomposed = _decompose(facts)
    components_used = {
        item.concept for parent in decomposed for item in catalog.components_of(parent)
    }

    chosen = [
        fact
        for qname, fact in facts.items()
        if qname not in decomposed
        # A note component whose parent did not reconcile is left out: the
        # caption stands, and its components would double-count it.
        and (fact.definition.parent is None or qname in components_used)
    ]
    chosen.sort(key=lambda fact: _position(catalog, fact.definition))

    lines: list[ExtractedLine] = []
    for ordinal, fact in enumerate(chosen):
        deduction = fact.definition.is_deduction
        lines.append(
            ExtractedLine(
                ordinal=ordinal,
                raw_label=fact.definition.label_ko,
                raw_value=str(fact.reported),
                amount=-abs(fact.reported) if deduction else fact.reported,
                sign_normalization=(
                    SignNormalization.TAXONOMY_SIGNED if deduction else SignNormalization.AS_IS
                ),
                locator=SourceLocator(
                    source_file=path.name,
                    concept=fact.definition.concept,
                    context=fact.context_id,
                ),
                depth=1 if fact.definition.parent else 0,
                is_subtotal=fact.definition.is_subtotal,
                subtotal_kind=fact.definition.subtotal,
                note_references=(fact.definition.parent,) if fact.definition.parent else (),
            )
        )

    return ExtractedStatement(
        source_file=path.name,
        lines=tuple(lines),
        currency=_currency(root) or "KRW",
        # XBRL figures are stated in the currency's own units. There is no
        # presentation scale to detect and none to guess at.
        scale=0,
        sheet=f"{context.basis} {context.start} ~ {context.end}",
        period_label=f"{context.start} ~ {context.end}",
    )


def _position(catalog: ConceptCatalog, definition: ConceptDefinition) -> tuple[int, int]:
    """Where a line prints on the statement.

    A note component stands *in place of* the caption it replaces, not after
    it, so it sorts at its parent's position — otherwise 이자수익 would print
    below 당기순이익 and every subtotal above it would re-sum without it.
    """
    if definition.parent is None:
        return (definition.order, 0)
    parent = catalog.get(definition.parent)
    return (parent.order if parent else definition.order, definition.order)


def _currency(root: ET.Element) -> str | None:
    for unit in root.findall(f"{XBRLI}unit"):
        measure = unit.findtext(f"{XBRLI}measure")
        if measure and "iso4217" in measure:
            return _local(measure).upper()
    return None
