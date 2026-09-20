"""Finalization, the IFRS 18 statement, impact and export (API spec §2).

Spec §19 governs this whole file: **a result that did not reconcile is never
presented as a normal one**. `POST /finalize` answers `409` with the failing
checks, `GET /statement` and `GET /impact` carry the reconciliation block at
the top of the body rather than somewhere a client might not look, and an
export of an unreconciled analysis has to be asked for explicitly and comes
back watermarked.
"""

from __future__ import annotations

import io
import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.export.xlsx import ExportPackage, build_workbook
from app.api.deps import CurrentUser, SessionDep, SettingsDep, parse_uuid
from app.api.errors import ApiProblemError, ConflictError, NotFoundError, UnprocessableStateError
from app.api.schemas.analysis import (
    CheckResponse,
    ExportResponse,
    FinalizeResponse,
    ImpactResponse,
    KpiResponse,
    ReclassificationResponse,
    ReconciliationResponse,
    SectionResponse,
    StatementLineResponse,
    StatementResponse,
    SubtotalResponse,
    WaterfallStepResponse,
)
from app.core.product import DISCLAIMER_KO, LIMITATIONS_EN
from app.data.catalog import catalog_version
from app.data.rule_catalog import rule_set_version
from app.domain.enums import ActorType, AuditAction, ExportFormat, ExportStatus, ProjectStatus
from app.domain.statement import ClassifiedLine
from app.models import Export, FinancialStatementLine, Ifrs18Classification, Project
from app.repositories.classifications import ClassificationRepository
from app.repositories.projects import ProjectRepository
from app.repositories.statements import LineRepository, StatementRepository
from app.services import audit
from app.services.analysis import (
    Analysis,
    AnalysisError,
    NotReadyError,
    build,
    current_snapshot,
    finalize,
    reopen,
)

router = APIRouter(tags=["analysis"])

#: The export filename a browser will save. Korean statements are routinely
#: shared by email, so the name has to say what the file is on its own.
EXPORT_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _project(session: SessionDep, project_id: str, user: CurrentUser) -> Project:
    project = await ProjectRepository(session).by_id(parse_uuid(project_id, "Project"), user.id)
    if project is None:
        raise NotFoundError("Project")
    return project


async def _analysis(session: AsyncSession, project: Project, settings: SettingsDep) -> Analysis:
    try:
        return await build(session, project=project, settings=settings)
    except AnalysisError as exc:
        raise UnprocessableStateError(
            title="Nothing to analyse", code=exc.code, detail=exc.reason
        ) from exc


def _reconciliation(analysis: Analysis) -> ReconciliationResponse:
    return ReconciliationResponse(
        status=analysis.status,
        passed=analysis.reconciled,
        checks=[
            CheckResponse(
                check=finding.check,
                passed=finding.passed,
                severity=finding.severity,
                detail=finding.detail,
                expected=finding.expected,
                actual=finding.actual,
                delta=finding.delta,
                tolerance=finding.tolerance,
            )
            for finding in analysis.validation.findings
        ],
        blocking_failures=[f.check for f in analysis.validation.blocking_failures],
        warnings=[f.check for f in analysis.validation.warnings],
    )


