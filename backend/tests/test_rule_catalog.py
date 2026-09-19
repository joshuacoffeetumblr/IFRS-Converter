"""The shipped rule set must be well-formed, cited, and honest about its status."""

from __future__ import annotations

import json
from importlib import resources

import pytest

from app.data.rule_catalog import RuleCatalogError, _rule, get_engine, load_rules, rule_set_version
from app.domain.enums import Ifrs18Category, RuleVerificationStatus


def test_rule_set_loads_and_is_versioned() -> None:
    version, standard, rules = load_rules()

    assert version == rule_set_version()
    assert rules
    assert standard.name.startswith("IFRS 18")


def test_standard_metadata_is_recorded() -> None:
    """Spec §25: record the basis, including when the standard applies."""
    _, standard, _ = load_rules()

    assert standard.issued == "2024-04-09"
    assert standard.mandatory_from == "2027-01-01"
    assert standard.early_application_permitted


def test_every_rule_carries_a_citation() -> None:
    _, _, rules = load_rules()

    for rule in rules:
        assert rule.source.reference, rule.rule_id
        assert rule.source.type, rule.rule_id


def test_no_rule_claims_primary_verification() -> None:
    """Honesty check.

    This environment cannot reach the issued text of IFRS 18, so no citation
    has been confirmed against it. A rule claiming VERIFIED_PRIMARY here would
    be a false claim about how well the requirement is established.
    """
    _, _, rules = load_rules()

    overclaiming = [
        rule.rule_id
        for rule in rules
        if rule.source.verification_status is RuleVerificationStatus.VERIFIED_PRIMARY
    ]

    assert not overclaiming, (
        f"rules claim primary verification without it: {overclaiming}. "
        "See docs/07-ifrs18-source-verification.md."
    )


def test_rules_are_listed_in_priority_order() -> None:
    """The file is read by people; its order should match its behaviour."""
    raw = json.loads(
        resources.files("app.data").joinpath("classification_rules.json").read_text("utf-8")
    )
    priorities = [entry["priority"] for entry in raw["rules"]]

    assert priorities == sorted(priorities)


def test_priorities_are_unique() -> None:
    _, _, rules = load_rules()
    priorities = [rule.priority for rule in rules]

    assert len(priorities) == len(set(priorities))


def test_exactly_one_residual_rule_exists() -> None:
    """Two would make the outcome order-dependent; none would leave gaps."""
    _, _, rules = load_rules()

    residual = [rule for rule in rules if rule.is_residual]

    assert len(residual) == 1
    assert residual[0].outcome is not None
    assert residual[0].outcome.category is Ifrs18Category.OPERATING


def test_the_residual_has_the_lowest_precedence() -> None:
    _, _, rules = load_rules()
    residual = next(rule for rule in rules if rule.is_residual)

    assert residual.priority == max(rule.priority for rule in rules)


def test_fact_dependent_rules_define_both_outcomes() -> None:
    _, _, rules = load_rules()

    for rule in rules:
        if rule.requires_activity_fact is not None:
            assert rule.outcome_when_fact_true is not None, rule.rule_id
            assert rule.outcome_when_fact_false is not None, rule.rule_id


def test_line_fact_rules_inherit_and_offer_the_undue_cost_relief() -> None:
    """B65 and B72 both fall back to operating for undue cost or effort."""
    _, _, rules = load_rules()

    line_fact_rules = [rule for rule in rules if rule.requires_line_fact]
    assert line_fact_rules

    for rule in line_fact_rules:
        assert rule.inherits_category, rule.rule_id
        assert rule.undue_cost_outcome is not None, rule.rule_id
        assert rule.undue_cost_outcome.category is Ifrs18Category.OPERATING, rule.rule_id


def test_unverified_conditions_are_routed_to_review() -> None:
    """docs/07 remaining-verification items 4 and 5, as agreed."""
    _, _, rules = load_rules()
    by_id = {rule.rule_id: rule for rule in rules}

    assert by_id["IFRS18-INVESTING-002"].requires_human_review
    assert by_id["IFRS18-FINANCING-001"].requires_human_review


def test_equity_method_rule_has_no_activity_condition() -> None:
    """docs/07 F3: it is unconditional, and a regression test guards that."""
    _, _, rules = load_rules()
    equity = next(rule for rule in rules if rule.rule_id == "IFRS18-INVESTING-001")

    assert equity.requires_activity_fact is None
    assert equity.outcome is not None
    assert equity.outcome.category is Ifrs18Category.INVESTING


def test_engine_builds_from_the_shipped_rule_set() -> None:
    engine = get_engine()

    assert len(engine.rules) == len(load_rules()[2])


def test_malformed_rule_is_rejected() -> None:
    with pytest.raises(RuleCatalogError):
        _rule({"rule_id": "BAD", "priority": 1, "description": "x"})


def test_rule_with_an_unknown_field_is_rejected() -> None:
    """A typo must fail at load, not silently never match."""
    bad = _rule(
        {
            "rule_id": "BAD",
            "priority": 1,
            "description": "x",
            "condition": {"field": {"name": "nonexistent", "op": "eq", "value": 1}},
            "outcome": {"category": "OPERATING"},
            "source": {"type": "OTHER", "reference": "x"},
        }
    )

    with pytest.raises(Exception, match="unknown field"):
        bad.validate()
