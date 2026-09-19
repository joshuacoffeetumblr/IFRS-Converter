"""Data access for users, companies and projects.

Ownership is enforced **here**, not in the routers. Every project query is
scoped by the owning user, so a router cannot forget: the worst outcome of a
missed check in this product is one client seeing another's financial
statements.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, Project, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def add(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()
        return user


class CompanyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(
        self,
        *,
        name: str,
        identifier: str | None,
        identifier_scheme: str | None,
        jurisdiction: str,
        industry_code: str | None = None,
    ) -> Company:
        """Match on the registered identifier where there is one.

        Names are not unique and are frequently entered differently, so
        matching on a name would merge unrelated entities. Without an
        identifier a new company row is created rather than guessed at.
        """
        if identifier:
            existing = await self._session.execute(
                select(Company).where(
                    Company.identifier == identifier,
                    Company.identifier_scheme == identifier_scheme,
                )
            )
            found = existing.scalar_one_or_none()
            if found is not None:
                return found

        company = Company(
            name=name,
            identifier=identifier,
            identifier_scheme=identifier_scheme,
            jurisdiction=jurisdiction,
            industry_code=industry_code,
        )
        self._session.add(company)
        await self._session.flush()
        return company


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _owned(self, owner_id: uuid.UUID) -> Select[tuple[Project]]:
        return select(Project).where(
            Project.owner_user_id == owner_id, Project.deleted_at.is_(None)
        )

    async def by_id(self, project_id: uuid.UUID, owner_id: uuid.UUID) -> Project | None:
        """Scoped by owner, so another user's id simply does not exist here."""
        result = await self._session.execute(self._owned(owner_id).where(Project.id == project_id))
        return result.scalar_one_or_none()

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        limit: int = 25,
        status: str | None = None,
    ) -> list[Project]:
        # Ordered by a *pair*, not by timestamp alone. PostgreSQL's `now()` is
        # transaction time, so rows written in one transaction share a
        # `created_at`; ordering by it alone is therefore not a total order,
        # and a cursor built on it would skip or repeat rows at the tie.
        query = (
            self._owned(owner_id)
            .order_by(Project.created_at.desc(), Project.id.desc())
            .limit(limit)
        )
        if status:
            query = query.where(Project.status == status)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def count_for_owner(self, owner_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(self._owned(owner_id).subquery())
        )
        return int(result.scalar_one())

    async def add(self, project: Project) -> Project:
        self._session.add(project)
        await self._session.flush()
        return project

    async def soft_delete(self, project: Project) -> None:
        project.deleted_at = dt.datetime.now(dt.UTC)
        await self._session.flush()
