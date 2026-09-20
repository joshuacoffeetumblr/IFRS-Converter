"""Data access for review questions, answers and declared business activities."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BusinessActivity, ReviewQuestion, UserReview


class QuestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_project(self, project_id: uuid.UUID) -> list[ReviewQuestion]:
        result = await self._session.execute(
            select(ReviewQuestion)
            .where(ReviewQuestion.project_id == project_id)
            .order_by(ReviewQuestion.created_at, ReviewQuestion.id)
        )
        return list(result.scalars().all())

    async def by_id(self, question_id: uuid.UUID, project_id: uuid.UUID) -> ReviewQuestion | None:
        """Scoped by project: a question from another analysis is not visible."""
        result = await self._session.execute(
            select(ReviewQuestion).where(
                ReviewQuestion.id == question_id,
                ReviewQuestion.project_id == project_id,
            )
        )
        return result.scalar_one_or_none()

    async def find(
        self, project_id: uuid.UUID, question_key: str, line_id: uuid.UUID | None
    ) -> ReviewQuestion | None:
        """The one question with this key and scope, if it has been raised.

        Matches the ``(project_id, question_key, line_id)`` unique constraint,
        so re-running classification re-finds the question the user already
        answered instead of raising a duplicate.
        """
        query = select(ReviewQuestion).where(
            ReviewQuestion.project_id == project_id,
            ReviewQuestion.question_key == question_key,
        )
        query = query.where(
            ReviewQuestion.line_id == line_id
            if line_id is not None
            else ReviewQuestion.line_id.is_(None)
        )
        return (await self._session.execute(query)).scalar_one_or_none()

    async def add(self, question: ReviewQuestion) -> ReviewQuestion:
        self._session.add(question)
        await self._session.flush()
        return question

    async def delete(self, question: ReviewQuestion) -> None:
        await self._session.delete(question)
        await self._session.flush()


class ActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_project(
        self, company_id: uuid.UUID, project_id: uuid.UUID
    ) -> list[BusinessActivity]:
        """Activities that apply to this project.

        Both the company-wide rows and the ones confirmed for this project
        specifically: a main business activity is a property of the entity, but
        a user may confirm it while working on one period.
        """
        result = await self._session.execute(
            select(BusinessActivity)
            .where(
                BusinessActivity.company_id == company_id,
                (BusinessActivity.project_id == project_id)
                | (BusinessActivity.project_id.is_(None)),
            )
            .order_by(BusinessActivity.activity_type)
        )
        return list(result.scalars().all())

    async def find(
        self, company_id: uuid.UUID, project_id: uuid.UUID | None, activity_type: str
    ) -> BusinessActivity | None:
        query = select(BusinessActivity).where(
            BusinessActivity.company_id == company_id,
            BusinessActivity.activity_type == activity_type,
        )
        query = query.where(
            BusinessActivity.project_id == project_id
            if project_id is not None
            else BusinessActivity.project_id.is_(None)
        )
        return (await self._session.execute(query)).scalar_one_or_none()

    async def add(self, activity: BusinessActivity) -> BusinessActivity:
        self._session.add(activity)
        await self._session.flush()
        return activity


class ReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, review: UserReview) -> UserReview:
        """Append-only: a review is never updated, only followed by another."""
        self._session.add(review)
        await self._session.flush()
        return review

    async def for_classification(self, classification_id: uuid.UUID) -> list[UserReview]:
        result = await self._session.execute(
            select(UserReview)
            .where(UserReview.classification_id == classification_id)
            .order_by(UserReview.reviewed_at, UserReview.id)
        )
        return list(result.scalars().all())
