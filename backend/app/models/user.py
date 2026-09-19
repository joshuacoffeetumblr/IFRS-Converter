"""Users (ERD §2)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EnumText, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from app.domain.enums import UserRole


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (enum_check("role", UserRole),)

    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[EnumText] = mapped_column(default=UserRole.OWNER.value)
    #: Null when the account is SSO-only.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    deleted_at: Mapped[dt.datetime | None]
