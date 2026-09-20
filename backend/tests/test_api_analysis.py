"""Finalization, the statement, impact and export over HTTP (spec §19, §22, §34).

The governing property here is the validation gate: **a result that did not
reconcile is never presented as a normal one**. Finalization answers `409` with
the failing checks, the statement and impact bodies carry the reconciliation
block, and an export of an unreconciled analysis has to be asked for and comes
back watermarked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ProjectStatus, ReconciliationStatus
from app.models import AuditLog, Export, ImpactAnalysis, Project
from tests.test_api_classification import classification_for, classifications, classified
from tests.test_api_projects import create, sign_up

XLSX_MAGIC = b"PK\x03\x04"


async def reviewed(api: AsyncClient, headers: dict[str, str], data: bytes) -> dict[str, Any]:
    """A project whose every classification a person has looked at.

    The synthetic statement's aggregate captions (기타수익, 금융수익 …) are
    deliberately unclassifiable by rule, so reaching the gate means deciding
    them — which is exactly the review step this product is built around.
    """
    project, _ = await classified(api, headers, data)
    for row in (await classifications(api, headers, project["id"]))["items"]:
        if row["final_ifrs18_category"] == "UNCLASSIFIED":
            payload = {
                "action": "OVERRIDDEN",
                "final_ifrs18_category": "OPERATING",
                "override_reason": "주석 확인 결과 영업 관련",
            }
        elif row["requires_human_review"]:
            payload = {"action": "ACCEPTED"}
        else:
            continue
        response = await api.patch(
            f"/api/projects/{project['id']}/classifications/{row['id']}",
            json=payload,
            headers=headers,
        )
        assert response.status_code == 200, response.text
    return project


async def finalized(
    api: AsyncClient, headers: dict[str, str], data: bytes
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = await reviewed(api, headers, data)
    response = await api.post(f"/api/projects/{project['id']}/finalize", headers=headers)
    assert response.status_code == 200, response.text
    return project, dict(response.json())


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------


async def test_finalizing_is_blocked_by_work_only_a_person_can_do(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await classified(api, headers, statement_bytes)

    response = await api.post(f"/api/projects/{project['id']}/finalize", headers=headers)

    assert response.status_code == 422
    body = response.json()
    assert body["type"].endswith("/not-ready")
    assert "UNREVIEWED_CLASSIFICATION" in {reason["code"] for reason in body["blocking_reasons"]}


async def test_finalizing_an_empty_project_says_nothing_was_extracted(
    api: AsyncClient,
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project = await create(api, headers)

    response = await api.post(f"/api/projects/{project['id']}/finalize", headers=headers)

    assert response.status_code == 422
    assert {reason["code"] for reason in response.json()["blocking_reasons"]} == {
        "NOTHING_EXTRACTED"
    }


async def test_a_reviewed_project_finalizes_and_reconciles(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")

    project, body = await finalized(api, headers, statement_bytes)

    assert body["status"] == "FINALIZED"
    assert body["reconciliation"]["passed"] is True
    assert body["reconciliation"]["blocking_failures"] == []
    assert body["finalized_at"]
    stored = await db_session.get(Project, project["id"])
    assert stored is not None
    await db_session.refresh(stored)
    assert stored.status == ProjectStatus.FINALIZED
    assert stored.reconciliation_status == ReconciliationStatus.PASSED


async def test_total_invariance_is_checked_with_no_tolerance(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """The backbone: IFRS 18 changes presentation, not measurement."""
    headers = await sign_up(api, "owner@example.com")

    _, body = await finalized(api, headers, statement_bytes)

    check = next(
        item for item in body["reconciliation"]["checks"] if item["check"] == "TOTAL_INVARIANCE"
    )
    assert check["passed"] is True
    assert check["tolerance"] == "0.000000"
    assert check["delta"] == "0.000000"
    assert check["expected"] == check["actual"]


async def test_finalizing_stores_a_snapshot(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    """Stored so an export stays reproducible after the rules change."""
    headers = await sign_up(api, "owner@example.com")

    project, body = await finalized(api, headers, statement_bytes)

    snapshot = await db_session.get(ImpactAnalysis, body["impact_analysis_id"])
    assert snapshot is not None
    assert snapshot.is_current is True
    assert snapshot.reconciliation_status == ReconciliationStatus.PASSED
    assert snapshot.rule_set_version
    assert snapshot.config_snapshot is not None
    assert snapshot.kpis["items"]
    assert snapshot.waterfall["balances"] is True
    assert str(snapshot.project_id) == project["id"]


async def test_finalizing_is_audited(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")

    project, _ = await finalized(api, headers, statement_bytes)

    entry = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "FINALIZED", AuditLog.project_id == project["id"]
            )
        )
    ).scalar_one()
    assert entry.after is not None
    assert entry.after["reconciliation_status"] == "PASSED"
    assert entry.after["failed_checks"] == []


async def test_a_finalized_project_cannot_be_finalized_again(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await finalized(api, headers, statement_bytes)

    again = await api.post(f"/api/projects/{project['id']}/finalize", headers=headers)

    assert again.status_code == 409
    assert again.json()["type"].endswith("/project-finalized")


# ---------------------------------------------------------------------------
# The gate refusing
# ---------------------------------------------------------------------------


async def unreconcilable(api: AsyncClient, headers: dict[str, str], data: bytes) -> dict[str, Any]:
    """A project the standard forbids presenting the way we would present it.

    IFRS 18 ¶73: an entity whose main business activity is providing financing
    to customers may not present "profit before financing and income taxes".
    Such entities are outside the MVP's validated scope, so the gate blocks
    rather than emitting a statement with a prohibited subtotal.
    """
    project = await reviewed(api, headers, data)
    response = await api.put(
        f"/api/projects/{project['id']}/business-activities/PROVIDING_FINANCING_TO_CUSTOMERS",
        json={"is_main_business_activity": True, "description": "여신 전업"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return project


async def test_a_failing_gate_refuses_to_finalize(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project = await unreconcilable(api, headers, statement_bytes)
    for row in (await classifications(api, headers, project["id"], requires_review=True))["items"]:
        if row["reviewed_at"] is None:
            await api.patch(
                f"/api/projects/{project['id']}/classifications/{row['id']}",
                json={"action": "ACCEPTED"},
                headers=headers,
            )

    response = await api.post(f"/api/projects/{project['id']}/finalize", headers=headers)

    assert response.status_code == 409
    body = response.json()
    assert body["type"].endswith("/reconciliation-failed")
    assert body["checks"]
    assert any(not check["passed"] for check in body["checks"])


async def test_a_failing_gate_is_recorded_rather_than_rolled_back(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    """The request fails, and the session dependency rolls a failed request
    back — so the failure is committed before the error is raised. A project
    whose reconciliation failed must not look untouched."""
    headers = await sign_up(api, "owner@example.com")
    project = await unreconcilable(api, headers, statement_bytes)
    for row in (await classifications(api, headers, project["id"], requires_review=True))["items"]:
        if row["reviewed_at"] is None:
            await api.patch(
                f"/api/projects/{project['id']}/classifications/{row['id']}",
                json={"action": "ACCEPTED"},
                headers=headers,
            )

    response = await api.post(f"/api/projects/{project['id']}/finalize", headers=headers)

    assert response.status_code == 409
    stored = await db_session.get(Project, project["id"])
    assert stored is not None
    await db_session.refresh(stored)
    assert stored.status == ProjectStatus.RECONCILIATION_FAILED
    assert stored.reconciliation_status == ReconciliationStatus.FAILED
    assert stored.finalized_at is None
    snapshot = await db_session.get(ImpactAnalysis, response.json()["impact_analysis_id"])
    assert snapshot is not None
    assert snapshot.reconciliation_status == ReconciliationStatus.FAILED


# ---------------------------------------------------------------------------
# The statement
# ---------------------------------------------------------------------------


async def test_the_statement_is_built_from_the_decisions_that_stand(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """A human override is what the statement is built from, not the proposal."""
    headers = await sign_up(api, "owner@example.com")
    project = await reviewed(api, headers, statement_bytes)

    body = (await api.get(f"/api/projects/{project['id']}/statement", headers=headers)).json()

    operating = next(s for s in body["sections"] if s["category"] == "OPERATING")
    overridden = [line for line in operating["lines"] if line["user_override"]]
    assert {line["label_ko"] for line in overridden} == {
        "기타수익",
        "금융수익",
        "금융비용",
        "기타비용",
    }
    assert all(line["classification_id"] and line["line_id"] for line in operating["lines"])


async def test_the_statement_carries_its_reconciliation_and_disclaimer(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Spec §19 and §24: a client cannot render these figures without knowing
    whether they reconcile, and the disclaimer comes from the API."""
    headers = await sign_up(api, "owner@example.com")
    project = await reviewed(api, headers, statement_bytes)

    body = (await api.get(f"/api/projects/{project['id']}/statement", headers=headers)).json()

    assert body["reconciliation"]["status"] == "PASSED"
    assert body["reconciliation"]["checks"]
    assert "IFRS 18" in body["disclaimer"]
    assert body["limitations"]


