"""The rule condition language. Pure — no rule set, no database."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.enums import ActivityType
from app.domain.rules import (
    ClassifiableItem,
    EntityFacts,
    RuleDefinitionError,
    evaluate_condition,
    validate_condition,
)


def item(**overrides: object) -> ClassifiableItem:
    defaults: dict[str, object] = {
        "line_id": "line-1",
        "raw_label": "이자수익",
        "amount": Decimal("3000"),
        "normalized_account_code": "INTEREST_INCOME",
        "account_ancestors": ("INTEREST_INCOME", "FINANCE_INCOME"),
    }
    defaults.update(overrides)
    return ClassifiableItem(**defaults)  # type: ignore[arg-type]


NO_FACTS = EntityFacts.unknown()


# ---------------------------------------------------------------------------
# Validation happens at load time, not at match time
# ---------------------------------------------------------------------------


def test_unknown_field_is_rejected_at_load_time() -> None:
    """A typo must be a startup failure, not a silent no-match.

    A condition that quietly never matches would let an item fall through to
    the residual operating category, changing operating profit with nothing in
    the audit trail to explain it.
    """
    with pytest.raises(RuleDefinitionError, match="unknown field"):
        validate_condition({"field": {"name": "acount_code", "op": "eq", "value": "X"}})


def test_unknown_operator_is_rejected() -> None:
    with pytest.raises(RuleDefinitionError, match="unknown operator"):
        validate_condition({"field": {"name": "raw_label", "op": "sounds_like", "value": "X"}})


def test_unknown_activity_is_rejected() -> None:
    with pytest.raises(RuleDefinitionError, match="unknown activity"):
        validate_condition(
            {"field": {"name": "entity.main_business_activity.MINING", "op": "is", "value": True}}
        )


def test_invalid_regex_is_rejected() -> None:
    with pytest.raises(RuleDefinitionError, match="invalid regex"):
        validate_condition({"field": {"name": "raw_label", "op": "matches", "value": "[unclosed"}})


def test_in_requires_a_list() -> None:
    with pytest.raises(RuleDefinitionError, match="requires a list"):
        validate_condition({"field": {"name": "raw_label", "op": "in", "value": "not a list"}})


def test_is_requires_a_tri_state_value() -> None:
    with pytest.raises(RuleDefinitionError, match="true, false or null"):
        validate_condition({"field": {"name": "raw_label", "op": "is", "value": "maybe"}})


def test_empty_combinator_is_rejected() -> None:
    with pytest.raises(RuleDefinitionError, match="non-empty"):
        validate_condition({"all": []})


def test_multiple_keys_are_rejected() -> None:
    with pytest.raises(RuleDefinitionError, match="exactly one key"):
        validate_condition({"all": [], "any": []})


def test_valid_nested_condition_passes() -> None:
    validate_condition(
        {
            "all": [
                {"field": {"name": "normalized_account_code", "op": "in", "value": ["A", "B"]}},
                {
                    "not": {
                        "field": {
                            "name": "entity.main_business_activity.INVESTING_IN_ASSETS",
                            "op": "is",
                            "value": True,
                        }
                    }
                },
            ]
        }
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def cond(name: str, op: str, value: object) -> dict[str, object]:
    return {"field": {"name": name, "op": op, "value": value}}


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (cond("normalized_account_code", "eq", "INTEREST_INCOME"), True),
        (cond("normalized_account_code", "eq", "REVENUE"), False),
        (cond("normalized_account_code", "ne", "REVENUE"), True),
        (cond("normalized_account_code", "in", ["REVENUE", "INTEREST_INCOME"]), True),
        (cond("normalized_account_code", "not_in", ["REVENUE"]), True),
        (cond("account_ancestors", "contains", "FINANCE_INCOME"), True),
        (cond("account_ancestors", "contains", "REVENUE"), False),
        (cond("raw_label", "matches", "이자"), True),
        (cond("amount", "gt", "1000"), True),
        (cond("amount", "lt", "1000"), False),
        (cond("amount", "gte", "3000"), True),
        (cond("is_aggregate", "eq", False), True),
    ],
)
def test_operators(condition: dict[str, object], expected: bool) -> None:
    assert evaluate_condition(condition, item(), NO_FACTS) is expected


def test_all_requires_every_child() -> None:
    condition = {
        "all": [
            {"field": {"name": "normalized_account_code", "op": "eq", "value": "INTEREST_INCOME"}},
            {"field": {"name": "amount", "op": "gt", "value": "999999"}},
        ]
    }

    assert evaluate_condition(condition, item(), NO_FACTS) is False


def test_any_requires_one_child() -> None:
    condition = {
        "any": [
            {"field": {"name": "normalized_account_code", "op": "eq", "value": "REVENUE"}},
            {"field": {"name": "normalized_account_code", "op": "eq", "value": "INTEREST_INCOME"}},
        ]
    }

    assert evaluate_condition(condition, item(), NO_FACTS) is True


def test_not_inverts() -> None:
    condition = {"not": {"field": {"name": "is_aggregate", "op": "eq", "value": True}}}

    assert evaluate_condition(condition, item(), NO_FACTS) is True


# ---------------------------------------------------------------------------
# The tri-state, which is the whole point of NEEDS_FACT
# ---------------------------------------------------------------------------


def test_unknown_fact_is_not_false() -> None:
    """`None` (never asked) must never satisfy a test for `False` (user said no).

    Conflating them would classify items the user was never asked about.
    """
    condition = {
        "field": {
            "name": "entity.main_business_activity.INVESTING_IN_ASSETS",
            "op": "is",
            "value": False,
        }
    }

    assert evaluate_condition(condition, item(), EntityFacts.unknown()) is False
    assert (
        evaluate_condition(
            condition, item(), EntityFacts({ActivityType.INVESTING_IN_ASSETS: False})
        )
        is True
    )


def test_unknown_fact_matches_null() -> None:
    condition = {
        "field": {
            "name": "entity.main_business_activity.INVESTING_IN_ASSETS",
            "op": "is",
            "value": None,
        }
    }

    assert evaluate_condition(condition, item(), EntityFacts.unknown()) is True


def test_confirmed_fact_is_read() -> None:
    condition = {
        "field": {
            "name": "entity.main_business_activity.PROVIDING_FINANCING_TO_CUSTOMERS",
            "op": "is",
            "value": True,
        }
    }
    facts = EntityFacts({ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS: True})

    assert evaluate_condition(condition, item(), facts) is True


def test_amounts_compare_as_decimals() -> None:
    """Comparison must not route through binary floating point."""
    condition = {"field": {"name": "amount", "op": "gte", "value": "0.1"}}
    small = item(amount=Decimal("0.1"))

    assert evaluate_condition(condition, small, NO_FACTS) is True
