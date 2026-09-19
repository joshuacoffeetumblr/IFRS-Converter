"""Builders for reconstruction and validation tests."""

from __future__ import annotations

from decimal import Decimal

from app.domain.classification import ClassificationDecision
from app.domain.enums import (
    ClassificationMethod,
    ConfidenceBand,
    DecompositionStatus,
    Ifrs18Category,
    SignNormalization,
    SubtotalKind,
)
from app.domain.extraction import ExtractedLine, ExtractedStatement, SourceLocator
from app.domain.statement import ClassifiedLine

LOCATOR = SourceLocator(source_file="fs.xlsx", sheet="손익계산서")


def line(
    ordinal: int,
    label: str,
    amount: str,
    *,
    subtotal: SubtotalKind | None = None,
    decomposed: bool = False,
) -> ExtractedLine:
    return ExtractedLine(
        ordinal=ordinal,
        raw_label=label,
        raw_value=amount,
        amount=Decimal(amount),
        sign_normalization=SignNormalization.AS_IS,
        locator=LOCATOR,
        is_subtotal=subtotal is not None,
        subtotal_kind=subtotal,
        decomposition_status=(
            DecompositionStatus.DECOMPOSED if decomposed else DecompositionStatus.NOT_REQUIRED
        ),
    )


def decide(
    label: str,
    category: Ifrs18Category,
    *,
    resolved: bool = True,
) -> ClassificationDecision:
    return ClassificationDecision(
        line_id=label,
        category=category,
        subcategory=None,
        method=ClassificationMethod.RULE if resolved else ClassificationMethod.UNRESOLVED,
        rule_id="TEST-001" if resolved else None,
        confidence=Decimal(1) if resolved else Decimal(0),
        confidence_band=ConfidenceBand.HIGH if resolved else ConfidenceBand.LOW,
        requires_human_review=not resolved,
    )


def classified(
    extracted: ExtractedLine,
    category: Ifrs18Category,
    *,
    resolved: bool = True,
) -> ClassifiedLine:
    return ClassifiedLine(
        line=extracted,
        decision=decide(extracted.raw_label, category, resolved=resolved),
    )


def statement(*lines: ExtractedLine) -> ExtractedStatement:
    return ExtractedStatement(source_file="fs.xlsx", lines=lines, currency="KRW", scale=6)