def _limitations(analysis: Analysis) -> list[str]:
    """What this run cannot claim, said in product (Q7)."""
    return [*LIMITATIONS_EN, *(f.detail for f in analysis.validation.warnings)]


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/finalize", response_model=FinalizeResponse)
async def finalize_project(
    project_id: str, session: SessionDep, user: CurrentUser, settings: SettingsDep
) -> FinalizeResponse:
    """Run reconstruction and every validation, and record the outcome.

    Three answers, and only one of them is a finalized project:

    * `422` — blocked by work only a person can do: an unanswered question, an
      unreviewed classification. The blocking reasons are listed.
    * `409` — the reconciliation failed. The failing checks travel with the
      error, and the project is left at `RECONCILIATION_FAILED` with the
      snapshot that explains why.
    * `200` — reconciled and finalized.
    """
    project = await _project(session, project_id, user)
    if project.status == ProjectStatus.FINALIZED:
        raise ConflictError(
            title="Project is already finalized",
            code="project-finalized",
            detail="Reopen the project before finalizing it again.",
        )

    try:
        analysis = await finalize(session, project=project, settings=settings, actor_id=user.id)
    except NotReadyError as exc:
        raise UnprocessableStateError(
            title="The project is not ready to be finalized",
            code="not-ready",
            detail="Some work can only be completed by a person.",
            blocking_reasons=[reason.model_dump() for reason in exc.reasons],
        ) from exc
    except AnalysisError as exc:
        raise UnprocessableStateError(
            title="Nothing to finalize", code=exc.code, detail=exc.reason
        ) from exc

    assert analysis.snapshot is not None  # finalize always stores one
    if not analysis.reconciled:
        # The failure is part of the record, and the record must outlive the
        # error response — the session dependency rolls a failed request back.
        await session.commit()
        raise ApiProblemError(
            status_code=status.HTTP_409_CONFLICT,
            title="IFRS 18 reconstruction could not be reconciled.",
            code="reconciliation-failed",
            detail="The statement was not finalized. Every check is listed below.",
            extra={
                "impact_analysis_id": str(analysis.snapshot.id),
                "checks": _reconciliation(analysis).model_dump(mode="json")["checks"],
            },
        )

    return FinalizeResponse(
        status=project.status,
        reconciliation=_reconciliation(analysis),
        impact_analysis_id=analysis.snapshot.id,
        finalized_at=project.finalized_at,
    )


@router.post("/projects/{project_id}/reopen", response_model=FinalizeResponse)
async def reopen_project(
    project_id: str, session: SessionDep, user: CurrentUser, settings: SettingsDep
) -> FinalizeResponse:
    """Return a finalized project to review so it can be corrected.

    Not a silent undo: the previous snapshot stays, marked no longer current,
    and the reopening is audited.
    """
    project = await _project(session, project_id, user)
    if project.status != ProjectStatus.FINALIZED:
        raise ConflictError(
            title="Project is not finalized",
            code="project-not-finalized",
            detail="There is nothing to reopen.",
        )

    await reopen(session, project=project, actor_id=user.id)
    snapshot = await current_snapshot(session, project)
    analysis = await _analysis(session, project, settings)
    return FinalizeResponse(
        status=project.status,
        reconciliation=_reconciliation(analysis),
        impact_analysis_id=snapshot.id if snapshot else analysis.snapshot.id,  # type: ignore[union-attr]
        finalized_at=project.finalized_at,
    )


# ---------------------------------------------------------------------------
# The statement
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/statement", response_model=StatementResponse)
async def get_statement(
    project_id: str, session: SessionDep, user: CurrentUser, settings: SettingsDep
) -> StatementResponse:
    """The reconstructed IFRS 18 statement of profit or loss.

    The reconciliation block comes first in the body, not as a footnote: a
    client must not be able to render these figures without knowing whether
    they reconcile (spec §19).
    """
    project = await _project(session, project_id, user)
    analysis = await _analysis(session, project, settings)
    ids = await _classification_ids(session, project, analysis)

    return StatementResponse(
        reconciliation=_reconciliation(analysis),
        currency=analysis.reconstructed.currency,
        scale=analysis.reconstructed.scale,
        sections=[
            SectionResponse(
                category=section.category,
                label_en=section.label_en,
                label_ko=section.label_ko,
                total=section.total,
                lines=[
                    StatementLineResponse(
                        line_id=ids.line_id(item),
                        classification_id=ids.classification_id(item),
                        label_ko=item.line.raw_label,
                        amount=item.amount,
                        normalized_account_code=item.normalized_account_code,
                        rule_id=item.decision.rule_id,
                        user_override=ids.overridden(item),
                    )
                    for item in section.lines
                ],
            )
            for section in analysis.reconstructed.sections
        ],
        subtotals=[
            SubtotalResponse(
                key=subtotal.key,
                label_en=subtotal.label_en,
                label_ko=subtotal.label_ko,
                amount=subtotal.amount,
                presented=subtotal.presented,
                suppressed_reason=subtotal.suppressed_reason,
            )
            for subtotal in analysis.reconstructed.subtotals
        ],
        disclaimer=DISCLAIMER_KO,
        limitations=_limitations(analysis),
    )


