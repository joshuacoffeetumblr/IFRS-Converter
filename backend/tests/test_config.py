"""Configuration invariants that protect accounting correctness."""

from __future__ import annotations

from decimal import Decimal

from app.core.config import ClassificationSettings, ReconciliationSettings, Settings


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
