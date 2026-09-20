"""Classification and human review over HTTP (spec §1, §8).

The property under test throughout is the one the product rests on: **the
engine proposes and a person decides**. A run produces proposals, a review
queue and questions; only a `PATCH` settles anything; and a re-run never
quietly discards what a person settled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ProjectStatus
from app.models import (
    AuditLog,
    FinancialStatementLine,
    Ifrs18Classification,
    Project,
    UserReview,
)
from tests.test_api_projects import create, sign_up
from tests.test_api_uploads import extracted, line_named


async def classified(
    api: AsyncClient, headers: dict[str, str], data: bytes, **payload: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    project, _ = await extracted(api, headers, data)
    response = await api.post(
        f"/api/projects/{project['id']}/classify", json=payload, headers=headers
    )
    assert response.status_code == 200, response.text
    return project, dict(response.json())


async def classifications(
    api: AsyncClient, headers: dict[str, str], project_id: str, **params: Any
) -> dict[str, Any]:
    response = await api.get(
        f"/api/projects/{project_id}/classifications", params=params, headers=headers
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def classification_for(
    api: AsyncClient, headers: dict[str, str], project_id: str, account: str
) -> dict[str, Any]:
    body = await classifications(api, headers, project_id)
    return next(item for item in body["items"] if item["original_account"] == account)


# ---------------------------------------------------------------------------
# Running the engine
# ---------------------------------------------------------------------------


async def test_every_detail_line_is_classified_and_no_subtotal_is(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """A subtotal is a reconciliation target, not a classifiable fact."""
    headers = await sign_up(api, "owner@example.com")

    project, body = await classified(api, headers, statement_bytes)

    lines = (await api.get(f"/api/projects/{project['id']}/lines", headers=headers)).json()
    detail = [item for item in lines["items"] if not item["is_subtotal"]]
    assert body["summary"]["total"] == len(detail)
    items = (await classifications(api, headers, project["id"]))["items"]
    assert {item["original_account"] for item in items} == {item["raw_label"] for item in detail}


async def test_operating_is_the_residual_category(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Revenue is operating because nothing claimed it, not because a rule said so."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)

    revenue = await classification_for(api, headers, project["id"], "매출액")

    assert revenue["final_ifrs18_category"] == "OPERATING"
    assert revenue["classification_method"] == "RESIDUAL_DEFAULT"
    assert revenue["requires_human_review"] is False


async def test_a_rule_decision_carries_its_citation(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Spec §23: the user must be able to read the rule that decided their number."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)

    equity_method = await classification_for(api, headers, project["id"], "지분법이익")

    assert equity_method["final_ifrs18_category"] == "INVESTING"
    assert equity_method["rule_id"] == "IFRS18-INVESTING-001"
    assert equity_method["rule_source_reference"]
    assert [item["evidence_type"] for item in equity_method["evidence"]] == ["RULE_SOURCE"]


async def test_an_aggregate_caption_reaches_a_person(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """One category cannot be right for a bucket holding items from several."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)

    other_income = await classification_for(api, headers, project["id"], "기타수익")

    assert other_income["final_ifrs18_category"] == "UNCLASSIFIED"
    assert other_income["requires_human_review"] is True
    assert other_income["rule_id"] == "IFRS18-AGGREGATE-001"


async def test_classification_puts_the_project_in_review(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")

    project, body = await classified(api, headers, statement_bytes)

    assert body["summary"]["requires_review"] > 0
    stored = await db_session.get(Project, project["id"])
    assert stored is not None
    await db_session.refresh(stored)
    assert stored.status == ProjectStatus.IN_REVIEW
    assert stored.rule_set_version == body["rule_set_version"]


async def test_the_ai_assistant_is_reported_as_unavailable(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """No advisor is wired up, and saying so beats letting a caller assume."""
    headers = await sign_up(api, "owner@example.com")

    _, body = await classified(api, headers, statement_bytes, use_ai_assistant=True)

    assert body["ai_assistant_available"] is False
    assert body["summary"]["by_method"].get("AI") is None


async def test_classifying_before_extraction_is_refused(api: AsyncClient) -> None:
    headers = await sign_up(api, "owner@example.com")
    project = await create(api, headers)

    response = await api.post(f"/api/projects/{project['id']}/classify", json={}, headers=headers)

    assert response.status_code == 422
    assert response.json()["type"].endswith("/nothing-extracted")


async def test_classification_is_audited(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")

    project, body = await classified(api, headers, statement_bytes)

    entry = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "CLASSIFIED", AuditLog.project_id == project["id"]
            )
        )
    ).scalar_one()
    assert entry.after is not None
    assert entry.after["classified"] == body["summary"]["total"]
    assert entry.after["rule_set_version"] == body["rule_set_version"]


