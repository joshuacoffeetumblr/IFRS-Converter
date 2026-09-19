"""Data access for uploads, statements and extracted lines."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    FinancialStatement,
    FinancialStatementLine,
    Ifrs18Classification,
    UploadedFile,
)


class UploadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, upload: UploadedFile) -> UploadedFile:
        self._session.add(upload)
        await self._session.flush()
        return upload

    async def by_id(self, upload_id: uuid.UUID, project_id: uuid.UUID) -> UploadedFile | None:
        """Scoped by project, so an upload from another project is not visible."""
        result = await self._session.execute(
            select(UploadedFile).where(
                UploadedFile.id == upload_id, UploadedFile.project_id == project_id
            )
        )
        return result.scalar_one_or_none()

    async def latest_for_project(self, project_id: uuid.UUID) -> UploadedFile | None:
        result = await self._session.execute(
            select(UploadedFile)
            .where(UploadedFile.project_id == project_id)
            .order_by(UploadedFile.created_at.desc(), UploadedFile.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_by_digest(self, project_id: uuid.UUID, sha256: str) -> UploadedFile | None:
        result = await self._session.execute(
            select(UploadedFile).where(
                UploadedFile.project_id == project_id, UploadedFile.sha256 == sha256
            )
        )
        return result.scalars().first()


class StatementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, statement: FinancialStatement) -> FinancialStatement:
        self._session.add(statement)
        await self._session.flush()
        return statement

    async def for_project(self, project_id: uuid.UUID) -> list[FinancialStatement]:
        result = await self._session.execute(
            select(FinancialStatement)
            .where(FinancialStatement.project_id == project_id)
            .order_by(FinancialStatement.created_at, FinancialStatement.id)
        )
        return list(result.scalars().all())

    async def primary_for_project(self, project_id: uuid.UUID) -> FinancialStatement | None:
        """The current-period income statement the analysis runs on."""
        result = await self._session.execute(
            select(FinancialStatement)
            .where(
                FinancialStatement.project_id == project_id,
                FinancialStatement.statement_type == "INCOME_STATEMENT",
                FinancialStatement.is_comparative.is_(False),
            )
            .order_by(FinancialStatement.created_at.desc(), FinancialStatement.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def replace_project_statements(self, project_id: uuid.UUID) -> int:
        """Discard a previous extraction before running a new one.

        Re-extracting must not leave the old lines behind: they would be
        double counted and the classifications attached to them would refer to
        figures no longer on screen. Classifications go first, since they
        reference the lines.
        """
        await self._session.execute(
            delete(Ifrs18Classification).where(Ifrs18Classification.project_id == project_id)
        )
        existing = await self.for_project(project_id)
        for statement in existing:
            await self._session.delete(statement)
        await self._session.flush()
        return len(existing)


class LineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_all(self, lines: list[FinancialStatementLine]) -> None:
        self._session.add_all(lines)
        await self._session.flush()

    async def for_statement(self, statement_id: uuid.UUID) -> list[FinancialStatementLine]:
        result = await self._session.execute(
            select(FinancialStatementLine)
            .where(FinancialStatementLine.statement_id == statement_id)
            .order_by(FinancialStatementLine.ordinal)
        )
        return list(result.scalars().all())

    async def for_project(self, project_id: uuid.UUID) -> list[FinancialStatementLine]:
        result = await self._session.execute(
            select(FinancialStatementLine)
            .join(
                FinancialStatement,
                FinancialStatement.id == FinancialStatementLine.statement_id,
            )
            .where(FinancialStatement.project_id == project_id)
            .order_by(FinancialStatementLine.ordinal)
        )
        return list(result.scalars().all())

    async def by_id(
        self, line_id: uuid.UUID, project_id: uuid.UUID
    ) -> FinancialStatementLine | None:
        """Scoped through the statement to the project, so ownership still holds."""
        result = await self._session.execute(
            select(FinancialStatementLine)
            .join(
                FinancialStatement,
                FinancialStatement.id == FinancialStatementLine.statement_id,
            )
            .where(
                FinancialStatementLine.id == line_id,
                FinancialStatement.project_id == project_id,
            )
        )
        return result.scalar_one_or_none()

    async def count_for_project(self, project_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(FinancialStatementLine)
            .join(
                FinancialStatement,
                FinancialStatement.id == FinancialStatementLine.statement_id,
            )
            .where(FinancialStatement.project_id == project_id)
        )
        return int(result.scalar_one())