# ---------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/impact", response_model=ImpactResponse)
async def get_impact(
    project_id: str, session: SessionDep, user: CurrentUser, settings: SettingsDep
) -> ImpactResponse:
    """The impact screen of spec §22, computed server-side in full.

    KPIs whose change must be zero are returned explicitly showing zero. That
    is the point: it is the user-visible proof that IFRS 18 changed
    presentation without changing profit.
    """
    project = await _project(session, project_id, user)
    analysis = await _analysis(session, project, settings)
    ids = await _classification_ids(session, project, analysis)
    snapshot = await current_snapshot(session, project)

    return ImpactResponse(
        reconciliation=_reconciliation(analysis),
        currency=analysis.impact.currency,
        scale=analysis.impact.scale,
        comparable=analysis.impact.comparable,
        headline=_kpi(analysis.impact.headline) if analysis.impact.headline else None,
        kpis=[_kpi(item) for item in analysis.impact.kpis],
        waterfall=[
            WaterfallStepResponse(
                kind=step.kind,
                key=step.key,
                label_ko=step.label_ko,
                label_en=step.label_en,
                value=step.value,
                to_category=step.to_category,
                reported_placement=step.reported_placement,
                line_ids=list(step.line_ids),
                classification_ids=ids.for_keys(step.line_ids),
            )
            for step in analysis.impact.waterfall
        ],
        waterfall_balances=analysis.impact.waterfall_balances,
        top_reclassifications=[
            ReclassificationResponse(
                classification_id=ids.by_key(item.line_id),
                line_id=ids.line_by_key(item.line_id),
                original_account=item.label,
                amount=item.amount,
                reported_placement=item.reported_placement,
                ifrs18_category=item.ifrs18_category,
                impact_on_operating_profit=item.impact_on_operating_profit,
                rule_id=item.rule_id,
                rationale_summary=item.rationale,
            )
            for item in analysis.impact.top_reclassifications
        ],
        computed_at=snapshot.computed_at if snapshot else None,
        disclaimer=DISCLAIMER_KO,
        limitations=_limitations(analysis),
    )


def _kpi(kpi: object) -> KpiResponse:
    return KpiResponse.model_validate(kpi, from_attributes=True)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/export/excel")
