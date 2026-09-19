"""Object factories for integration tests.

The ``db_session`` fixture itself lives in ``conftest.py`` so pytest discovers
it without an import — importing a fixture into a test module shadows it and
trips F811.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    ProjectBasis,
    SignNormalization,
    StatementType,
    UserRole,
)
from app.models import (
    Company,
    FinancialStatement,
    FinancialStatementLine,
    Project,
    User,
)


async def make_user(session: AsyncSession, *, email: str | None = None) -> User:
    user = User(
        email=email or f"{uuid.uuid4().hex}@example.com",
        display_name="Test Preparer",
        role=UserRole.PREPARER,
    )
    session.add(user)
    await session.flush()
    return user


async def make_company(session: AsyncSession, *, name: str = "테스트 주식회사") -> Company:
    company = Company(name=name, jurisdiction="KR")
    session.add(company)
    await session.flush()
    return company


async def make_project(session: AsyncSession) -> Project:
    user = await make_user(session)
    company = await make_company(session)
    project = Project(
        owner_user_id=user.id,
        company_id=company.id,
        name="2025 연결 손익계산서",
        fiscal_year=2025,
        period_start=dt.date(2025, 1, 1),
        period_end=dt.date(2025, 12, 31),
        basis=ProjectBasis.CONSOLIDATED,
        presentation_currency="KRW",
        presentation_scale=6,
    )
    session.add(project)
    await session.flush()
    return project


async def make_statement(session: AsyncSession, project: Project) -> FinancialStatement:
    statement = FinancialStatement(
        project_id=project.id,
        statement_type=StatementType.INCOME_STATEMENT,
        period_start=project.period_start,
        period_end=project.period_end,
        currency="KRW",
        scale=6,
    )
    session.add(statement)
    await session.flush()
    return statement


async def make_line(
    session: AsyncSession,
    statement: FinancialStatement,
    *,
    ordinal: int,
    raw_label: str,
    amount: Decimal,
    **kwargs: object,
) -> FinancialStatementLine:
    line = FinancialStatementLine(
        statement_id=statement.id,
        ordinal=ordinal,
        raw_label=raw_label,
        raw_value=str(amount),
        amount=amount,
        sign_normalization=SignNormalization.AS_IS,
        source_locator={"sheet": "손익계산서", "row": ordinal, "column": "D"},
        **kwargs,
    )
    session.add(line)
    await session.flush()
    return line
