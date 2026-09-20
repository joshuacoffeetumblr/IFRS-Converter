"""The review questions, as data (spec §5).

The load-time checks matter more than they look: a rule that asks for a fact
no question defines would reach the user as a blank prompt in the one screen
where this product cannot afford to be vague.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.data.question_catalog import (
    QuestionCatalogError,
    activity_question_key,
    get_question,
    get_questions,
    load_questions,
    parse_questions,
)
from app.data.rule_catalog import load_rules
from app.domain.enums import ActivityType, QuestionAnswer, QuestionScope


def test_every_rule_that_needs_a_fact_has_a_question() -> None:
    questions = get_questions()
    _, _, rules = load_rules()

    for rule in rules:
        if rule.requires_activity_fact is not None:
            assert activity_question_key(rule.requires_activity_fact) in questions, rule.rule_id
        if rule.requires_line_fact is not None:
            assert rule.requires_line_fact in questions, rule.rule_id


def test_every_question_is_raised_by_a_rule_that_exists() -> None:
    """A question nobody asks is dead weight in a screen the user reads."""
    _, questions = load_questions()
    _, _, rules = load_rules()
    known = {rule.rule_id for rule in rules}

    for spec in questions.values():
        assert spec.raised_by_rule_ids, spec.question_key
        assert set(spec.raised_by_rule_ids) <= known, spec.question_key


def test_both_specified_main_business_activities_are_askable() -> None:
    """IFRS 18 names two; an entity may have both (B30)."""
    questions = get_questions()

    for activity in ActivityType:
        if activity.is_specified_main_business_activity:
            assert activity_question_key(activity) in questions


def test_an_entity_question_settles_an_activity_and_a_line_question_does_not() -> None:
    for spec in get_questions().values():
        if spec.scope is QuestionScope.COMPANY:
            assert spec.activity_type is not None
            assert QuestionAnswer.NO in spec.answers
        else:
            assert spec.activity_type is None
            # B65 and B72 ask *which* category. "No" answers nothing.
            assert QuestionAnswer.NO not in spec.answers


def test_only_the_standards_own_relief_is_offered() -> None:
    """Undue cost or effort is a rule in B65 and B72, not a general escape."""
    for spec in get_questions().values():
        if spec.allows_undue_cost_or_effort:
            assert spec.scope is QuestionScope.LINE


def test_every_question_is_asked_in_both_languages() -> None:
    for spec in get_questions().values():
        assert spec.question_text_ko.strip()
        assert spec.question_text_en.strip()
        assert spec.question_text_ko != spec.question_text_en


def test_a_question_carries_its_paragraph() -> None:
    """The help text is where the user learns what the standard actually says."""
    for spec in get_questions().values():
        assert spec.help_en and "IFRS 18" in spec.help_en


def test_an_unknown_key_is_simply_absent() -> None:
    assert get_question("NO_SUCH_QUESTION") is None


# ---------------------------------------------------------------------------
# Malformed catalogs fail at load time, not at review time
# ---------------------------------------------------------------------------


def _document(**overrides: Any) -> dict[str, Any]:
    return {
        "version": "test",
        "questions": [
            {
                "question_key": "SMBA_INVESTING_IN_ASSETS",
                "scope": "COMPANY",
                "activity_type": "INVESTING_IN_ASSETS",
                "question_text_ko": "질문",
                "question_text_en": "question",
                "answers": ["YES", "NO", "NOT_SURE"],
                **overrides,
            }
        ],
    }


def _load(document: dict[str, Any]) -> None:
    parse_questions(document)


def test_an_activity_question_must_be_keyed_by_its_activity() -> None:
    with pytest.raises(QuestionCatalogError, match="key must be"):
        _load(_document(question_key="ASK_ABOUT_INVESTING"))


def test_a_line_question_may_not_claim_an_activity() -> None:
    with pytest.raises(QuestionCatalogError, match="COMPANY question"):
        _load(_document(scope="LINE", question_key="FX_UNDERLYING_ITEM"))


def test_an_unknown_answer_is_refused() -> None:
    with pytest.raises(QuestionCatalogError, match="invalid question"):
        _load(_document(answers=["MAYBE"]))


def test_a_catalog_without_a_version_is_refused() -> None:
    document = _document()
    del document["version"]
    with pytest.raises(QuestionCatalogError, match="no version"):
        _load(document)
