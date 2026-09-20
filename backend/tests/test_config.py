"""Configuration invariants that protect accounting correctness."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.config import (
    AiSettings,
    ClassificationSettings,
    ReconciliationSettings,
    Settings,
)


def test_confidence_thresholds_match_spec() -> None:
    settings = Settings(environment="ci")

    assert settings.classification.high_confidence_min == Decimal("0.95")
    assert settings.classification.medium_confidence_min == Decimal("0.80")


def test_no_accounting_setting_is_declared_as_float() -> None:
    """Architecture §5: no binary float may reach an accounting decision.

    Asserted against the model's declared annotations rather than a sample of
    values, so a field added later cannot quietly reintroduce ``float``.
    """
    for model in (ClassificationSettings, ReconciliationSettings):
        for name, field in model.model_fields.items():
            assert field.annotation is not float, f"{model.__name__}.{name} must be Decimal"

    settings = Settings(environment="ci")
    assert isinstance(settings.classification.high_confidence_min, Decimal)
    assert isinstance(settings.reconciliation.profit_before_tax_tolerance, Decimal)


def test_alembic_url_uses_sync_driver() -> None:
    settings = Settings(environment="ci")

    assert "+asyncpg" in str(settings.database_url)
    assert "+psycopg" in settings.sync_database_url
    assert "+asyncpg" not in settings.sync_database_url


# ---------------------------------------------------------------------------
# Refusing to start on a dangerous production configuration (spec §31, §32)
# ---------------------------------------------------------------------------


SAFE_PRODUCTION: dict[str, object] = {
    "environment": "production",
    "debug": False,
    "database_url": "postgresql+asyncpg://app:a-real-secret@db.internal:5432/ifrs18",
    "cors_origins": ["https://ifrs18.example.com"],
}


def _production(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> Settings:
    """A production Settings with a real signing key, plus whatever is broken."""
    monkeypatch.setenv("IFRS18_AUTH_SECRET_KEY", "x" * 48)
    return Settings(**{**SAFE_PRODUCTION, **overrides})  # type: ignore[arg-type]


def test_a_correctly_configured_production_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    _production(monkeypatch).check_production_ready()


def test_development_is_never_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same settings that are refused in production are the right defaults
    locally, so the check must key on the environment and nothing else."""
    monkeypatch.delenv("IFRS18_AUTH_SECRET_KEY", raising=False)
    Settings(environment="local", debug=True).check_production_ready()


def test_an_unset_signing_key_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """A per-process key means nobody set one on purpose, and anyone who reads
    the default can mint tokens the moment it becomes a shared constant."""
    monkeypatch.delenv("IFRS18_AUTH_SECRET_KEY", raising=False)
    settings = Settings(**SAFE_PRODUCTION)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="IFRS18_AUTH_SECRET_KEY"):
        settings.check_production_ready()


def test_a_short_signing_key_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IFRS18_AUTH_SECRET_KEY", "short")
    settings = Settings(**SAFE_PRODUCTION)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="32 characters"):
        settings.check_production_ready()


def test_debug_mode_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec §32: a traceback from this service quotes financial data."""
    with pytest.raises(RuntimeError, match="IFRS18_DEBUG"):
        _production(monkeypatch, debug=True).check_production_ready()


def test_the_repositorys_own_database_credentials_refuse_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """These are in docker-compose.yml and .env.example. Reaching production
    with them is not a weak password, it is a published one."""
    with pytest.raises(RuntimeError, match="development credentials"):
        _production(
            monkeypatch,
            database_url="postgresql+asyncpg://ifrs18:ifrs18@db:5432/ifrs18",
        ).check_production_ready()


def test_a_wildcard_cors_origin_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match=r"\*"):
        _production(monkeypatch, cors_origins=["*"]).check_production_ready()


def test_a_plaintext_cors_origin_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uploads and exports carry the client's financial statements."""
    with pytest.raises(RuntimeError, match="plaintext origin"):
        _production(
            monkeypatch, cors_origins=["http://ifrs18.example.com"]
        ).check_production_ready()


def test_a_localhost_cors_origin_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default. It is how a production deployment ends up trusting a
    developer's laptop."""
    with pytest.raises(RuntimeError, match="development origin"):
        _production(monkeypatch, cors_origins=["http://localhost:3000"]).check_production_ready()


def test_empty_cors_origins_refuse_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="cannot call the API"):
        _production(monkeypatch, cors_origins=[]).check_production_ready()


def test_every_problem_is_reported_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator fixing a deployment should learn everything that is wrong in
    one restart, not discover the next one after each fix."""
    monkeypatch.delenv("IFRS18_AUTH_SECRET_KEY", raising=False)
    settings = Settings(
        environment="production",
        debug=True,
        database_url="postgresql+asyncpg://ifrs18:ifrs18@db:5432/ifrs18",
        cors_origins=["*"],
    )

    with pytest.raises(RuntimeError) as caught:
        settings.check_production_ready()

    message = str(caught.value)
    assert "(4)" in message
    for expected in ("SECRET_KEY", "IFRS18_DEBUG", "development credentials", "*"):
        assert expected in message


def test_the_ai_assistant_is_off_until_a_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §1: the product's answer for anything a rule cannot decide is a
    person, with or without AI. Nothing here may turn itself on.

    The environment is cleared explicitly: a key that happens to sit in the
    shell would otherwise decide the result of this test, which is the exact
    confusion it exists to rule out.
    """
    for name in ("ANTHROPIC_API_KEY", "IFRS18_AI_API_KEY", "IFRS18_AI_ENABLED"):
        monkeypatch.delenv(name, raising=False)

    assert AiSettings().configured is False
    assert AiSettings(enabled=False, api_key="sk-test").configured is False
    assert AiSettings(enabled=True, api_key="sk-test").configured is True

    # A key in the ambient environment is the SDK's own convention, so it does
    # enable the assistant — but only together with the explicit switch.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ambient")
    assert AiSettings(enabled=True).configured is True
    assert AiSettings(enabled=False).configured is False


# ---------------------------------------------------------------------------
# Reading settings from the environment, the way a deployment actually does
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The exact syntax .env.example, docker-compose.yml and the production
        # overlay all document. It raised SettingsError and killed the process
        # at import, so `docker compose up` could never have worked.
        ("http://localhost:3000", ["http://localhost:3000"]),
        (
            "https://a.example.com,https://b.example.com",
            ["https://a.example.com", "https://b.example.com"],
        ),
        (
            "  https://a.example.com , https://b.example.com  ",
            ["https://a.example.com", "https://b.example.com"],
        ),
        # A JSON array still works, because that is what pydantic-settings
        # accepted before and somebody's deployment may be passing it.
        ('["https://c.example.com"]', ["https://c.example.com"]),
        ("", []),
    ],
)
def test_cors_origins_parse_from_a_plain_environment_variable(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    """Settings are read from the environment in every real deployment, and a
    list-shaped field is decoded as JSON *in the environment source* — before
    any validator runs. Constructing `Settings(cors_origins=[...])` in a test
    bypasses that path entirely, which is how this went unnoticed."""
    monkeypatch.setenv("IFRS18_CORS_ORIGINS", raw)

    assert Settings(environment="ci").cors_origins == expected


def test_an_unset_cors_origin_keeps_the_development_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IFRS18_CORS_ORIGINS", raising=False)

    assert Settings(environment="ci").cors_origins == ["http://localhost:3000"]
