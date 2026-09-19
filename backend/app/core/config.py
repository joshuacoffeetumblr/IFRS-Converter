"""Application configuration.

Every tunable value lives here and is sourced from the environment (spec §29).
Accounting thresholds in particular are configuration, never literals in the
engine: spec §11 requires confidence thresholds to be configurable, and §19
requires reconciliation tolerances to be explicit.
"""

from __future__ import annotations

import os
import secrets
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ClassificationSettings(BaseSettings):
    """Thresholds governing the three-layer decision model (spec §1, §11).

    These are snapshotted onto each project at classification time
    (``projects.config_snapshot``) so a finalized analysis can always explain
    itself under the thresholds that actually applied to it.
    """

    model_config = SettingsConfigDict(env_prefix="IFRS18_CLASSIFICATION_")

    high_confidence_min: Decimal = Decimal("0.95")
    medium_confidence_min: Decimal = Decimal("0.80")

    #: Unmatched items below this share of revenue skip individual human review.
    materiality_floor_pct_of_revenue: Decimal = Decimal("0.1")

    #: Any reclassification moving operating profit by more than this share is
    #: always reviewed, regardless of the method's confidence.
    always_review_operating_profit_pct: Decimal = Decimal("1.0")


class ReconciliationSettings(BaseSettings):
    """Tolerances for the validation gate (spec §19, architecture §7)."""

    model_config = SettingsConfigDict(env_prefix="IFRS18_RECONCILIATION_")

    #: Tolerance for profit-before-tax agreement, in presentation units.
    #: Non-zero only because source documents round; any actual difference is
    #: always reported, never silently absorbed.
    profit_before_tax_tolerance: Decimal = Decimal("1")

    #: Tolerance for agreement with subtotals printed in the source document.
    subtotal_tolerance: Decimal = Decimal("1")

    # Total invariance has no tolerance: IFRS 18 changes presentation, not
    # measurement, so the sum of all income and expenses cannot change.
    # A difference there is a bug, not a rounding artefact.


class UploadSettings(BaseSettings):
    """File-ingest limits (spec §31)."""

    model_config = SettingsConfigDict(env_prefix="IFRS18_UPLOAD_")

    max_bytes: int = 25 * 1024 * 1024
    allowed_content_types: tuple[str, ...] = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "text/csv",
        "application/pdf",
    )
    #: Uploaded statements are deleted this many days after upload (spec §32).
    retention_days: int = 30
    #: Where uploaded files are written. A local directory for now; the
    #: interface is narrow enough that object storage slots in behind it.
    directory: Path = Path("var/uploads")


class AuthSettings(BaseSettings):
    """Bearer-token settings (spec §31)."""

    model_config = SettingsConfigDict(env_prefix="IFRS18_AUTH_")

    #: Signing key. The default is generated per process, which is deliberate:
    #: a shared hardcoded default would be a published signing key, and an
    #: unset key in production must break loudly rather than silently accept
    #: tokens anyone could mint. `Settings.check_production_ready` enforces it.
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    token_ttl_minutes: int = 12 * 60
    #: Minimum password length. Length dominates composition rules in practice.
    min_password_length: int = 12


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="IFRS18_",
        extra="ignore",
    )

    environment: Literal["local", "ci", "staging", "production"] = "local"
    debug: bool = False
    api_prefix: str = "/api"
    log_level: str = "INFO"

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://ifrs18:ifrs18@localhost:5432/ifrs18"),
    )

    #: Comma-separated in the environment; parsed into a list by pydantic.
    cors_origins: list[str] = ["http://localhost:3000"]

    auth: AuthSettings = Field(default_factory=AuthSettings)
    classification: ClassificationSettings = Field(default_factory=ClassificationSettings)
    reconciliation: ReconciliationSettings = Field(default_factory=ReconciliationSettings)
    upload: UploadSettings = Field(default_factory=UploadSettings)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    def check_production_ready(self) -> None:
        """Fail fast on configuration that is only safe in development.

        A generated signing key is fine locally — every restart invalidates
        tokens, which is harmless. In production it means tokens do not survive
        a deployment, and more importantly it means nobody set the key on
        purpose. Refusing to start is better than discovering it later.
        """
        if self.environment != "production":
            return
        if not os.environ.get("IFRS18_AUTH_SECRET_KEY"):
            raise RuntimeError(
                "IFRS18_AUTH_SECRET_KEY must be set in production; refusing to "
                "start with a per-process signing key."
            )

    @property
    def sync_database_url(self) -> str:
        """Alembic runs migrations synchronously."""
        return str(self.database_url).replace("+asyncpg", "+psycopg")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
