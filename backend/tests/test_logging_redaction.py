"""Spec §32: raw financial values must not reach application logs."""

from __future__ import annotations

from app.core.logging import REDACTED, redact_financial_data


def test_monetary_fields_are_redacted() -> None:
    event = {
        "event": "classification_completed",
        "line_id": "abc",
        "amount": "3000000000.000000",
        "raw_label": "이자수익",
    }

    result = redact_financial_data(None, "info", dict(event))

    assert result["amount"] == REDACTED
    assert result["raw_label"] == REDACTED
    assert result["line_id"] == "abc", "non-sensitive identifiers survive"


def test_ai_payloads_are_redacted() -> None:
    event = {"ai_reasoning": "The item arises from investment assets.", "rule_id": "IFRS18-INV-001"}

    result = redact_financial_data(None, "info", dict(event))

    assert result["ai_reasoning"] == REDACTED
    assert result["rule_id"] == "IFRS18-INV-001"