# ---------------------------------------------------------------------------
# Re-running
# ---------------------------------------------------------------------------


async def test_re_running_does_not_duplicate_classifications(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, first = await classified(api, headers, statement_bytes)

    again = await api.post(f"/api/projects/{project['id']}/classify", json={}, headers=headers)

    assert again.json()["summary"]["total"] == first["summary"]["total"]
    rows = (
        (
            await db_session.execute(
                select(Ifrs18Classification).where(Ifrs18Classification.project_id == project["id"])
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == first["summary"]["total"]


async def test_re_running_keeps_a_human_decision(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """The guarantee behind `preserve_user_overrides`."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "기타수익")
    await api.patch(
        f"/api/projects/{project['id']}/classifications/{row['id']}",
        json={
            "action": "OVERRIDDEN",
            "final_ifrs18_category": "OPERATING",
            "override_reason": "주석 24 확인 결과 영업 관련 수익",
        },
        headers=headers,
    )

    again = await api.post(f"/api/projects/{project['id']}/classify", json={}, headers=headers)

    assert again.json()["preserved_overrides"] == 1
    after = await classification_for(api, headers, project["id"], "기타수익")
    assert after["final_ifrs18_category"] == "OPERATING"
    assert after["classification_method"] == "USER"
    # The engine's own view is refreshed beside it, so a reviewer can see both.
    assert after["proposed_ifrs18_category"] == "UNCLASSIFIED"


async def test_a_re_run_can_be_told_to_discard_overrides(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Opt-out, never the default: discarding a decision has to be asked for."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "기타수익")
    await api.patch(
        f"/api/projects/{project['id']}/classifications/{row['id']}",
        json={
            "action": "OVERRIDDEN",
            "final_ifrs18_category": "OPERATING",
            "override_reason": "확인함",
        },
        headers=headers,
    )

    again = await api.post(
        f"/api/projects/{project['id']}/classify",
        json={"preserve_user_overrides": False},
        headers=headers,
    )

    assert again.json()["preserved_overrides"] == 0
    after = await classification_for(api, headers, project["id"], "기타수익")
    assert after["final_ifrs18_category"] == "UNCLASSIFIED"


async def test_a_line_corrected_into_a_subtotal_loses_its_classification(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Otherwise the old decision keeps being counted in a statement it left."""
    headers = await sign_up(api, "owner@example.com")
    project, first = await classified(api, headers, statement_bytes)
    line = await line_named(api, headers, project["id"], "기타수익")
    await api.patch(
        f"/api/projects/{project['id']}/lines/{line['id']}",
        json={"is_subtotal": True, "subtotal_kind": "GROSS_PROFIT"},
        headers=headers,
    )

    again = await api.post(f"/api/projects/{project['id']}/classify", json={}, headers=headers)

    assert again.json()["discarded"] == 1
    assert again.json()["summary"]["total"] == first["summary"]["total"] - 1


# ---------------------------------------------------------------------------
# A person decides
# ---------------------------------------------------------------------------


async def test_accepting_a_proposal_is_recorded(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    """ "A person looked at this and agreed" is audit-relevant in itself (spec §8)."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "기타수익")

    response = await api.patch(
        f"/api/projects/{project['id']}/classifications/{row['id']}",
        json={"action": "ACCEPTED"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["reviewed_at"] is not None
    assert response.json()["user_override"] is False
    review = (
        await db_session.execute(
            select(UserReview).where(
                UserReview.action == "ACCEPTED", UserReview.classification_id == row["id"]
            )
        )
    ).scalar_one()
    assert str(review.classification_id) == row["id"]


async def test_an_override_becomes_the_users_decision(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "기타수익")

    response = await api.patch(
        f"/api/projects/{project['id']}/classifications/{row['id']}",
        json={
            "action": "OVERRIDDEN",
            "final_ifrs18_category": "OPERATING",
            "final_ifrs18_subcategory": "OPERATING_OTHER",
            "override_reason": "주석 24 확인",
        },
        headers=headers,
    )

    body = response.json()
    assert body["final_ifrs18_category"] == "OPERATING"
    assert body["classification_method"] == "USER"
    assert body["user_override"] is True
    assert body["override_reason"] == "주석 24 확인"
    assert body["reviewer_user_id"] is not None


async def test_an_override_moves_the_operating_profit_it_should(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """ERD §5: the stored movement follows the category that now stands.

    기타수익 was presented outside the entity's own operating subtotal, so
    calling it operating moves 12,000 into operating profit.
    """
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "기타수익")
    assert row["impact_on_operating_profit"] == "0.000000"

    response = await api.patch(
        f"/api/projects/{project['id']}/classifications/{row['id']}",
        json={
            "action": "OVERRIDDEN",
            "final_ifrs18_category": "OPERATING",
            "override_reason": "영업 관련",
        },
        headers=headers,
    )

    assert response.json()["impact_on_operating_profit"] == "12000.000000"


async def test_an_override_without_a_reason_is_refused(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """An unexplained override is not an audit trail (spec §8)."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "기타수익")

    response = await api.patch(
        f"/api/projects/{project['id']}/classifications/{row['id']}",
        json={"action": "OVERRIDDEN", "final_ifrs18_category": "OPERATING"},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/reason-required")


async def test_an_override_without_a_category_is_refused(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "기타수익")

    response = await api.patch(
        f"/api/projects/{project['id']}/classifications/{row['id']}",
        json={"action": "OVERRIDDEN", "override_reason": "확인함"},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/category-required")


async def test_a_subcategory_from_another_category_is_refused(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Every subcategory belongs to exactly one category; the pair must agree."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "기타수익")

    response = await api.patch(
        f"/api/projects/{project['id']}/classifications/{row['id']}",
        json={
            "action": "OVERRIDDEN",
            "final_ifrs18_category": "OPERATING",
            "final_ifrs18_subcategory": "FINANCING_INCOME",
            "override_reason": "확인함",
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/subcategory-mismatch")


async def test_deferring_leaves_the_item_in_the_queue(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    """Coming back later is not deciding."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "기타수익")

    response = await api.patch(
        f"/api/projects/{project['id']}/classifications/{row['id']}",
        json={"action": "DEFERRED", "override_reason": "주석 확인 필요"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["reviewed_at"] is None
    summary = (await classifications(api, headers, project["id"]))["summary"]
    assert summary["unreviewed"] == summary["requires_review"]
    review = (
        await db_session.execute(
            select(UserReview).where(
                UserReview.action == "DEFERRED", UserReview.classification_id == row["id"]
            )
        )
    ).scalar_one()
    assert str(review.classification_id) == row["id"]


async def test_reviewing_clears_the_finalization_blocker(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """What unblocks a project is that a person decided, not a lowered flag."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    before = (await classifications(api, headers, project["id"]))["summary"]

    for row in (await classifications(api, headers, project["id"], requires_review=True))["items"]:
        await api.patch(
            f"/api/projects/{project['id']}/classifications/{row['id']}",
            json={"action": "ACCEPTED"},
            headers=headers,
        )

    after = (await classifications(api, headers, project["id"]))["summary"]
    assert before["unreviewed"] > 0
    assert after["unreviewed"] == 0
    assert after["requires_review"] == before["requires_review"]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


async def test_the_detail_view_shows_the_rule_and_the_source_cell(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """The drill-down of spec §7."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "지분법이익")

    response = await api.get(
        f"/api/projects/{project['id']}/classifications/{row['id']}", headers=headers
    )

    body = response.json()
    assert body["rule"]["rule_id"] == "IFRS18-INVESTING-001"
    assert "49" in body["rule"]["source_reference"] or "50" in body["rule"]["source_reference"]
    assert body["rule"]["verification_status"]
    assert body["source_locator"]["cell"]
    assert body["raw_label"] == "지분법이익"
    assert body["reviews"] == []


async def test_the_detail_view_shows_the_review_history(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "기타수익")
    await api.patch(
        f"/api/projects/{project['id']}/classifications/{row['id']}",
        json={
            "action": "OVERRIDDEN",
            "final_ifrs18_category": "OPERATING",
            "override_reason": "주석 24 확인",
        },
        headers=headers,
    )

    body = (
        await api.get(f"/api/projects/{project['id']}/classifications/{row['id']}", headers=headers)
    ).json()

    assert [item["action"] for item in body["reviews"]] == ["OVERRIDDEN"]
    assert body["reviews"][0]["previous_category"] == "UNCLASSIFIED"
    assert body["reviews"][0]["new_category"] == "OPERATING"
    assert body["reviews"][0]["reason"] == "주석 24 확인"


async def test_the_review_queue_can_be_filtered_without_changing_the_counts(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """A summary that moved with the filter would tell a reviewer they were done."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)

    everything = await classifications(api, headers, project["id"])
    queue = await classifications(api, headers, project["id"], requires_review=True)

    assert len(queue["items"]) == everything["summary"]["requires_review"]
    assert len(queue["items"]) < len(everything["items"])
    assert queue["summary"] == everything["summary"]


async def test_classifications_can_be_filtered_by_category_and_method(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)

    investing = await classifications(api, headers, project["id"], category="INVESTING")
    residual = await classifications(api, headers, project["id"], method="RESIDUAL_DEFAULT")

    assert [item["original_account"] for item in investing["items"]] == ["지분법이익"]
    assert {item["original_account"] for item in residual["items"]} == {
        "매출액",
        "매출원가",
        "판매비와관리비",
    }


async def test_amounts_and_impacts_cross_the_api_as_strings(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)

    items = (await classifications(api, headers, project["id"]))["items"]

    assert all(isinstance(item["amount"], str) for item in items)
    assert all(
        item["impact_on_operating_profit"] is None
        or isinstance(item["impact_on_operating_profit"], str)
        for item in items
    )


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


async def test_another_user_cannot_classify_or_read_your_project(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    owner = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, owner, statement_bytes)
    row = await classification_for(api, owner, project["id"], "기타수익")
    intruder = await sign_up(api, "intruder@example.com")

    for method, path, body in [
        ("POST", f"/api/projects/{project['id']}/classify", {}),
        ("GET", f"/api/projects/{project['id']}/classifications", None),
        ("GET", f"/api/projects/{project['id']}/classifications/{row['id']}", None),
        (
            "PATCH",
            f"/api/projects/{project['id']}/classifications/{row['id']}",
            {"action": "ACCEPTED"},
        ),
    ]:
        response = await api.request(method, path, json=body, headers=intruder)
        assert response.status_code == 404, f"{method} {path}"


async def test_a_classification_from_another_project_is_not_reachable(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Scoping through the project is what makes the id useless on its own."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "기타수익")
    other = await create(api, headers, name="다른 프로젝트")

    response = await api.get(
        f"/api/projects/{other['id']}/classifications/{row['id']}", headers=headers
    )

    assert response.status_code == 404


async def test_every_classification_route_requires_authentication(api: AsyncClient) -> None:
    project_id = "00000000-0000-0000-0000-000000000000"

    for method, path, body in [
        ("POST", f"/api/projects/{project_id}/classify", {}),
        ("GET", f"/api/projects/{project_id}/classifications", None),
        ("GET", f"/api/projects/{project_id}/classifications/{project_id}", None),
        (
            "PATCH",
            f"/api/projects/{project_id}/classifications/{project_id}",
            {"action": "ACCEPTED"},
        ),
    ]:
        response = await api.request(method, path, json=body)
        assert response.status_code == 401, f"{method} {path}"


# ---------------------------------------------------------------------------
# Normalization is written back onto the lines
# ---------------------------------------------------------------------------


async def test_classification_records_how_each_caption_resolved(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")

    project, _ = await classified(api, headers, statement_bytes)

    lines = (await api.get(f"/api/projects/{project['id']}/lines", headers=headers)).json()
    by_label = {item["raw_label"]: item for item in lines["items"]}
    assert by_label["매출액"]["normalized_account_code"] == "REVENUE"
    assert by_label["매출총이익"]["normalized_account_code"] is None
    stored = await db_session.get(FinancialStatementLine, by_label["매출액"]["id"])
    assert stored is not None
    assert stored.normalization_method in {"EXACT", "SYNONYM", "FUZZY"}


async def test_a_manual_mapping_survives_and_changes_the_decision(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """A correction that changed nothing downstream would be a silent no-op.

    기타수익 is an aggregate the engine refuses to classify. Mapping it by hand
    to a specific account both sticks and moves the line out of the queue.
    """
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)
    line = await line_named(api, headers, project["id"], "기타수익")
    await api.patch(
        f"/api/projects/{project['id']}/lines/{line['id']}",
        json={"normalized_account_code": "SHARE_OF_PROFIT_OF_ASSOCIATES"},
        headers=headers,
    )

    await api.post(f"/api/projects/{project['id']}/classify", json={}, headers=headers)

    row = await classification_for(api, headers, project["id"], "기타수익")
    assert row["normalized_account_code"] == "SHARE_OF_PROFIT_OF_ASSOCIATES"
    assert row["final_ifrs18_category"] == "INVESTING"
    assert row["rule_id"] == "IFRS18-INVESTING-001"
    reread = await line_named(api, headers, project["id"], "기타수익")
    assert reread["normalized_account_code"] == "SHARE_OF_PROFIT_OF_ASSOCIATES"
