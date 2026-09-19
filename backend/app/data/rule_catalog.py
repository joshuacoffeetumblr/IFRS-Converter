"""Loading the IFRS 18 rule set from its data file.

The rule set is data (``classification_rules.json``) so that each rule carries
its citation and can be reviewed by an accountant without reading Python
(spec §25). This module is the only place that reads it, which keeps
``app.domain.classification`` pure and testable against rules constructed in
the test itself.

Every rule is validated at load time — condition shape, field names, operators,
and the presence of the outcomes its kind requires. A malformed rule is a
startup failure, never a silent no-match at classification time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from typing import Any

from app.domain.classification import (
    ClassificationEngine,
    ClassificationRuleSpec,
    Outcome,
    RuleSource,
)
from app.domain.enums import (
    ActivityType,
    Ifrs18Category,
    Ifrs18Subcategory,
    RuleVerificationStatus,
)
from app.domain.rules import RuleDefinitionError

RULES_RESOURCE = "classification_rules.json"


class RuleCatalogError(ValueError):
    """The rule set file is malformed."""


@dataclass(frozen=True, slots=True)
class StandardInfo:
    name: str
    issued: str
    mandatory_from: str
    early_application_permitted: bool


def _outcome(raw: dict[str, Any] | None) -> Outcome | None:
    if raw is None:
        return None
    subcategory = raw.get("subcategory")
    return Outcome(
        category=Ifrs18Category(raw["category"]),
        subcategory=Ifrs18Subcategory(subcategory) if subcategory else None,
    )


def _rule(entry: dict[str, Any]) -> ClassificationRuleSpec:
    try:
        source_raw = entry["source"]
        activity = entry.get("requires_activity_fact")
        ceiling = entry.get("confidence_ceiling")
        return ClassificationRuleSpec(
            rule_id=entry["rule_id"],
            priority=int(entry["priority"]),
            description=entry["description"],
            condition=entry["condition"],
            source=RuleSource(
                type=source_raw["type"],
                reference=source_raw["reference"],
                verification_status=RuleVerificationStatus(
                    source_raw.get("verification_status", "UNVERIFIED")
                ),
                url=source_raw.get("url"),
                note=source_raw.get("note"),
            ),
            outcome=_outcome(entry.get("outcome")),
            requires_activity_fact=ActivityType(activity) if activity else None,
            outcome_when_fact_true=_outcome(entry.get("outcome_when_fact_true")),
            outcome_when_fact_false=_outcome(entry.get("outcome_when_fact_false")),
            requires_line_fact=entry.get("requires_line_fact"),
            inherits_category=bool(entry.get("inherits_category", False)),
            undue_cost_outcome=_outcome(entry.get("undue_cost_outcome")),
            requires_human_review=bool(entry.get("requires_human_review", False)),
            is_residual=bool(entry.get("is_residual", False)),
            confidence_ceiling=Decimal(str(ceiling)) if ceiling is not None else None,
        )
    except (KeyError, ValueError) as exc:
        raise RuleCatalogError(f"invalid rule {entry.get('rule_id', entry)!r}: {exc}") from exc


def load_rules() -> tuple[str, StandardInfo, tuple[ClassificationRuleSpec, ...]]:
    raw = resources.files("app.data").joinpath(RULES_RESOURCE).read_text(encoding="utf-8")
    document = json.loads(raw)

    version = document.get("version")
    if not version:
        raise RuleCatalogError("rule set has no version")

    standard_raw = document.get("standard") or {}
    standard = StandardInfo(
        name=standard_raw.get("name", "IFRS 18"),
        issued=standard_raw.get("issued", ""),
        mandatory_from=standard_raw.get("mandatory_from", ""),
        early_application_permitted=bool(standard_raw.get("early_application_permitted", False)),
    )

    rules = tuple(_rule(entry) for entry in document.get("rules", ()))
    if not rules:
        raise RuleCatalogError("rule set contains no rules")

    for rule in rules:
        try:
            rule.validate()
        except RuleDefinitionError as exc:
            raise RuleCatalogError(str(exc)) from exc

    return version, standard, rules


@lru_cache(maxsize=1)
def get_engine() -> ClassificationEngine:
    _, _, rules = load_rules()
    return ClassificationEngine(rules)


@lru_cache(maxsize=1)
def rule_set_version() -> str:
    version, _, _ = load_rules()
    return version