async def export_excel(
    project_id: str,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    acknowledge_unreconciled: Annotated[bool, Query()] = False,
) -> Response:
    """The Excel deliverable of spec §34.

    Refused with `409` unless the analysis reconciled — **unless** the caller
    acknowledges that explicitly, in which case every sheet of the produced
    file is watermarked. That satisfies §19 while still letting someone take
    failing output away to investigate it.
    """
    project = await _project(session, project_id, user)
    analysis = await _analysis(session, project, settings)

    if not analysis.reconciled and not acknowledge_unreconciled:
        raise ApiProblemError(
            status_code=status.HTTP_409_CONFLICT,
            title="This analysis did not reconcile.",
            code="reconciliation-failed",
            detail=(
                "Pass acknowledge_unreconciled=true to export it anyway. The "
                "file will be watermarked on every sheet."
            ),
            extra={"checks": _reconciliation(analysis).model_dump(mode="json")["checks"]},
        )

    workbook = build_workbook(
        ExportPackage(
            source=analysis.source,
            classified=analysis.classified,
            reconstructed=analysis.reconstructed,
            validation=analysis.validation,
            impact=analysis.impact,
            project_name=project.name,
            rule_set_version=project.rule_set_version or rule_set_version(),
            catalog_version=catalog_version(),
        )
    )
    buffer = io.BytesIO()
    workbook.save(buffer)
    payload = buffer.getvalue()

    record = Export(
        project_id=project.id,
        impact_analysis_id=(
            snapshot.id if (snapshot := await current_snapshot(session, project)) else None
        ),
        format=ExportFormat.XLSX.value,
        status=ExportStatus.READY.value,
        is_watermarked_unreconciled=not analysis.reconciled,
        requested_by=user.id,
    )
    session.add(record)
    await session.flush()
    await audit.record(
        session,
        action=AuditAction.EXPORTED,
        entity_type="exports",
        entity_id=record.id,
        project_id=project.id,
        actor_user_id=user.id,
        actor_type=ActorType.USER,
        after={
            "format": ExportFormat.XLSX.value,
            "watermarked": record.is_watermarked_unreconciled,
        },
    )

    filename = f"{project.fiscal_year}-ifrs18-analysis.xlsx"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if record.is_watermarked_unreconciled:
        # Said in a header too, so a client that streams the bytes straight to
        # disk still knows what it is handling. ASCII only: HTTP headers are
        # latin-1, and the watermark text itself is not.
        headers["X-IFRS18-Reconciliation"] = "FAILED"
    return Response(content=payload, media_type=EXPORT_MEDIA_TYPE, headers=headers)


@router.get("/projects/{project_id}/exports", response_model=list[ExportResponse])
async def list_exports(
    project_id: str, session: SessionDep, user: CurrentUser
) -> list[ExportResponse]:
    """What has been taken out of this project, and whether it was watermarked."""
    from sqlalchemy import select

    project = await _project(session, project_id, user)
    rows = (
        (
            await session.execute(
                select(Export)
                .where(Export.project_id == project.id)
                .order_by(Export.created_at.desc(), Export.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ExportResponse.model_validate(row) for row in rows]


# ---------------------------------------------------------------------------
# Mapping domain lines back to stored ids, for click-through
# ---------------------------------------------------------------------------


class _Ids:
    """Domain lines carry ordinals; a client needs the stored ids.

    Every waterfall step and reclassification is built with the ids the user
    will navigate by, so the click-through of spec §5 and §23 is a navigation
    rather than a second query.
    """

    def __init__(
        self,
        lines: dict[int, FinancialStatementLine],
        rows: dict[uuid.UUID, Ifrs18Classification],
    ) -> None:
        self._lines = lines
        self._rows = rows

    def line_id(self, item: ClassifiedLine) -> uuid.UUID | None:
        line = self._lines.get(item.line.ordinal)
        return line.id if line else None

    def _row(self, line_id: uuid.UUID | None) -> Ifrs18Classification | None:
        return self._rows.get(line_id) if line_id is not None else None

    def classification_id(self, item: ClassifiedLine) -> uuid.UUID | None:
        row = self._row(self.line_id(item))
        return row.id if row else None

    def overridden(self, item: ClassifiedLine) -> bool:
        row = self._row(self.line_id(item))
        return bool(row and row.user_override)

    def line_by_key(self, key: str) -> uuid.UUID | None:
        """A decision's ``line_id`` is the ordinal it was classified under."""
        line = self._lines.get(int(key)) if key.isdigit() else None
        return line.id if line else None

    def by_key(self, key: str) -> uuid.UUID | None:
        row = self._row(self.line_by_key(key))
        return row.id if row else None

    def for_keys(self, keys: tuple[str, ...]) -> list[uuid.UUID]:
        found = (self.by_key(key) for key in keys)
        return [item for item in found if item is not None]


async def _classification_ids(session: AsyncSession, project: Project, analysis: Analysis) -> _Ids:
    statement = await StatementRepository(session).primary_for_project(project.id)
    lines = await LineRepository(session).for_statement(statement.id) if statement else []
    rows = await ClassificationRepository(session).by_line(project.id)
    return _Ids({line.ordinal: line for line in lines}, rows)
