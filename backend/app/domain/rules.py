"""The rule condition language, and its interpreter.

Rules are **data**, not code (spec §25): each carries its authoritative citation
and effective date, so the rule set can be exported, diffed, and reviewed by an
accountant who does not read Python.

That makes an interpreter necessary, and the interpreter is deliberately total
and tiny:

* no ``eval``, and no code is ever loaded from the database;
* every field name is checked against a whitelist **at load time**, so a typo
  is a startup failure rather than a silent ``NO_MATCH`` that quietly
  reclassifies an item;
* every operator is explicit, with no coercion between types.

A silent no-match is the dangerous failure here. An item that should have been
investing and instead falls through to the residual operating category changes
operating profit without anything in the audit trail explaining why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.enums import ActivityType, Ifrs18Category, Ifrs18Subcategory

# ---------------------------------------------------------------------------
# Fields a condition may read
# ---------------------------------------------------------------------------

#: Scalar fields of the item being classified.
ITEM_FIELDS: frozenset[str] = frozenset(
    {
        "normalized_account_code",
        "raw_label",
        "amount",
        "current_category",
        "statement_section",
        "is_aggregate",
        "account_ancestors",
        "note_references",
    }
)

#: Prefix for a tri-state fact about the entity, e.g.
#: ``entity.main_business_activity.INVESTING_IN_ASSETS``.
ACTIVITY_FIELD_PREFIX = "entity.main_business_activity."

OPERATORS: frozenset[str] = frozenset(
    {"eq", "ne", "in", "not_in", "matches", "is", "contains", "gt", "gte", "lt", "lte"}
)

COMBINATORS: frozenset[str] = frozenset({"all", "any", "not"})


class RuleDefinitionError(ValueError):
    """A rule's condition is malformed. Raised at load time, never at match time."""


@dataclass(frozen=True, slots=True)
class LineFact:
    """A fact about one line that only the entity can supply (Q4).

    Which risk a derivative manages (B72) and which item gave rise to a foreign
    exchange difference (B65) cannot be read from an account name, and two
    lines in one statement can answer differently — so unlike a main business
    activity, this is asked and answered per line.

    ``undue_cost_or_effort`` is the standard's own relief, not a way of saying
    "unknown": it means the user has told us that tracing the underlying item
    would require grossing up or is impracticable, which is itself a complete
    answer and routes the line to operating.
    """

    question_key: str
    category: Ifrs18Category | None = None
    subcategory: Ifrs18Subcategory | None = None
    undue_cost_or_effort: bool = False

    @property
    def is_answered(self) -> bool:
        return self.undue_cost_or_effort or self.category is not None


@dataclass(frozen=True, slots=True)
class EntityFacts:
    """Confirmed facts about the reporting entity (spec §10).

    ``is_main`` is **tri-state**: ``None`` means unknown and is what triggers a
    ``NEEDS_FACT`` outcome. ``None`` and ``False`` are never conflated — only
    ``None`` blocks, because a user who answered "no" has already been asked.

    ``line_facts`` carries the per-line answers, keyed by the same ``line_id``
    the caller puts on a ``ClassifiableItem``. An absent key is unknown, which
    is what keeps a line-fact rule blocked until somebody answers it.
    """

    main_business_activities: dict[ActivityType, bool | None]
    line_facts: dict[tuple[str, str], LineFact] = field(default_factory=dict)

    def is_main(self, activity: ActivityType) -> bool | None:
        return self.main_business_activities.get(activity)

    def line_fact(self, line_id: str, question_key: str) -> LineFact | None:
        fact = self.line_facts.get((line_id, question_key))
        return fact if fact is not None and fact.is_answered else None

    @classmethod
    def unknown(cls) -> EntityFacts:
        return cls(main_business_activities={})


