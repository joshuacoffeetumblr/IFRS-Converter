"""Application configuration.

Every tunable value lives here and is sourced from the environment (spec §29).
Accounting thresholds in particular are configuration, never literals in the
engine: spec §11 requires confidence thresholds to be configurable, and §19
requires reconciliation tolerances to be explicit.
"""

from __future__ import annotations

import json
import os
import secrets
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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


class AiSettings(BaseSettings):
    """The AI advisor (spec §1 layer 2, §12).

    **Off unless a key is present.** The product works without it: rules decide
    what they can, and everything else goes to a person. An advisor adds
    suggestions to that queue; it never removes the queue.
    """

    model_config = SettingsConfigDict(env_prefix="IFRS18_AI_")

    #: Set to false to keep the assistant off even where a key is configured.
    enabled: bool = True
    api_key: str | None = None
    model: str = "claude-opus-5"
    #: Classification is a short, well-specified judgement, not open-ended
    #: reasoning. Raise it if a statement's captions are genuinely obscure.
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_tokens: int = 2_000
    timeout_seconds: float = 30.0
    #: One repair round trip when the output does not validate (spec §12).
    repair_attempts: int = 1

    @property
    def configured(self) -> bool:
        """Whether an advisor can actually be built.

        The key is read from `IFRS18_AI_API_KEY` or the SDK's own
        `ANTHROPIC_API_KEY`; without either there is nothing to call, and
        saying so here keeps every caller from having to check twice.
        """
        return self.enabled and bool(self.api_key or os.environ.get("ANTHROPIC_API_KEY"))


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

    #: Comma-separated in the environment.
    #:
    #: `NoDecode` is load-bearing. pydantic-settings decodes a complex field —
    #: anything list-shaped — as JSON *in the environment source*, before any
    #: validator runs, so `IFRS18_CORS_ORIGINS=https://app.example.com` raised
    #: `SettingsError` and the process died at import. That is the syntax
    #: `.env.example`, `docker-compose.yml` and the production overlay all
    #: document, which meant `docker compose up` could never have worked.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    auth: AuthSettings = Field(default_factory=AuthSettings)
    classification: ClassificationSettings = Field(default_factory=ClassificationSettings)
    reconciliation: ReconciliationSettings = Field(default_factory=ReconciliationSettings)
    upload: UploadSettings = Field(default_factory=UploadSettings)
    ai: AiSettings = Field(default_factory=AiSettings)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated list, or a JSON array for compatibility."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(decoded, list):
                    return [str(origin).strip() for origin in decoded if str(origin).strip()]
        return [origin.strip() for origin in text.split(",") if origin.strip()]

    def check_production_ready(self) -> None:
        """Fail fast on configuration that is only safe in development.

        Every one of these is a default that is *correct* locally and dangerous
        in production, which is the combination that ships. A misconfiguration
        that stops the process is an outage; the same misconfiguration that
        starts cleanly is an incident nobody notices, so this refuses to start
        and names what to set.

        Reported together rather than one at a time: an operator fixing a
        deployment should learn everything that is wrong in one restart.
        """
        if self.environment != "production":
            return

        problems: list[str] = []

        # A generated signing key is fine locally — every restart invalidates
        # tokens, which is harmless. In production it means nobody set the key
        # on purpose, and tokens do not survive a deployment.
        if not os.environ.get("IFRS18_AUTH_SECRET_KEY"):
            problems.append(
                "IFRS18_AUTH_SECRET_KEY is unset, so the signing key is generated "
                "per process. Set it to a secret of at least 32 characters."
            )
        elif len(self.auth.secret_key) < 32:
            problems.append(
                "IFRS18_AUTH_SECRET_KEY is shorter than 32 characters, which is "
                "too short to sign tokens with."
            )

        # Debug responses carry tracebacks, and a traceback from this service
        # quotes financial data (spec §32).
        if self.debug:
            problems.append("IFRS18_DEBUG is on, which exposes tracebacks. Set it to false.")

        # The credentials in docker-compose and .env.example are published in
        # this repository. Reaching production with them is not a weak password,
        # it is a public one.
        url = str(self.database_url)
        if "ifrs18:ifrs18@" in url:
            problems.append(
                "IFRS18_DATABASE_URL still carries the development credentials, "
                "which are published in this repository."
            )

        if not self.cors_origins:
            problems.append("IFRS18_CORS_ORIGINS is empty, so the web app cannot call the API.")
        for origin in self.cors_origins:
            if origin == "*":
                problems.append(
                    "IFRS18_CORS_ORIGINS contains '*'. Credentialed requests carry "
                    "financial data; name the web app's origin instead."
                )
            elif origin.startswith("http://") and not origin.startswith("http://localhost"):
                problems.append(
                    f"IFRS18_CORS_ORIGINS contains the plaintext origin {origin!r}. "
                    "Uploads and exports must not travel over http in production."
                )
            elif origin.startswith("http://localhost"):
                problems.append(f"IFRS18_CORS_ORIGINS contains the development origin {origin!r}.")

        if problems:
            raise RuntimeError(
                "Refusing to start in production. "
                + " ".join(f"({index}) {text}" for index, text in enumerate(problems, start=1))
            )

    @property
    def sync_database_url(self) -> str:
        """Alembic runs migrations synchronously."""
        return str(self.database_url).replace("+asyncpg", "+psycopg")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
