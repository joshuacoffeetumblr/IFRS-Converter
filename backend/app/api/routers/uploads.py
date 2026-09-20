"""Upload, extraction and line correction (API spec §2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ingest.grid import ExtractOptions
from app.adapters.storage.files import (
    CHUNK_SIZE,
    StoredFile,
    UploadRejectedError,
    store_upload,
)
from app.api.deps import (
    CurrentUser,
    SessionDep,
    SettingsDep,
    ensure_not_finalized,
    parse_uuid,
)
from app.api.errors import NotFoundError, UnprocessableStateError
from app.api.schemas.statement import (
    ExtractionReportResponse,
    ExtractRequest,
    ExtractResponse,
    LineResponse,
    LinesResponse,
    ReconciliationCheckResponse,
    SourceLocatorResponse,
    StatementResponse,
    UpdateLineRequest,
    UploadResponse,
)
from app.domain.enums import (
    ActorType,
    AuditAction,
    NormalizationMethod,
    ParseStatus,
    ProjectStatus,
    ScanStatus,
)
from app.domain.extraction import ReconciliationReport
from app.models import FinancialStatementLine, NormalizedAccount, Project, UploadedFile
from app.repositories.accounts import AccountRepository
from app.repositories.projects import ProjectRepository
from app.repositories.statements import (
    LineRepository,
    StatementRepository,
    UploadRepository,
)
from app.services import audit
from app.services.extraction import ExtractionError, extract

router = APIRouter(tags=["statements"])


async def _project(session: SessionDep, project_id: str, user: CurrentUser) -> Project:
    project = await ProjectRepository(session).by_id(parse_uuid(project_id, "Project"), user.id)
    if project is None:
        raise NotFoundError("Project")
    return project


def _line_response(line: FinancialStatementLine) -> LineResponse:
    return LineResponse(
        id=line.id,
        ordinal=line.ordinal,
        depth=line.depth,
        raw_label=line.raw_label,
        raw_value=line.raw_value,
        amount=line.amount,
        sign_normalization=line.sign_normalization,
        is_subtotal=line.is_subtotal,
        subtotal_kind=line.subtotal_kind,
        decomposition_status=line.decomposition_status,
        parent_line_id=line.parent_line_id,
        normalized_account_code=(
            line.normalized_account.code if line.normalized_account is not None else None
        ),
        note_references=list(line.note_references or []),
        source_locator=SourceLocatorResponse.model_validate(line.source_locator or {}),
    )


def _line_state(line: FinancialStatementLine) -> dict[str, Any]:
    """The audited state of a line.

    Recorded in the same shape before and after, so the two are comparable: a
    partial diff of whatever the client happened to send would report the
    figure as the client typed it against the figure as stored, and the two
    are not the same string.
    """
    return {
        "raw_label": line.raw_label,
        "amount": str(line.amount),
        "is_subtotal": line.is_subtotal,
        "subtotal_kind": line.subtotal_kind,
        "normalized_account_code": (
            line.normalized_account.code if line.normalized_account is not None else None
        ),
    }


async def _resolve_account(
    session: AsyncSession, code: str | None, *, line: FinancialStatementLine
) -> NormalizedAccount | None:
    """Map a canonical account code onto the seeded dictionary.

    An unrecognised code is refused rather than stored: a mapping to an account
    that does not exist would survive review looking like a decision.
    """
    if not code:
        return None
    if line.is_subtotal:
        raise UnprocessableStateError(
            title="A subtotal cannot carry an account",
            code="subtotal-has-no-account",
            detail="A subtotal is a reconciliation target, not a classifiable item.",
        )
    account = await AccountRepository(session).by_code(code)
    if account is None:
        raise UnprocessableStateError(
            title="Unknown account",
            code="unknown-account",
            detail=f"{code} is not an account in the dictionary.",
        )
    return account


def _report_response(
    report: ReconciliationReport, *, signs_inferred: bool = False
) -> ExtractionReportResponse:
    return ExtractionReportResponse(
        passed=report.passed,
        blockers=list(report.blockers),
        signs_inferred=signs_inferred,
        checks=[
            ReconciliationCheckResponse(
                check=check.check,
                reported=check.reported,
                computed=check.computed,
                delta=check.delta,
                tolerance=check.tolerance,
                passed=check.passed,
            )
            for check in report.checks
        ],
    )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    project_id: str,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
) -> UploadResponse:
    project = await _project(session, project_id, user)

    async def chunks() -> AsyncIterator[bytes]:
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk

    try:
        stored: StoredFile = await store_upload(
            chunks(),
            filename=file.filename,
            directory=settings.upload.directory,
            max_bytes=settings.upload.max_bytes,
            allowed_mime_types=settings.upload.allowed_content_types,
        )
    except UploadRejectedError as exc:
        raise UnprocessableStateError(
            title="Upload rejected", code=exc.code, detail=exc.reason
        ) from exc

    uploads = UploadRepository(session)
    duplicate = await uploads.find_by_digest(project.id, stored.sha256)
    if duplicate is not None:
        # The identical file is already attached. Returning the existing record
        # keeps a retried upload idempotent instead of creating a second row
        # that later extractions would have to choose between.
        stored.path.unlink(missing_ok=True)
        return UploadResponse.model_validate(duplicate)

    record = await uploads.add(
        UploadedFile(
            project_id=project.id,
            original_filename=stored.original_filename,
            storage_key=stored.storage_key,
            mime_type=stored.mime_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            # No scanner is wired up yet, and saying CLEAN would be a claim we
            # cannot support. SKIPPED is the honest value.
            scan_status=ScanStatus.SKIPPED,
            parse_status=ParseStatus.PENDING,
            uploaded_by=user.id,
        )
    )
    project.status = ProjectStatus.UPLOADED
    await session.flush()

    await audit.record(
        session,
        action=AuditAction.CREATED,
        entity_type="uploaded_files",
        entity_id=record.id,
        project_id=project.id,
        actor_user_id=user.id,
        actor_type=ActorType.USER,
        after={"filename": record.original_filename, "sha256": record.sha256},
    )
    return UploadResponse.model_validate(record)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/extract", response_model=ExtractResponse)
async def run_extraction(
    project_id: str,
    payload: ExtractRequest,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
) -> ExtractResponse:
    project = await _project(session, project_id, user)
    uploads = UploadRepository(session)

    upload = (
        await uploads.by_id(payload.uploaded_file_id, project.id)
        if payload.uploaded_file_id
        else await uploads.latest_for_project(project.id)
    )
    if upload is None:
        raise UnprocessableStateError(
            title="Nothing to extract",
            code="no-upload",
            detail="Upload a statement before running extraction.",
        )

    try:
        result = await extract(
            session,
            project=project,
            upload=upload,
            storage_dir=settings.upload.directory,
            options=ExtractOptions(
                sheet=payload.sheet,
                header_row=payload.header_row,
                label_column=payload.label_column,
                amount_column=payload.amount_column,
                note_column=payload.note_column,
                period_index=payload.period_index,
            ),
            actor_id=user.id,
        )
    except ExtractionError as exc:
        # The service has already recorded the failure on the project and the
        # upload, and that record is the point: a project whose extraction
        # failed must not look untouched afterwards. The session dependency
        # rolls back on its way out of a failed request, so the bookkeeping is
        # committed here before the error is raised.
        await session.commit()
        raise UnprocessableStateError(
            title="Extraction failed", code=exc.code, detail=exc.reason
        ) from exc

    return ExtractResponse(
        statement=StatementResponse.model_validate(result.statement),
        line_count=len(result.lines),
        report=_report_response(result.report, signs_inferred=result.signs_inferred),
    )


@router.get("/projects/{project_id}/statements", response_model=list[StatementResponse])
async def list_statements(
    project_id: str, session: SessionDep, user: CurrentUser
) -> list[StatementResponse]:
    project = await _project(session, project_id, user)
    statements = await StatementRepository(session).for_project(project.id)
    return [StatementResponse.model_validate(item) for item in statements]


@router.get("/projects/{project_id}/lines", response_model=LinesResponse)
async def list_lines(project_id: str, session: SessionDep, user: CurrentUser) -> LinesResponse:
    """Every extracted line, with the reconciliation that verified it."""
    from app.domain.extraction import reconcile_extraction
    from app.services.extraction import to_extracted_statement

    project = await _project(session, project_id, user)
    statement = await StatementRepository(session).primary_for_project(project.id)
    if statement is None:
        return LinesResponse(items=[])

    lines = await LineRepository(session).for_statement(statement.id)
    report = reconcile_extraction(to_extracted_statement(statement, lines))
    return LinesResponse(
        items=[_line_response(line) for line in lines],
        report=_report_response(report),
    )


@router.patch("/projects/{project_id}/lines/{line_id}", response_model=LineResponse)
async def update_line(
    project_id: str,
    line_id: str,
    payload: UpdateLineRequest,
    session: SessionDep,
    user: CurrentUser,
) -> LineResponse:
    """Correct a misread cell.

    Editing a line invalidates anything derived from it, so the project returns
    to ``EXTRACTED``: a classification decided against the old figure is no
    longer a decision about this statement.
    """
    project = await _project(session, project_id, user)
    line = await LineRepository(session).by_id(parse_uuid(line_id, "Line"), project.id)
    if line is None:
        raise NotFoundError("Line")

    ensure_not_finalized(project)

    before = _line_state(line)

    updates = payload.model_dump(exclude_unset=True)
    # The account is a relationship, not a column, so it is resolved rather
    # than assigned. `setattr` would look like it worked and change nothing.
    # A mentioned-but-null code means "clear it", which is not the same as not
    # mentioning it at all — hence `model_fields_set` rather than the value.
    account_code = updates.pop("normalized_account_code", None)
    remap_account = "normalized_account_code" in payload.model_fields_set

    for field, value in updates.items():
        setattr(line, field, value)

    if payload.is_subtotal is False and payload.subtotal_kind is None:
        line.subtotal_kind = None
    if payload.is_subtotal:
        # A subtotal is a reconciliation target, never a classifiable fact.
        line.normalized_account = None
        line.normalization_method = None
        line.normalization_score = None
        remap_account = False

    if remap_account:
        line.normalized_account = await _resolve_account(session, account_code, line=line)
        line.normalization_method = NormalizationMethod.MANUAL if account_code else None
        line.normalization_score = None

    project.status = ProjectStatus.EXTRACTED
    await session.flush()
    # Report what was stored, not what was sent: the column is NUMERIC(38, 6),
    # so `12500` comes back as `12500.000000` and a client comparing the two
    # would see a change it did not make.
    await session.refresh(line)

    await audit.record(
        session,
        action=AuditAction.UPDATED,
        entity_type="financial_statement_lines",
        entity_id=line.id,
        project_id=project.id,
        actor_user_id=user.id,
        actor_type=ActorType.USER,
        before=before,
        after=_line_state(line),
    )
    return _line_response(line)