async def test_every_required_subtotal_is_present(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project = await reviewed(api, headers, statement_bytes)

    body = (await api.get(f"/api/projects/{project['id']}/statement", headers=headers)).json()

    keys = [item["key"] for item in body["subtotals"]]
    assert "OPERATING_PROFIT" in keys
    # F9: both are presented even when they are equal.
    assert "PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES" in keys
    assert "PROFIT_BEFORE_TAX" in keys
    assert all(item["presented"] for item in body["subtotals"])


async def test_a_prohibited_subtotal_is_flagged_rather_than_omitted(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """IFRS 18 ¶73. A flag, not an omission, so the reason can be shown."""
    headers = await sign_up(api, "owner@example.com")
    project = await unreconcilable(api, headers, statement_bytes)

    body = (await api.get(f"/api/projects/{project['id']}/statement", headers=headers)).json()

    pbfit = next(
        item
        for item in body["subtotals"]
        if item["key"] == "PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES"
    )
    assert pbfit["presented"] is False
    assert pbfit["suppressed_reason"]
    assert body["reconciliation"]["passed"] is False


async def test_the_statement_needs_a_classification_first(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    from tests.test_api_uploads import extracted

    headers = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, headers, statement_bytes)

    response = await api.get(f"/api/projects/{project['id']}/statement", headers=headers)

    assert response.status_code == 422
    assert response.json()["type"].endswith("/nothing-classified")


# ---------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------


async def test_profit_before_tax_does_not_move(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """The proof the whole product rests on: presentation changed, profit did not."""
    headers = await sign_up(api, "owner@example.com")
    project = await reviewed(api, headers, statement_bytes)

    body = (await api.get(f"/api/projects/{project['id']}/impact", headers=headers)).json()

    by_key = {item["key"]: item for item in body["kpis"]}
    assert by_key["PROFIT_BEFORE_TAX"]["change"] == "0.000000"
    assert by_key["PROFIT_FOR_THE_PERIOD"]["change"] == "0.000000"
    assert by_key["REVENUE"]["change"] == "0.000000"


async def test_operating_profit_moves_and_the_waterfall_explains_it(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project = await reviewed(api, headers, statement_bytes)

    body = (await api.get(f"/api/projects/{project['id']}/impact", headers=headers)).json()

    assert body["waterfall_balances"] is True
    assert body["headline"]["key"] == "OPERATING_PROFIT"
    kinds = [step["kind"] for step in body["waterfall"]]
    assert kinds[0] == "START" and kinds[-1] == "END"
    start = body["waterfall"][0]["value"]
    end = body["waterfall"][-1]["value"]
    assert start != end
    assert body["headline"]["before"] == start
    assert body["headline"]["after"] == end


async def test_a_measure_ifrs18_introduced_has_no_before(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Null, not zero: the entity never reported this figure."""
    headers = await sign_up(api, "owner@example.com")
    project = await reviewed(api, headers, statement_bytes)

    body = (await api.get(f"/api/projects/{project['id']}/impact", headers=headers)).json()

    by_key = {item["key"]: item for item in body["kpis"]}
    assert by_key["INVESTING_RESULT"]["before"] is None
    assert by_key["INVESTING_RESULT"]["change"] is None


async def test_every_movement_can_be_clicked_through(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Spec §5 and §23: navigation, not a second query."""
    headers = await sign_up(api, "owner@example.com")
    project = await reviewed(api, headers, statement_bytes)

    body = (await api.get(f"/api/projects/{project['id']}/impact", headers=headers)).json()

    deltas = [step for step in body["waterfall"] if step["kind"] == "DELTA"]
    assert deltas
    assert all(step["classification_ids"] for step in deltas)
    top = body["top_reclassifications"]
    assert top and all(item["classification_id"] and item["line_id"] for item in top)
    known = {row["id"] for row in (await classifications(api, headers, project["id"]))["items"]}
    assert {item["classification_id"] for item in top} <= known


async def test_impact_amounts_are_strings_at_a_fixed_scale(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """A zero total must read the same as any other figure, or a client
    comparing strings sees a difference that is not there."""
    headers = await sign_up(api, "owner@example.com")
    project = await reviewed(api, headers, statement_bytes)

    statement = (await api.get(f"/api/projects/{project['id']}/statement", headers=headers)).json()

    totals = [section["total"] for section in statement["sections"]]
    assert all(isinstance(total, str) and total.count(".") == 1 for total in totals)
    assert all(len(total.split(".")[1]) == 6 for total in totals)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def test_a_reconciled_project_exports_a_workbook(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await finalized(api, headers, statement_bytes)

    response = await api.get(f"/api/projects/{project['id']}/export/excel", headers=headers)

    assert response.status_code == 200
    assert response.content.startswith(XLSX_MAGIC)
    assert "attachment" in response.headers["content-disposition"]
    assert "X-IFRS18-Reconciliation" not in response.headers
    record = (
        await db_session.execute(select(Export).where(Export.project_id == project["id"]))
    ).scalar_one()
    assert record.is_watermarked_unreconciled is False


async def test_an_unreconciled_export_must_be_acknowledged(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Spec §19: failing output can be taken away to investigate, but never by
    accident."""
    headers = await sign_up(api, "owner@example.com")
    project = await unreconcilable(api, headers, statement_bytes)

    refused = await api.get(f"/api/projects/{project['id']}/export/excel", headers=headers)

    assert refused.status_code == 409
    assert refused.json()["type"].endswith("/reconciliation-failed")
    assert refused.json()["checks"]


async def test_an_acknowledged_unreconciled_export_is_watermarked(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project = await unreconcilable(api, headers, statement_bytes)

    response = await api.get(
        f"/api/projects/{project['id']}/export/excel?acknowledge_unreconciled=true",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.content.startswith(XLSX_MAGIC)
    assert response.headers["X-IFRS18-Reconciliation"] == "FAILED"
    record = (
        await db_session.execute(select(Export).where(Export.project_id == project["id"]))
    ).scalar_one()
    assert record.is_watermarked_unreconciled is True


async def test_exporting_is_audited_and_listed(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    """Taking figures out of the product is itself an event worth recording."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await finalized(api, headers, statement_bytes)
    await api.get(f"/api/projects/{project['id']}/export/excel", headers=headers)

    listed = (await api.get(f"/api/projects/{project['id']}/exports", headers=headers)).json()

    assert [item["format"] for item in listed] == ["XLSX"]
    entry = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "EXPORTED", AuditLog.project_id == project["id"]
            )
        )
    ).scalar_one()
    assert entry.after is not None and entry.after["watermarked"] is False


# ---------------------------------------------------------------------------
# Reopening
# ---------------------------------------------------------------------------


async def test_a_finalized_project_refuses_changes(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """A finalized analysis may already have been sent somewhere."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await finalized(api, headers, statement_bytes)
    row = await classification_for(api, headers, project["id"], "매출액")

    refused = [
        await api.post(f"/api/projects/{project['id']}/classify", json={}, headers=headers),
        await api.patch(
            f"/api/projects/{project['id']}/classifications/{row['id']}",
            json={"action": "ACCEPTED"},
            headers=headers,
        ),
        await api.put(
            f"/api/projects/{project['id']}/business-activities/INVESTING_IN_ASSETS",
            json={"is_main_business_activity": True},
            headers=headers,
        ),
    ]

    assert [response.status_code for response in refused] == [409, 409, 409]
    assert all(r.json()["type"].endswith("/project-finalized") for r in refused)


async def test_reopening_returns_the_project_to_review(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await finalized(api, headers, statement_bytes)

    response = await api.post(f"/api/projects/{project['id']}/reopen", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "IN_REVIEW"
    assert response.json()["finalized_at"] is None
    stored = await db_session.get(Project, project["id"])
    assert stored is not None
    await db_session.refresh(stored)
    assert stored.reconciliation_status == ReconciliationStatus.NOT_RUN
    # And the project can be worked on again.
    again = await api.post(f"/api/projects/{project['id']}/classify", json={}, headers=headers)
    assert again.status_code == 200


async def test_reopening_something_that_is_not_finalized_is_refused(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project = await reviewed(api, headers, statement_bytes)

    response = await api.post(f"/api/projects/{project['id']}/reopen", headers=headers)

    assert response.status_code == 409
    assert response.json()["type"].endswith("/project-not-finalized")


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


async def test_another_user_reaches_none_of_it(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    owner = await sign_up(api, "owner@example.com")
    project, _ = await finalized(api, owner, statement_bytes)
    intruder = await sign_up(api, "intruder@example.com")

    for method, path in [
        ("POST", f"/api/projects/{project['id']}/finalize"),
        ("POST", f"/api/projects/{project['id']}/reopen"),
        ("GET", f"/api/projects/{project['id']}/statement"),
        ("GET", f"/api/projects/{project['id']}/impact"),
        ("GET", f"/api/projects/{project['id']}/export/excel"),
        ("GET", f"/api/projects/{project['id']}/exports"),
        ("GET", f"/api/projects/{project['id']}/audit-logs"),
    ]:
        response = await api.request(method, path, headers=intruder)
        assert response.status_code == 404, f"{method} {path}"


async def test_every_analysis_route_requires_authentication(api: AsyncClient) -> None:
    project_id = "00000000-0000-0000-0000-000000000000"

    for method, path in [
        ("POST", f"/api/projects/{project_id}/finalize"),
        ("GET", f"/api/projects/{project_id}/statement"),
        ("GET", f"/api/projects/{project_id}/impact"),
        ("GET", f"/api/projects/{project_id}/export/excel"),
        ("GET", f"/api/projects/{project_id}/audit-logs"),
    ]:
        response = await api.request(method, path)
        assert response.status_code == 401, f"{method} {path}"


# ---------------------------------------------------------------------------
# The audit trail, read back
# ---------------------------------------------------------------------------


async def test_the_audit_trail_reads_as_a_narrative(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await finalized(api, headers, statement_bytes)

    body = (await api.get(f"/api/projects/{project['id']}/audit-logs", headers=headers)).json()

    actions = [item["action"] for item in body["items"]]
    assert actions[0] == "CREATED"
    assert actions.index("EXTRACTED") < actions.index("CLASSIFIED")
    assert actions.index("CLASSIFIED") < actions.index("FINALIZED")
    assert body["total"] == len(actions)
    assert all(item["actor_type"] in {"USER", "SYSTEM"} for item in body["items"])


async def test_the_audit_trail_can_be_filtered(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await finalized(api, headers, statement_bytes)

    body = (
        await api.get(
            f"/api/projects/{project['id']}/audit-logs?action=OVERRIDDEN", headers=headers
        )
    ).json()

    assert body["items"]
    assert {item["action"] for item in body["items"]} == {"OVERRIDDEN"}
    assert all(item["before"] is not None for item in body["items"])


async def test_an_override_is_recorded_with_both_sides(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Spec §8: what it was, what it became, who decided and when."""
    headers = await sign_up(api, "owner@example.com")
    project = await reviewed(api, headers, statement_bytes)

    body = (
        await api.get(
            f"/api/projects/{project['id']}/audit-logs?action=OVERRIDDEN", headers=headers
        )
    ).json()

    entry = body["items"][0]
    assert entry["before"]["final_ifrs18_category"] == "UNCLASSIFIED"
    assert entry["after"]["final_ifrs18_category"] == "OPERATING"
    assert entry["after"]["reason"]
    assert entry["actor_user_id"]
    assert entry["occurred_at"]
