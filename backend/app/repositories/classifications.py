"""Data access for IFRS 18 classifications and their evidence."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import ClassificationEvidence, Ifrs18Classification


class ClassificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_project(
        self,
        project_id: uuid.UUID,
        *,
        requires_review: bool | None = None,
        category: str | None = None,
        method: str | None = None,
    ) -> list[Ifrs18Classification]:
        """Every classification in the project, largest absolute impact first.

        The default order is the review order (arch §9): the line that moves
        operating profit most is the one a reviewer should see first. Ordering
        is applied in Python because the sort key is ``abs()`` of a nullable
        column and the tie-break has to be stable.
        """
        query = (
            select(Ifrs18Classification)
            .where(Ifrs18Classification.project_id == project_id)
            .options(selectinload(Ifrs18Classification.evidence))
        )
        if requires_review is not None:
            query = query.where(Ifrs18Classification.requires_human_review.is_(requires_review))
        if category:
            query = query.where(Ifrs18Classification.final_ifrs18_category == category)
        if method:
            query = query.where(Ifrs18Classification.classification_method == method)

        rows = list((await self._session.execute(query)).scalars().all())
        return sorted(rows, key=_review_order)

    async def by_id(
        self, classification_id: uuid.UUID, project_id: uuid.UUID
    ) -> Ifrs18Classification | None:
        """Scoped by project, so another project's id is simply not here."""
        result = await self._session.execute(
            select(Ifrs18Classification)
            .where(
                Ifrs18Classification.id == classification_id,
                Ifrs18Classification.project_id == project_id,
            )
            .options(selectinload(Ifrs18Classification.evidence))
        )
        return result.scalar_one_or_none()

    async def by_line(self, project_id: uuid.UUID) -> dict[uuid.UUID, Ifrs18Classification]:
        """Keyed by line, which is how a re-run finds what it already decided."""
        rows = (
            (
                await self._session.execute(
                    select(Ifrs18Classification)
                    .where(Ifrs18Classification.project_id == project_id)
                    .options(selectinload(Ifrs18Classification.evidence))
                )
            )
            .scalars()
            .all()
        )
        return {row.line_id: row for row in rows}

    async def add(self, classification: Ifrs18Classification) -> Ifrs18Classification:
        self._session.add(classification)
        await self._session.flush()
        return classification

    async def delete_for_lines(self, project_id: uuid.UUID, line_ids: Sequence[uuid.UUID]) -> int:
        """Drop classifications for lines that are no longer classifiable.

        A line corrected into a subtotal keeps its old decision otherwise, and
        that decision would still be counted in the statement it no longer
        belongs to.
        """
        if not line_ids:
            return 0
        matched = (
            Ifrs18Classification.project_id == project_id,
            Ifrs18Classification.line_id.in_(line_ids),
        )
        # Counted before the delete rather than read from `rowcount`, which
        # SQLAlchemy does not promise on every result object.
        doomed = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(Ifrs18Classification).where(*matched)
                )
            ).scalar_one()
        )
        await self._session.execute(delete(Ifrs18Classification).where(*matched))
        await self._session.flush()
        return doomed

    async def replace_evidence(
        self, classification: Ifrs18Classification, evidence: Sequence[ClassificationEvidence]
    ) -> None:
        classification.evidence.clear()
        for item in evidence:
            classification.evidence.append(item)
        await self._session.flush()


def _review_order(row: Ifrs18Classification) -> tuple[Decimal, str]:
    """Largest absolute effect on operating profit first, then stable by id.

    A classification with no computed impact sorts last rather than first: an
    unknown movement is not a large one. The id breaks ties so the order is
    total — two lines of equal size must not swap places between requests.
    """
    impact = row.impact_on_operating_profit
    return (Decimal(0) if impact is None else -abs(impact), str(row.id))
