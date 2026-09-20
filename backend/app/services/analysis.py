"""Reconstruction, the reconciliation gate, and finalization (spec §19).

Everything here is built from the **stored** classifications, not from a fresh
engine run. That is the point: by the time a statement is reconstructed, some
of those decisions are a person's, and re-deriving them would quietly discard
the review the whole product exists to support.

The gate itself lives in `app.domain.validation` and is pure. This layer only
decides what happens to the project when it fails — which is: the failure is
recorded, the project is marked `RECONCILIATION_FAILED`, and nothing is
presented as a normal result.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.project import BlockingReason
from app.core.config import Settings
from app.domain.classification import ClassificationDecision
from app.domain.enums import (
    ActorType,
    AuditAction,
    ClassificationMethod,
    ConfidenceBand,
    Ifrs18Category,
    Ifrs18Subcategory,
    ProjectStatus,
    ReconciliationStatus,
)
from app.domain.extraction import ExtractedStatement
from app.domain.impact import ImpactAnalysis as ImpactReport
from app.domain.impact import analyse
from app.domain.statement import ClassifiedLine, Ifrs18Statement, reconstruct
from app.domain.validation import Tolerances, ValidationReport, validate
from app.models import FinancialStatementLine, Ifrs18Classification, ImpactAnalysis, Project
from app.repositories.classifications import ClassificationRepository
from app.repositories.statements import LineRepository, StatementRepository
from app.services import audit
from app.services.classification import entity_facts, line_key
from app.services.extraction import to_extracted_statement
from app.services.projects import progress_for


class AnalysisError(Exception):
    """The project is not in a state where an analysis means anything."""

    def __init__(self, reason: str, *, code: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


class NotReadyError(Exception):
    """Finalization is blocked by work only a person can complete."""

    def __init__(self, reasons: list[BlockingReason]) -> None:
        super().__init__("The project is not ready to be finalized.")
        self.reasons = reasons


@dataclass(frozen=True, slots=True)
class Analysis:
    """One complete reading of a project, computed together."""

    source: ExtractedStatement
    classified: tuple[ClassifiedLine, ...]
    reconstructed: Ifrs18Statement
    validation: ValidationReport
    impact: ImpactReport
    snapshot: ImpactAnalysis | None = None

    @property
    def reconciled(self) -> bool:
        return self.validation.passed

    @property
    def status(self) -> ReconciliationStatus:
        return ReconciliationStatus.PASSED if self.reconciled else ReconciliationStatus.FAILED


def tolerances_from(settings: Settings) -> Tolerances:
    return Tolerances(
        profit_before_tax=settings.reconciliation.profit_before_tax_tolerance,
        subtotal=settings.reconciliation.subtotal_tolerance,
    )


def to_classified_lines(
    source: ExtractedStatement,
    lines: list[FinancialStatementLine],
    rows: dict[uuid.UUID, Ifrs18Classification],
) -> tuple[ClassifiedLine, ...]:
    """Rebuild the domain view from what was decided and stored.

    The category used is the **final** one, so a human override is what the
    statement is built from. A line with no classification at all is carried as
    ``UNCLASSIFIED`` rather than dropped: dropping it would change the total
    and the gate would report an unexplained arithmetic failure instead of the
    real problem.
    """
    by_ordinal = {line.ordinal: line for line in lines}
    classified: list[ClassifiedLine] = []

    for line in source.lines:
        stored = by_ordinal.get(line.ordinal)
        if stored is None or line.is_subtotal:
            continue
        row = rows.get(stored.id)
        classified.append(
            ClassifiedLine(
                line=line,
                decision=_decision_of(row, line_id=line_key(line)),
                normalized_account_code=row.normalized_account_code if row else None,
            )
        )
    return tuple(classified)


def _decision_of(row: Ifrs18Classification | None, *, line_id: str) -> ClassificationDecision:
    if row is None:
        return ClassificationDecision(
            line_id=line_id,
            category=Ifrs18Category.UNCLASSIFIED,
            subcategory=None,
            method=ClassificationMethod.UNRESOLVED,
            rule_id=None,
            confidence=Decimal(0),
            confidence_band=ConfidenceBand.LOW,
            requires_human_review=True,
        )
    return ClassificationDecision(
        line_id=line_id,
        category=Ifrs18Category(row.final_ifrs18_category or Ifrs18Category.UNCLASSIFIED.value),
        subcategory=(
            Ifrs18Subcategory(row.final_ifrs18_subcategory)
            if row.final_ifrs18_subcategory
            else None
        ),
        method=ClassificationMethod(row.classification_method),
        rule_id=row.rule_id,
        confidence=row.ai_confidence if row.ai_confidence is not None else Decimal(0),
        confidence_band=(
            ConfidenceBand(row.confidence_band) if row.confidence_band else ConfidenceBand.LOW
        ),
        requires_human_review=row.requires_human_review,
        reasoning=row.ai_reasoning,
    )


async def build(session: AsyncSession, *, project: Project, settings: Settings) -> Analysis:
    """Reconstruct, validate and analyse, without writing anything."""
    statement = await StatementRepository(session).primary_for_project(project.id)
    if statement is None:
        raise AnalysisError("Extract a statement before analysing it.", code="nothing-extracted")

    lines = await LineRepository(session).for_statement(statement.id)
    rows = await ClassificationRepository(session).by_line(project.id)
    if not rows:
        raise AnalysisError(
            "Classify the statement before analysing it.", code="nothing-classified"
        )

    source = to_extracted_statement(statement, lines)
    classified = to_classified_lines(source, lines, rows)
    facts = await entity_facts(session, project, lines)

    reconstructed = reconstruct(source, classified, facts=facts)
    return Analysis(
        source=source,
        classified=classified,
        reconstructed=reconstructed,
        validation=validate(
            source, classified, reconstructed, tolerances=tolerances_from(settings)
        ),
        impact=analyse(source, classified, reconstructed),
    )


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------


async def finalize(
    session: AsyncSession,
    *,
    project: Project,
    settings: Settings,
    actor_id: uuid.UUID,
) -> Analysis:
    """Run the gate and record the outcome, whichever way it goes.

    A failed gate is stored, not discarded: the project reaches
    ``RECONCILIATION_FAILED`` and keeps the snapshot that explains why. The
    caller is responsible for refusing to present it as a normal result — which
    the API does by answering `409` with the failing checks.
    """
    progress = await progress_for(session, project)
    if progress.blocking_reasons:
        raise NotReadyError(list(progress.blocking_reasons))

    analysis = await build(session, project=project, settings=settings)
    snapshot = await _store_snapshot(session, project=project, analysis=analysis, settings=settings)
    analysis = Analysis(
        source=analysis.source,
        classified=analysis.classified,
        reconstructed=analysis.reconstructed,
        validation=analysis.validation,
        impact=analysis.impact,
        snapshot=snapshot,
    )

    project.reconciliation_status = analysis.status.value
    project.config_snapshot = _config_snapshot(settings)
    if analysis.reconciled:
        project.status = ProjectStatus.FINALIZED.value
        project.finalized_at = _now()
    else:
        # Deliberately not FINALIZED, and the database agrees: a project may
        # not be finalized unless reconciliation passed.
        project.status = ProjectStatus.RECONCILIATION_FAILED.value
        project.finalized_at = None
    await session.flush()

    await audit.record(
        session,
        action=AuditAction.FINALIZED,
        entity_type="projects",
        entity_id=project.id,
        project_id=project.id,
        actor_user_id=actor_id,
        actor_type=ActorType.USER,
        after={
            "reconciliation_status": project.reconciliation_status,
            "status": project.status,
            "impact_analysis_id": str(snapshot.id),
            "failed_checks": [finding.check for finding in analysis.validation.blocking_failures],
        },
    )
    return analysis


async def reopen(session: AsyncSession, *, project: Project, actor_id: uuid.UUID) -> Project:
    """Return a finalized project to review so it can be corrected.

    Finalization is not a one-way door — a figure read from the wrong cell has
    to be fixable — but it is not silent either: the previous snapshot stays,
    marked no longer current, and the reopening is audited.
    """
    before = {"status": project.status, "finalized_at": str(project.finalized_at)}
    project.status = ProjectStatus.IN_REVIEW.value
    project.reconciliation_status = ReconciliationStatus.NOT_RUN.value
    project.finalized_at = None
    await session.flush()

    await audit.record(
        session,
        action=AuditAction.UPDATED,
        entity_type="projects",
        entity_id=project.id,
        project_id=project.id,
        actor_user_id=actor_id,
        actor_type=ActorType.USER,
        before=before,
        after={"status": project.status, "reason": "reopened for correction"},
    )
    return project


async def current_snapshot(session: AsyncSession, project: Project) -> ImpactAnalysis | None:
    result = await session.execute(
        select(ImpactAnalysis)
        .where(ImpactAnalysis.project_id == project.id, ImpactAnalysis.is_current.is_(True))
        .order_by(ImpactAnalysis.computed_at.desc(), ImpactAnalysis.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# The stored snapshot
# ---------------------------------------------------------------------------


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _config_snapshot(settings: Settings) -> dict[str, Any]:
    """The thresholds and tolerances this run was decided under (spec §11).

    Pinned to the project so a later global change cannot retroactively alter
    how a finished analysis explains itself.
    """
    return {
        "high_confidence_min": str(settings.classification.high_confidence_min),
        "medium_confidence_min": str(settings.classification.medium_confidence_min),
        "profit_before_tax_tolerance": str(settings.reconciliation.profit_before_tax_tolerance),
        "subtotal_tolerance": str(settings.reconciliation.subtotal_tolerance),
    }


def _kpis_json(analysis: Analysis) -> dict[str, Any]:
    return {
        "items": [
            {
                "key": kpi.key,
                "unit": kpi.unit.value,
                "before": None if kpi.before is None else str(kpi.before),
                "after": str(kpi.after),
                "change": None if kpi.change is None else str(kpi.change),
                "change_pct": None if kpi.change_pct is None else str(kpi.change_pct),
            }
            for kpi in analysis.impact.kpis
        ],
        "comparable": analysis.impact.comparable,
    }


def _waterfall_json(analysis: Analysis) -> dict[str, Any]:
    return {
        "steps": [
            {
                "kind": step.kind.value,
                "key": step.key,
                "label_ko": step.label_ko,
                "label_en": step.label_en,
                "value": str(step.value),
                "to_category": step.to_category.value if step.to_category else None,
                "reported_placement": (
                    step.reported_placement.value if step.reported_placement else None
                ),
                "line_ids": list(step.line_ids),
            }
            for step in analysis.impact.waterfall
        ],
        "balances": analysis.impact.waterfall_balances,
    }


def _reconciliation_json(analysis: Analysis) -> dict[str, Any]:
    return {
        "checks": [
            {
                "check": finding.check,
                "passed": finding.passed,
                "severity": finding.severity.value,
                "detail": finding.detail,
                "expected": None if finding.expected is None else str(finding.expected),
                "actual": None if finding.actual is None else str(finding.actual),
                "delta": None if finding.delta is None else str(finding.delta),
                "tolerance": str(finding.tolerance),
            }
            for finding in analysis.validation.findings
        ]
    }


def _limitations_json(analysis: Analysis) -> dict[str, Any]:
    """Caveats specific to this run, surfaced rather than buried (Q7).

    The product-wide limitations live in `app.core.product`; these are the ones
    that depend on what is actually in this statement.
    """
    warnings = [finding.detail for finding in analysis.validation.warnings]
    undecomposed = [
        item.line.raw_label
        for item in analysis.classified
        if item.category is Ifrs18Category.UNCLASSIFIED
    ]
    return {"warnings": warnings, "unclassified_lines": undecomposed}


async def _store_snapshot(
    session: AsyncSession, *, project: Project, analysis: Analysis, settings: Settings
) -> ImpactAnalysis:
    """Store the computed analysis so an export stays reproducible.

    Previous snapshots are kept but marked no longer current: an export taken
    last week must still be explainable, even after a correction.
    """
    for existing in (
        (
            await session.execute(
                select(ImpactAnalysis).where(
                    ImpactAnalysis.project_id == project.id,
                    ImpactAnalysis.is_current.is_(True),
                )
            )
        )
        .scalars()
        .all()
    ):
        existing.is_current = False

    snapshot = ImpactAnalysis(
        project_id=project.id,
        computed_at=_now(),
        rule_set_version=project.rule_set_version,
        config_snapshot=_config_snapshot(settings),
        reconciliation_status=analysis.status.value,
        reconciliation_details=_reconciliation_json(analysis),
        kpis=_kpis_json(analysis),
        waterfall=_waterfall_json(analysis),
        limitations=_limitations_json(analysis),
        is_current=True,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot
