"""Project lifecycle."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.project import (
    BlockingReason,
    CreateProjectRequest,
    ProjectProgress,
)
from app.domain.enums import ActorType, AuditAction, Ifrs18Category, ProjectStatus
from app.models import Project, User
from app.repositories.projects import CompanyRepository, ProjectRepository
from app.services import audit


async def create_project(
    session: AsyncSession, *, payload: CreateProjectRequest, owner: User
) -> Project:
    company = await CompanyRepository(session).get_or_create(
        name=payload.company.name,
        identifier=payload.company.identifier,
        identifier_scheme=(
            payload.company.identifier_scheme.value if payload.company.identifier_scheme else None
        ),
        jurisdiction=payload.company.jurisdiction,
        industry_code=payload.company.industry_code,
    )

    project = Project(
        owner_user_id=owner.id,
        company_id=company.id,
        name=payload.name,
        fiscal_year=payload.fiscal_year,
        period_start=payload.period_start,
        period_end=payload.period_end,
        basis=payload.basis,
        presentation_currency=payload.presentation_currency,
        presentation_scale=payload.presentation_scale,
        status=ProjectStatus.DRAFT,
    )
    await ProjectRepository(session).add(project)

    await audit.record(
        session,
        action=AuditAction.CREATED,
        entity_type="projects",
        entity_id=project.id,
        project_id=project.id,
        actor_user_id=owner.id,
        actor_type=ActorType.USER,
        after={"name": project.name, "fiscal_year": project.fiscal_year},
    )
    return project


async def delete_project(session: AsyncSession, *, project: Project, actor_id: uuid.UUID) -> None:
    """Soft delete.

    The uploaded statement and every classification decision remain referenced
    by the audit trail, which must stay readable (spec §8). Retention is a
    separate, scheduled concern (spec §32).
    """
    await ProjectRepository(session).soft_delete(project)
    await audit.record(
        session,
        action=AuditAction.DELETED,
        entity_type="projects",
        entity_id=project.id,
        project_id=project.id,
        actor_user_id=actor_id,
    )


async def progress_for(session: AsyncSession, project: Project) -> ProjectProgress:
    """What still stands between this project and a finalized statement.

    Computed here rather than in the client: the rule is an accounting one, and
    two implementations of it would eventually disagree about whether a
    statement may be finalized.
    """
    from sqlalchemy import func, select

    from app.models import (
        FinancialStatement,
        FinancialStatementLine,
        Ifrs18Classification,
        ReviewQuestion,
    )

    lines_total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(FinancialStatementLine)
                .join(
                    FinancialStatement,
                    FinancialStatement.id == FinancialStatementLine.statement_id,
                )
                .where(
                    FinancialStatement.project_id == project.id,
                    FinancialStatementLine.is_subtotal.is_(False),
                )
            )
        ).scalar_one()
    )

    classifications = (
        (
            await session.execute(
                select(Ifrs18Classification).where(Ifrs18Classification.project_id == project.id)
            )
        )
        .scalars()
        .all()
    )

    open_questions = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ReviewQuestion)
                .where(
                    ReviewQuestion.project_id == project.id,
                    ReviewQuestion.blocks_finalization.is_(True),
                    ReviewQuestion.answer.is_(None),
                )
            )
        ).scalar_one()
    )

    needing_review = [item for item in classifications if item.requires_human_review]
    unreviewed = [item for item in needing_review if item.reviewed_at is None]

    blocking: list[BlockingReason] = []
    if open_questions:
        blocking.append(
            BlockingReason(
                code="OPEN_REVIEW_QUESTION",
                count=open_questions,
                detail="A rule is waiting on a fact only the entity can supply.",
            )
        )
    if unreviewed:
        blocking.append(
            BlockingReason(
                code="UNREVIEWED_CLASSIFICATION",
                count=len(unreviewed),
                detail="These classifications have not been looked at by a person.",
            )
        )
    # A line the engine could not place, and nobody has placed since. The
    # reconciliation gate refuses these too, but by then the user has been told
    # they were ready to finalize — so they are reported here, where the work
    # still has somewhere to go.
    unplaced = [
        item
        for item in classifications
        if item.final_ifrs18_category in (None, Ifrs18Category.UNCLASSIFIED.value)
    ]
    missing = lines_total - len(classifications) if lines_total else 0
    if missing > 0 or unplaced:
        blocking.append(
            BlockingReason(
                code="UNCLASSIFIED_LINE",
                count=max(missing, 0) + len(unplaced),
                detail=(
                    "Some lines have no IFRS 18 category. Operating is the "
                    "residual category, so an unplaced line is a decision "
                    "nobody has made yet."
                ),
            )
        )
    if not lines_total:
        blocking.append(
            BlockingReason(
                code="NOTHING_EXTRACTED",
                count=1,
                detail="No statement has been extracted yet.",
            )
        )

    return ProjectProgress(
        lines_total=lines_total,
        lines_classified=len(classifications),
        requires_review=len(needing_review),
        reviewed=len(needing_review) - len(unreviewed),
        open_questions=open_questions,
        can_finalize=not blocking,
        blocking_reasons=blocking,
    )
