"""Wiring the domain stages together.

The stages themselves are pure and independent; this is the one place that
knows the order they run in. Keeping it here rather than in the CLI or a router
means the API, the exporter and a demo run all drive exactly the same sequence,
so none of them can quietly diverge.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.data.catalog import get_dictionary
from app.data.rule_catalog import get_engine
from app.domain.classification import ClassificationDecision
from app.domain.extraction import ExtractedLine, ExtractedStatement
from app.domain.impact import ImpactAnalysis, analyse
from app.domain.normalization import NormalizationReport, normalize_statement
from app.domain.rules import ClassifiableItem, EntityFacts
from app.domain.statement import ClassifiedLine, Ifrs18Statement, reconstruct
from app.domain.validation import Tolerances, ValidationReport, validate


@dataclass(frozen=True, slots=True)
class PipelineResult:
    source: ExtractedStatement
    classified: tuple[ClassifiedLine, ...]
    reconstructed: Ifrs18Statement
    validation: ValidationReport
    impact: ImpactAnalysis

    @property
    def reconciled(self) -> bool:
        return self.validation.passed


def line_label(line: ExtractedLine) -> str:
    """The default identity of a line: its caption.

    Readable, and sufficient for a one-shot run over a statement in memory.
    A caller that stores its lines has a better answer — a caption can repeat
    within one statement — and passes ``line_id_of`` instead.
    """
    return line.raw_label


def classify_statement(
    source: ExtractedStatement,
    facts: EntityFacts,
    *,
    use_advisor: bool = True,
    line_id_of: Callable[[ExtractedLine], str] = line_label,
) -> tuple[ClassifiedLine, ...]:
    """Normalize every line, then classify it.

    ``line_id_of`` decides what a decision's ``line_id`` is, and with it how a
    per-line fact (B65, B72) is keyed. The identity has to be the caller's
    choice because only the caller knows whether these lines are stored rows
    with ids of their own.
    """
    report = normalize_statement(source, get_dictionary())
    return classify_normalized(report, facts, use_advisor=use_advisor, line_id_of=line_id_of)


def classify_normalized(
    report: NormalizationReport,
    facts: EntityFacts,
    *,
    use_advisor: bool = True,
    line_id_of: Callable[[ExtractedLine], str] = line_label,
) -> tuple[ClassifiedLine, ...]:
    """Classify lines that have already been mapped to canonical accounts.

    Separate from ``classify_statement`` because a caller that stores its
    statements has something the dictionary does not: the mappings a human
    corrected by hand. Such a caller normalizes, substitutes those mappings
    into the report, and classifies that — which is the only way a manual
    correction can actually change a classification.

    Subtotals are passed over: they aggregate the lines around them and are
    reconciliation targets, not classifiable facts.
    """
    dictionary = get_dictionary()
    engine = get_engine()

    results: list[ClassifiedLine] = []
    for normalized in report.lines:
        if normalized.line.is_subtotal:
            continue

        definition = dictionary.get(normalized.code) if normalized.code else None
        ancestors: tuple[str, ...] = ()
        if definition:
            ancestors = (definition.code,)
            if definition.parent_code:
                ancestors += (definition.parent_code,)

        decision: ClassificationDecision = engine.classify(
            ClassifiableItem(
                line_id=line_id_of(normalized.line),
                raw_label=normalized.line.raw_label,
                amount=normalized.line.amount,
                normalized_account_code=normalized.code,
                account_ancestors=ancestors,
                is_aggregate=normalized.needs_decomposition,
                note_references=normalized.line.note_references,
            ),
            facts,
            use_advisor=use_advisor,
        )
        results.append(
            ClassifiedLine(
                line=normalized.line,
                decision=decision,
                normalized_account_code=normalized.code,
            )
        )
    return tuple(results)


def run(
    source: ExtractedStatement,
    facts: EntityFacts,
    *,
    tolerances: Tolerances | None = None,
    use_advisor: bool = True,
    line_id_of: Callable[[ExtractedLine], str] = line_label,
) -> PipelineResult:
    """Classify, reconstruct, validate and analyse in that order."""
    classified = classify_statement(source, facts, use_advisor=use_advisor, line_id_of=line_id_of)
    reconstructed = reconstruct(source, classified, facts=facts)
    return PipelineResult(
        source=source,
        classified=classified,
        reconstructed=reconstructed,
        validation=validate(source, classified, reconstructed, tolerances=tolerances),
        impact=analyse(source, classified, reconstructed),
    )
