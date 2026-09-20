"""Review questions and business activities over HTTP (spec §5, §10).

This is the ``NEEDS_FACT`` path end to end: a rule says which fact it needs,
the user supplies it, and the classification changes accordingly. The three
properties that must hold:

* a question is raised by a **rule**, never by a model;
* **only a person** confirms a main business activity, and an unknown fact is
  never read as "no";
* ``NOT_SURE`` is recorded and still blocks.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, BusinessActivity, Project
from tests.test_api_classification import classification_for, classified
from tests.test_api_projects import create, sign_up


async def questions(api: AsyncClient, headers: dict[str, str], project_id: str) -> dict[str, Any]:
    response = await api.get(f"/api/projects/{project_id}/questions", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def question_for(
    api: AsyncClient, headers: dict[str, str], project_id: str, key: str
) -> dict[str, Any]:
    body = await questions(api, headers, project_id)
    return next(item for item in body["items"] if item["question_key"] == key)


async def answer(
    api: AsyncClient,
    headers: dict[str, str],
    project_id: str,
    question_id: str,
    **payload: Any,
) -> Any:
    return await api.post(
        f"/api/projects/{project_id}/questions/{question_id}/answer",
        json=payload,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Questions are raised by rules
# ---------------------------------------------------------------------------


async def test_a_rule_that_needs_a_fact_raises_a_question(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")

    project, body = await classified(api, headers, fact_statement_bytes)

    raised = {item["question_key"]: item for item in body["questions"]}
    assert set(raised) == {
        "SMBA_INVESTING_IN_ASSETS",
        "FX_UNDERLYING_ITEM",
        "DERIVATIVE_RISK_MANAGED",
    }
    assert raised["SMBA_INVESTING_IN_ASSETS"]["raised_by_rule_id"] == "IFRS18-INVESTING-002"
    assert raised["FX_UNDERLYING_ITEM"]["raised_by_rule_id"] == "IFRS18-FX-001"
    assert body["summary"]["open_questions"] == 3
    assert project["id"]


async def test_an_entity_question_is_asked_once_and_a_line_question_per_line(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    """Q4: a main business activity settles the entity; B72 settles one instrument."""
    headers = await sign_up(api, "owner@example.com")

    _, body = await classified(api, headers, fact_statement_bytes)

    by_key = {item["question_key"]: item for item in body["questions"]}
    assert by_key["SMBA_INVESTING_IN_ASSETS"]["scope"] == "COMPANY"
    assert by_key["SMBA_INVESTING_IN_ASSETS"]["line_id"] is None
    assert by_key["DERIVATIVE_RISK_MANAGED"]["scope"] == "LINE"
    assert by_key["DERIVATIVE_RISK_MANAGED"]["line_id"] is not None


async def test_a_question_says_what_turns_on_it(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    """The user should know the stakes before answering, not after."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)

    question = await question_for(api, headers, project["id"], "SMBA_INVESTING_IN_ASSETS")

    assert question["affected_line_count"] == 1
    assert question["affected_amount"] == "6000.000000"
    assert question["question_text_ko"]
    assert question["help_ko"]
    assert question["options"] == ["YES", "NO", "NOT_SURE"]