@dataclass(frozen=True, slots=True)
class ClassifiableItem:
    """Everything a rule is allowed to see about one line."""

    line_id: str
    raw_label: str
    amount: Decimal
    normalized_account_code: str | None = None
    current_category: str | None = None
    statement_section: str = "PL"
    is_aggregate: bool = False
    #: The account's code plus its ancestors, so a rule can match a whole
    #: subtree without naming every leaf.
    account_ancestors: tuple[str, ...] = ()
    note_references: tuple[str, ...] = ()

    def field(self, name: str) -> Any:
        if name not in ITEM_FIELDS:  # pragma: no cover - guarded at load time
            raise RuleDefinitionError(f"unknown field {name!r}")
        return getattr(self, name)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_condition(condition: Any, *, path: str = "condition") -> None:
    """Check a condition's shape and field names. Raises, or returns None."""
    if not isinstance(condition, dict):
        raise RuleDefinitionError(f"{path}: expected an object, got {type(condition).__name__}")
    if len(condition) != 1:
        raise RuleDefinitionError(f"{path}: expected exactly one key, got {sorted(condition)}")

    key, value = next(iter(condition.items()))

    if key in COMBINATORS:
        if key == "not":
            validate_condition(value, path=f"{path}.not")
            return
        if not isinstance(value, list) or not value:
            raise RuleDefinitionError(f"{path}.{key}: expected a non-empty list")
        for index, child in enumerate(value):
            validate_condition(child, path=f"{path}.{key}[{index}]")
        return

    if key != "field":
        raise RuleDefinitionError(
            f"{path}: expected one of {sorted(COMBINATORS)} or 'field', got {key!r}"
        )

    if not isinstance(value, dict):
        raise RuleDefinitionError(f"{path}.field: expected an object")

    name = value.get("name")
    if not isinstance(name, str):
        raise RuleDefinitionError(f"{path}.field: 'name' must be a string")
    if name not in ITEM_FIELDS and not name.startswith(ACTIVITY_FIELD_PREFIX):
        raise RuleDefinitionError(
            f"{path}.field: unknown field {name!r}; "
            f"expected one of {sorted(ITEM_FIELDS)} or {ACTIVITY_FIELD_PREFIX}<ACTIVITY>"
        )
    if name.startswith(ACTIVITY_FIELD_PREFIX):
        activity = name.removeprefix(ACTIVITY_FIELD_PREFIX)
        try:
            ActivityType(activity)
        except ValueError as exc:
            raise RuleDefinitionError(f"{path}.field: unknown activity {activity!r}") from exc

    operator = value.get("op")
    if operator not in OPERATORS:
        raise RuleDefinitionError(
            f"{path}.field: unknown operator {operator!r}; expected one of {sorted(OPERATORS)}"
        )

    if "value" not in value:
        raise RuleDefinitionError(f"{path}.field: missing 'value'")

    if operator in {"in", "not_in"} and not isinstance(value["value"], list):
        raise RuleDefinitionError(f"{path}.field: {operator!r} requires a list")
    if operator == "is" and value["value"] not in (True, False, None):
        raise RuleDefinitionError(f"{path}.field: 'is' requires true, false or null")
    if operator == "matches":
        pattern = value["value"]
        if not isinstance(pattern, str):
            raise RuleDefinitionError(f"{path}.field: 'matches' requires a string")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RuleDefinitionError(f"{path}.field: invalid regex {pattern!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _as_decimal(value: Any) -> Decimal:
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise RuleDefinitionError(f"not comparable as a number: {value!r}") from exc


def _resolve(name: str, item: ClassifiableItem, facts: EntityFacts) -> Any:
    if name.startswith(ACTIVITY_FIELD_PREFIX):
        return facts.is_main(ActivityType(name.removeprefix(ACTIVITY_FIELD_PREFIX)))
    return item.field(name)


def _apply(operator: str, actual: Any, expected: Any) -> bool:
    match operator:
        case "eq":
            return bool(actual == expected)
        case "ne":
            return bool(actual != expected)
        case "in":
            return actual in expected
        case "not_in":
            return actual not in expected
        case "is":
            # Identity against the tri-state, so None (unknown) never compares
            # equal to False (the user said no).
            return actual is expected
        case "contains":
            return bool(actual) and expected in actual
        case "matches":
            return actual is not None and re.search(expected, str(actual)) is not None
        case "gt":
            return _as_decimal(actual) > _as_decimal(expected)
        case "gte":
            return _as_decimal(actual) >= _as_decimal(expected)
        case "lt":
            return _as_decimal(actual) < _as_decimal(expected)
        case "lte":
            return _as_decimal(actual) <= _as_decimal(expected)
    raise RuleDefinitionError(f"unknown operator {operator!r}")  # pragma: no cover


def evaluate_condition(
    condition: dict[str, Any],
    item: ClassifiableItem,
    facts: EntityFacts,
) -> bool:
    """Evaluate a validated condition. Never raises for ordinary data."""
    key, value = next(iter(condition.items()))

    if key == "all":
        return all(evaluate_condition(child, item, facts) for child in value)
    if key == "any":
        return any(evaluate_condition(child, item, facts) for child in value)
    if key == "not":
        return not evaluate_condition(value, item, facts)

    actual = _resolve(value["name"], item, facts)
    return _apply(value["op"], actual, value["value"])
