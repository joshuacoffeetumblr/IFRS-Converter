"""Loading the review questions a ``NEEDS_FACT`` rule raises (spec §5).

The questions live in ``review_questions.json`` for the same reason the rules
do: their wording is the thing the user actually decides on, so it has to be
reviewable — and translatable — without reading Python.

The catalog is checked against the rule set at load time. A rule that asks for
a fact nobody wrote a question for would otherwise surface as a blank prompt in
the review screen, which is the one place this product cannot afford to be
vague.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

from app.domain.enums import ActivityType, QuestionAnswer, QuestionScope

QUESTIONS_RESOURCE = "review_questions.json"

#: The key a main-business-activity question is filed under. Derived rather
#: than written down twice, so a rule naming an activity always finds its
#: question.
ACTIVITY_QUESTION_PREFIX = "SMBA_"


class QuestionCatalogError(ValueError):
    """The question file is malformed, or does not cover the rule set."""


def activity_question_key(activity: ActivityType) -> str:
    return f"{ACTIVITY_QUESTION_PREFIX}{activity.value}"


@dataclass(frozen=True, slots=True)
class QuestionSpec:
    question_key: str
    scope: QuestionScope
    question_text_ko: str
    question_text_en: str
    answers: tuple[QuestionAnswer, ...]
    raised_by_rule_ids: tuple[str, ...] = ()
    activity_type: ActivityType | None = None
    help_ko: str | None = None
    help_en: str | None = None
    #: Whether IFRS 18's "undue cost or effort" relief applies to this
    #: question. Where it does, saying so is a complete answer (B65, B72) —
    #: unlike "not sure", which leaves the item blocked.
    allows_undue_cost_or_effort: bool = False


def _question(entry: dict[str, Any]) -> QuestionSpec:
    try:
        activity = entry.get("activity_type")
        scope = QuestionScope(entry["scope"])
        return QuestionSpec(
            question_key=entry["question_key"],
            scope=scope,
            question_text_ko=entry["question_text_ko"],
            question_text_en=entry["question_text_en"],
            answers=tuple(QuestionAnswer(value) for value in entry["answers"]),
            raised_by_rule_ids=tuple(entry.get("raised_by_rule_ids", ())),
            activity_type=ActivityType(activity) if activity else None,
            help_ko=entry.get("help_ko"),
            help_en=entry.get("help_en"),
            allows_undue_cost_or_effort=bool(entry.get("allows_undue_cost_or_effort", False)),
        )
    except (KeyError, ValueError) as exc:
        raise QuestionCatalogError(
            f"invalid question {entry.get('question_key', entry)!r}: {exc}"
        ) from exc


def load_questions() -> tuple[str, dict[str, QuestionSpec]]:
    raw = resources.files("app.data").joinpath(QUESTIONS_RESOURCE).read_text(encoding="utf-8")
    return parse_questions(json.loads(raw))


def parse_questions(document: dict[str, Any]) -> tuple[str, dict[str, QuestionSpec]]:
    """Validate a question set. Separate from reading the file so the checks
    can be tested against a malformed catalog without one on disk."""
    version = document.get("version")
    if not version:
        raise QuestionCatalogError("question set has no version")

    questions: dict[str, QuestionSpec] = {}
    for entry in document.get("questions", ()):
        spec = _question(entry)
        if spec.question_key in questions:
            raise QuestionCatalogError(f"duplicate question key {spec.question_key!r}")
        if (spec.scope is QuestionScope.COMPANY) != (spec.activity_type is not None):
            raise QuestionCatalogError(
                f"{spec.question_key}: a COMPANY question settles an activity, "
                "and only a COMPANY question does"
            )
        if spec.activity_type and spec.question_key != activity_question_key(spec.activity_type):
            raise QuestionCatalogError(
                f"{spec.question_key}: an activity question's key must be "
                f"{activity_question_key(spec.activity_type)}"
            )
        questions[spec.question_key] = spec

    if not questions:
        raise QuestionCatalogError("question set contains no questions")
    return version, questions


@lru_cache(maxsize=1)
def get_questions() -> dict[str, QuestionSpec]:
    """Every question, keyed by ``question_key``, checked against the rule set."""
    from app.data.rule_catalog import load_rules

    _, questions = load_questions()
    _, _, rules = load_rules()

    for rule in rules:
        if rule.requires_activity_fact is not None:
            key = activity_question_key(rule.requires_activity_fact)
        elif rule.requires_line_fact is not None:
            key = rule.requires_line_fact
        else:
            continue
        if key not in questions:
            raise QuestionCatalogError(
                f"rule {rule.rule_id} raises {key!r}, which no question defines"
            )
    return questions


def get_question(question_key: str) -> QuestionSpec | None:
    return get_questions().get(question_key)
