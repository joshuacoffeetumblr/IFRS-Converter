"""Data access for the seeded account dictionary."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NormalizedAccount


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_code(self, code: str) -> NormalizedAccount | None:
        """Look an account up by its canonical code, e.g. ``INTEREST_INCOME``.

        Inactive accounts are excluded: retiring one has to stop new mappings
        being made to it, or the retirement means nothing.
        """
        result = await self._session.execute(
            select(NormalizedAccount).where(
                NormalizedAccount.code == code,
                NormalizedAccount.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()