async def test_a_per_line_question_cannot_be_answered_no(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    """ "No" answers nothing here: B72 asks *which* category, not whether."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    question = await question_for(api, headers, project["id"], "DERIVATIVE_RISK_MANAGED")

    assert question["options"] == ["YES", "NOT_SURE"]
    assert question["allows_undue_cost_or_effort"] is True
    response = await answer(api, headers, project["id"], question["id"], answer="NO")
    assert response.status_code == 422
    assert response.json()["type"].endswith("/unanswerable")


# ---------------------------------------------------------------------------
# Answering changes the classification
# ---------------------------------------------------------------------------


async def test_confirming_a_main_business_activity_changes_the_category(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    """IFRS 18 paragraphs 49-50: interest income is operating for an entity that
    invests in assets as a main business activity, and investing otherwise."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    question = await question_for(api, headers, project["id"], "SMBA_INVESTING_IN_ASSETS")

    response = await answer(
        api, headers, project["id"], question["id"], answer="YES", note="2025 사업보고서 II-1"
    )

    assert response.status_code == 200, response.text
    interest = await classification_for(api, headers, project["id"], "이자수익")
    assert interest["final_ifrs18_category"] == "OPERATING"
    assert interest["rule_id"] == "IFRS18-INVESTING-002"


async def test_denying_it_classifies_the_other_way(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    question = await question_for(api, headers, project["id"], "SMBA_INVESTING_IN_ASSETS")

    await answer(api, headers, project["id"], question["id"], answer="NO")

    interest = await classification_for(api, headers, project["id"], "이자수익")
    assert interest["final_ifrs18_category"] == "INVESTING"
    assert interest["proposed_ifrs18_subcategory"] == "INVESTING_INCOME"


async def test_answering_an_entity_question_records_the_activity(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes, db_session: AsyncSession
) -> None:
    """The answer is not just stored on the question: it becomes the entity fact."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    question = await question_for(api, headers, project["id"], "SMBA_INVESTING_IN_ASSETS")

    await answer(api, headers, project["id"], question["id"], answer="YES")

    activities = (
        await api.get(f"/api/projects/{project['id']}/business-activities", headers=headers)
    ).json()
    assert [item["activity_type"] for item in activities["items"]] == ["INVESTING_IN_ASSETS"]
    assert activities["items"][0]["is_main_business_activity"] is True
    assert activities["items"][0]["confirmed_by_user"] is True
    assert activities["items"][0]["is_specified"] is True
    entry = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "QUESTION_ANSWERED",
                AuditLog.project_id == project["id"],
            )
        )
    ).scalar_one()
    assert entry.after is not None and entry.after["answer"] == "YES"


async def test_not_sure_is_stored_and_still_blocks(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    """Spec §5: being asked and not knowing is worth recording, and is not a fact."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    question = await question_for(api, headers, project["id"], "SMBA_INVESTING_IN_ASSETS")

    response = await answer(api, headers, project["id"], question["id"], answer="NOT_SURE")

    assert response.status_code == 200, response.text
    assert response.json()["question"]["answer"] == "NOT_SURE"
    assert response.json()["question"]["is_resolved"] is False
    still_open = await questions(api, headers, project["id"])
    assert still_open["open_count"] == 3
    interest = await classification_for(api, headers, project["id"], "이자수익")
    assert interest["final_ifrs18_category"] == "UNCLASSIFIED"


async def test_a_per_line_answer_settles_that_line(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    """B72: the derivative follows the risk it manages, once we are told which."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    question = await question_for(api, headers, project["id"], "DERIVATIVE_RISK_MANAGED")

    response = await answer(
        api,
        headers,
        project["id"],
        question["id"],
        answer="YES",
        resolved_category="FINANCING",
        resolved_subcategory="FINANCING_INCOME",
        note="이자율스왑 — 차입금 이자율 위험",
    )

    assert response.status_code == 200, response.text
    derivative = await classification_for(api, headers, project["id"], "파생상품평가이익")
    assert derivative["final_ifrs18_category"] == "FINANCING"
    assert derivative["rule_id"] == "IFRS18-DERIV-001"
    assert derivative["requires_human_review"] is False


async def test_undue_cost_or_effort_sends_the_line_to_operating(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    """B65's own relief, and B72's: an answer with a destination, not a shrug."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    question = await question_for(api, headers, project["id"], "FX_UNDERLYING_ITEM")

    response = await answer(
        api,
        headers,
        project["id"],
        question["id"],
        answer="YES",
        undue_cost_or_effort=True,
        note="원인 항목 추적에 과도한 노력이 소요됨",
    )

    assert response.status_code == 200, response.text
    fx = await classification_for(api, headers, project["id"], "외환차익")
    assert fx["final_ifrs18_category"] == "OPERATING"
    assert fx["rule_id"] == "IFRS18-FX-001"


async def test_a_per_line_answer_needs_a_category_or_the_relief(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    question = await question_for(api, headers, project["id"], "FX_UNDERLYING_ITEM")

    response = await answer(api, headers, project["id"], question["id"], answer="YES")

    assert response.status_code == 422
    assert response.json()["type"].endswith("/category-required")


async def test_an_entity_question_cannot_carry_a_category(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    """It settles what the entity does, not where one line belongs."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    question = await question_for(api, headers, project["id"], "SMBA_INVESTING_IN_ASSETS")

    response = await answer(
        api, headers, project["id"], question["id"], answer="YES", resolved_category="FINANCING"
    )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/wrong-answer-shape")


async def test_a_mismatched_subcategory_is_refused(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    question = await question_for(api, headers, project["id"], "FX_UNDERLYING_ITEM")

    response = await answer(
        api,
        headers,
        project["id"],
        question["id"],
        answer="YES",
        resolved_category="OPERATING",
        resolved_subcategory="INVESTING_INCOME",
    )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/subcategory-mismatch")


async def test_an_answered_question_is_not_asked_again(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    """Re-running must find the answer, not raise a duplicate question."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    question = await question_for(api, headers, project["id"], "SMBA_INVESTING_IN_ASSETS")
    await answer(api, headers, project["id"], question["id"], answer="YES")

    await api.post(f"/api/projects/{project['id']}/classify", json={}, headers=headers)

    body = await questions(api, headers, project["id"])
    keys = [item["question_key"] for item in body["items"]]
    assert keys.count("SMBA_INVESTING_IN_ASSETS") == 1
    assert next(
        item for item in body["items"] if item["question_key"] == "SMBA_INVESTING_IN_ASSETS"
    )["is_resolved"]


# ---------------------------------------------------------------------------
# Business activities
# ---------------------------------------------------------------------------


async def test_nothing_is_assumed_about_a_new_company(api: AsyncClient) -> None:
    """Spec §10: an activity nobody confirmed simply is not there."""
    headers = await sign_up(api, "owner@example.com")
    project = await create(api, headers)

    body = (
        await api.get(f"/api/projects/{project['id']}/business-activities", headers=headers)
    ).json()

    assert body["items"] == []


async def test_declaring_an_activity_reclassifies_the_project(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)

    response = await api.put(
        f"/api/projects/{project['id']}/business-activities/INVESTING_IN_ASSETS",
        json={"is_main_business_activity": True, "description": "투자자산 운용이 주된 사업"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["confirmed_by_user"] is True
    interest = await classification_for(api, headers, project["id"], "이자수익")
    assert interest["final_ifrs18_category"] == "OPERATING"


async def test_a_confirmation_can_be_withdrawn_back_to_unknown(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    """Null is a real answer: a user who no longer stands behind a confirmation
    must not be forced to assert the opposite."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    await api.put(
        f"/api/projects/{project['id']}/business-activities/INVESTING_IN_ASSETS",
        json={"is_main_business_activity": True},
        headers=headers,
    )

    response = await api.put(
        f"/api/projects/{project['id']}/business-activities/INVESTING_IN_ASSETS",
        json={"is_main_business_activity": None},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_main_business_activity"] is None
    assert response.json()["confirmed_by_user"] is False
    # Unknown blocks again, which is the whole point of the three-valued fact.
    interest = await classification_for(api, headers, project["id"], "이자수익")
    assert interest["final_ifrs18_category"] == "UNCLASSIFIED"
    assert (await questions(api, headers, project["id"]))["open_count"] == 3


async def test_an_ai_suggestion_never_unblocks_a_rule(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes, db_session: AsyncSession
) -> None:
    """Spec §10: an AI row may exist, but only a person confirms.

    The row below says the activity *is* a main business activity. Because no
    person confirmed it, the engine must still read the fact as unknown.
    """
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    stored = await db_session.get(Project, project["id"])
    assert stored is not None
    db_session.add(
        BusinessActivity(
            company_id=stored.company_id,
            project_id=stored.id,
            activity_type="INVESTING_IN_ASSETS",
            is_main_business_activity=True,
            source="AI_SUGGESTED",
            confirmed_by_user=False,
        )
    )
    await db_session.flush()

    await api.post(f"/api/projects/{project['id']}/classify", json={}, headers=headers)

    interest = await classification_for(api, headers, project["id"], "이자수익")
    assert interest["final_ifrs18_category"] == "UNCLASSIFIED"
    assert (await questions(api, headers, project["id"]))["open_count"] == 3


async def test_an_ai_row_cannot_be_stored_as_confirmed(
    api: AsyncClient, db_session: AsyncSession
) -> None:
    """Belt and braces: the database refuses it too."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    headers = await sign_up(api, "owner@example.com")
    project = await create(api, headers)
    stored = await db_session.get(Project, project["id"])
    assert stored is not None

    db_session.add(
        BusinessActivity(
            company_id=stored.company_id,
            project_id=stored.id,
            activity_type="INVESTING_IN_ASSETS",
            is_main_business_activity=True,
            source="AI_SUGGESTED",
            confirmed_by_user=True,
            confirmed_at=dt.datetime.now(dt.UTC),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


async def test_another_user_cannot_see_or_answer_your_questions(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    owner = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, owner, fact_statement_bytes)
    question = await question_for(api, owner, project["id"], "SMBA_INVESTING_IN_ASSETS")
    intruder = await sign_up(api, "intruder@example.com")

    listed = await api.get(f"/api/projects/{project['id']}/questions", headers=intruder)
    answered = await answer(api, intruder, project["id"], question["id"], answer="YES")
    activities = await api.get(
        f"/api/projects/{project['id']}/business-activities", headers=intruder
    )

    assert listed.status_code == answered.status_code == activities.status_code == 404


async def test_a_question_from_another_project_is_not_reachable(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    question = await question_for(api, headers, project["id"], "SMBA_INVESTING_IN_ASSETS")
    other = await create(api, headers, name="다른 프로젝트")

    response = await answer(api, headers, other["id"], question["id"], answer="YES")

    assert response.status_code == 404


async def test_every_review_route_requires_authentication(api: AsyncClient) -> None:
    project_id = "00000000-0000-0000-0000-000000000000"

    for method, path, body in [
        ("GET", f"/api/projects/{project_id}/questions", None),
        ("POST", f"/api/projects/{project_id}/questions/{project_id}/answer", {"answer": "YES"}),
        ("GET", f"/api/projects/{project_id}/business-activities", None),
        (
            "PUT",
            f"/api/projects/{project_id}/business-activities/INVESTING_IN_ASSETS",
            {"is_main_business_activity": True},
        ),
    ]:
        response = await api.request(method, path, json=body)
        assert response.status_code == 401, f"{method} {path}"


# ---------------------------------------------------------------------------
# Deciding a line by hand, instead of answering
# ---------------------------------------------------------------------------


async def test_classifying_a_blocked_line_by_hand_stops_its_question_blocking(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    """A per-line question exists to decide that one line. Deciding it directly
    answers the same need, so the question stops blocking — but it is left
    *unanswered*, because nobody answered it."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    row = await classification_for(api, headers, project["id"], "외환차익")
    assert row["blocked_on_question_id"] is not None

    response = await api.patch(
        f"/api/projects/{project['id']}/classifications/{row['id']}",
        json={
            "action": "OVERRIDDEN",
            "final_ifrs18_category": "FINANCING",
            "override_reason": "차입금에서 발생한 외환차이",
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    question = await question_for(api, headers, project["id"], "FX_UNDERLYING_ITEM")
    assert question["blocks_finalization"] is False
    assert question["answer"] is None
    assert (await questions(api, headers, project["id"]))["open_count"] == 2


async def test_deciding_one_line_does_not_settle_an_entity_question(
    api: AsyncClient, uploads_dir: Path, fact_statement_bytes: bytes
) -> None:
    """A main business activity is a fact about the entity: other lines still
    depend on it, so overriding one of them settles nothing."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, fact_statement_bytes)
    row = await classification_for(api, headers, project["id"], "이자수익")

    await api.patch(
        f"/api/projects/{project['id']}/classifications/{row['id']}",
        json={
            "action": "OVERRIDDEN",
            "final_ifrs18_category": "INVESTING",
            "override_reason": "현금성자산 이자",
        },
        headers=headers,
    )

    question = await question_for(api, headers, project["id"], "SMBA_INVESTING_IN_ASSETS")
    assert question["blocks_finalization"] is True
    assert question["answer"] is None
