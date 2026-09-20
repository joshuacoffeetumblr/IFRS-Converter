"""Upload, extraction and line correction over HTTP (API spec §2).

`test_upload_security.py` covers the storage layer's own defences. What is
tested here is the endpoint contract built on top of it: that a rejection
reaches the client as a problem document rather than a stack trace, that a
retried upload does not produce a second record, that extraction is verified
before it is stored, and that correcting a figure is audited.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
from pathlib import Path
from typing import Any

from httpx import AsyncClient
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.enums import ProjectStatus, ReconciliationStatus
from app.models import AuditLog, FinancialStatement, Project, UploadedFile
from tests.fixtures.korean_income_statement import (
    STATEMENT_ROWS,
    SignStyle,
    build_csv,
    build_workbook,
)
from tests.test_api_projects import create, sign_up

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def stored_files(directory: Path) -> list[Path]:
    return [path for path in directory.glob("*") if path.is_file()] if directory.exists() else []


async def post_file(
    api: AsyncClient,
    headers: dict[str, str],
    project_id: str,
    data: bytes,
    *,
    filename: str = "손익계산서.xlsx",
) -> Any:
    # The declared content type is deliberately wrong: it must not be believed.
    return await api.post(
        f"/api/projects/{project_id}/upload",
        files={"file": (filename, data, "application/octet-stream")},
        headers=headers,
    )


async def uploaded(
    api: AsyncClient, headers: dict[str, str], data: bytes, **kwargs: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A project with one file attached."""
    project = await create(api, headers)
    response = await post_file(api, headers, project["id"], data, **kwargs)
    assert response.status_code == 201, response.text
    return project, dict(response.json())


async def extracted(
    api: AsyncClient, headers: dict[str, str], data: bytes
) -> tuple[dict[str, Any], dict[str, Any]]:
    project, _ = await uploaded(api, headers, data)
    response = await api.post(f"/api/projects/{project['id']}/extract", json={}, headers=headers)
    assert response.status_code == 200, response.text
    return project, dict(response.json())


def empty_workbook_bytes() -> bytes:
    """A real XLSX with nothing that could be read as an income statement."""
    buffer = io.BytesIO()
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet["A1"] = "표지"
    book.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


async def test_an_upload_is_recorded_with_its_digest(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")

    _, body = await uploaded(api, headers, statement_bytes)

    assert body["sha256"] == hashlib.sha256(statement_bytes).hexdigest()
    assert body["size_bytes"] == len(statement_bytes)
    assert body["mime_type"] == XLSX_MIME
    assert body["parse_status"] == "PENDING"
    assert len(stored_files(uploads_dir)) == 1


async def test_the_scan_status_is_skipped_rather_than_clean(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """No scanner is wired up. Reporting CLEAN would be a claim we cannot support."""
    headers = await sign_up(api, "owner@example.com")

    _, body = await uploaded(api, headers, statement_bytes)

    assert body["scan_status"] == "SKIPPED"


async def test_uploading_advances_the_project(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")

    project, _ = await uploaded(api, headers, statement_bytes)

    stored = await db_session.get(Project, project["id"])
    assert stored is not None
    assert stored.status == ProjectStatus.UPLOADED


async def test_uploading_is_audited(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")

    project, body = await uploaded(api, headers, statement_bytes)

    entries = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "uploaded_files",
                    AuditLog.project_id == project["id"],
                )
            )
        )
        .scalars()
        .all()
    )
    assert [str(entry.entity_id) for entry in entries] == [body["id"]]
    assert str(entries[0].project_id) == project["id"]
    assert entries[0].after == {"filename": "손익계산서.xlsx", "sha256": body["sha256"]}


async def test_the_same_file_twice_is_one_record(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    """A retried upload must not leave two candidates for extraction to choose between."""
    headers = await sign_up(api, "owner@example.com")
    project, first = await uploaded(api, headers, statement_bytes)

    again = await post_file(api, headers, project["id"], statement_bytes)

    assert again.status_code == 201
    assert again.json()["id"] == first["id"]
    count = await db_session.scalar(
        select(func.count())
        .select_from(UploadedFile)
        .where(UploadedFile.project_id == project["id"])
    )
    assert count == 1
    # The duplicate's bytes are removed rather than orphaned on disk.
    assert len(stored_files(uploads_dir)) == 1


async def test_the_same_file_in_another_project_is_its_own_record(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Deduplication is scoped to a project; two projects are two analyses."""
    headers = await sign_up(api, "owner@example.com")
    _, first = await uploaded(api, headers, statement_bytes)

    _, second = await uploaded(api, headers, statement_bytes)

    assert first["id"] != second["id"]
    assert first["sha256"] == second["sha256"]


# ---------------------------------------------------------------------------
# Rejection reaches the client as a problem document
# ---------------------------------------------------------------------------


async def test_a_disguised_executable_is_refused(
    api: AsyncClient, uploads_dir: Path, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project = await create(api, headers)

    response = await post_file(api, headers, project["id"], b"MZ\x90\x00" + b"\x00" * 512)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"].endswith("/unsupported-media-type")
    assert stored_files(uploads_dir) == []
    count = await db_session.scalar(
        select(func.count())
        .select_from(UploadedFile)
        .where(UploadedFile.project_id == project["id"])
    )
    assert count == 0


async def test_an_oversize_file_is_refused(
    api: AsyncClient, uploads_dir: Path, settings: Settings, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project = await create(api, headers)
    original = settings.upload.max_bytes
    settings.upload.max_bytes = 1024
    try:
        response = await post_file(api, headers, project["id"], statement_bytes)
    finally:
        settings.upload.max_bytes = original

    assert response.status_code == 422
    assert response.json()["type"].endswith("/file-too-large")
    assert stored_files(uploads_dir) == []


async def test_an_empty_file_is_refused(api: AsyncClient, uploads_dir: Path) -> None:
    headers = await sign_up(api, "owner@example.com")
    project = await create(api, headers)

    response = await post_file(api, headers, project["id"], b"")

    assert response.status_code == 422
    assert response.json()["type"].endswith("/empty-file")


async def test_a_traversing_filename_is_kept_for_display_only(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")

    _, body = await uploaded(api, headers, statement_bytes, filename="../../../etc/passwd.xlsx")

    assert body["original_filename"] == "passwd.xlsx"
    written = stored_files(uploads_dir)
    assert len(written) == 1
    assert written[0].parent == uploads_dir


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


async def test_another_user_cannot_upload_to_your_project(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    owner = await sign_up(api, "owner@example.com")
    project = await create(api, owner)
    intruder = await sign_up(api, "intruder@example.com")

    response = await post_file(api, intruder, project["id"], statement_bytes)

    assert response.status_code == 404
    assert stored_files(uploads_dir) == []


async def test_another_user_cannot_read_your_lines(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    owner = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, owner, statement_bytes)
    intruder = await sign_up(api, "intruder@example.com")

    response = await api.get(f"/api/projects/{project['id']}/lines", headers=intruder)

    assert response.status_code == 404


async def test_every_statement_route_requires_authentication(api: AsyncClient) -> None:
    project_id = "00000000-0000-0000-0000-000000000000"
    line_id = project_id

    for method, path, body in [
        ("POST", f"/api/projects/{project_id}/extract", {}),
        ("GET", f"/api/projects/{project_id}/statements", None),
        ("GET", f"/api/projects/{project_id}/lines", None),
        ("PATCH", f"/api/projects/{project_id}/lines/{line_id}", {"raw_label": "x"}),
    ]:
        response = await api.request(method, path, json=body)
        assert response.status_code == 401, f"{method} {path}"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


async def test_extraction_reads_every_line_and_reconciles(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")

    _, body = await extracted(api, headers, statement_bytes)

    assert body["line_count"] == len(STATEMENT_ROWS)
    assert body["report"]["passed"] is True
    assert body["report"]["blockers"] == []
    assert body["report"]["checks"] != []


async def test_extraction_without_an_upload_is_refused(api: AsyncClient) -> None:
    headers = await sign_up(api, "owner@example.com")
    project = await create(api, headers)

    response = await api.post(f"/api/projects/{project['id']}/extract", json={}, headers=headers)

    assert response.status_code == 422
    assert response.json()["type"].endswith("/no-upload")


async def test_an_unreadable_file_leaves_the_failure_recorded(
    api: AsyncClient, uploads_dir: Path, db_session: AsyncSession
) -> None:
    """A project whose extraction failed must not look untouched afterwards.

    The request fails, and the session dependency rolls a failed request back —
    so the router commits the failure bookkeeping before raising.
    """
    headers = await sign_up(api, "owner@example.com")
    project, upload = await uploaded(api, headers, empty_workbook_bytes())

    response = await api.post(f"/api/projects/{project['id']}/extract", json={}, headers=headers)

    assert response.status_code == 422
    assert response.json()["type"].endswith("/statement-not-found")
    stored_project = await db_session.get(Project, project["id"])
    stored_upload = await db_session.get(UploadedFile, upload["id"])
    assert stored_project is not None and stored_upload is not None
    await db_session.refresh(stored_project)
    await db_session.refresh(stored_upload)
    assert stored_project.status == ProjectStatus.EXTRACTION_FAILED
    assert stored_upload.parse_status == "FAILED"
    assert stored_upload.parse_error


async def test_a_pdf_is_stored_but_cannot_be_extracted_yet(
    api: AsyncClient, uploads_dir: Path
) -> None:
    """PDF ingest is out of MVP scope, and the endpoint says so rather than half-reading it."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await uploaded(api, headers, b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", filename="fs.pdf")

    response = await api.post(f"/api/projects/{project['id']}/extract", json={}, headers=headers)

    assert response.status_code == 422
    assert response.json()["type"].endswith("/unsupported-for-extraction")


async def test_a_csv_statement_is_extracted_too(
    api: AsyncClient, uploads_dir: Path, tmp_path: Path
) -> None:
    headers = await sign_up(api, "owner@example.com")
    data = build_csv(tmp_path / "source.csv").read_bytes()

    project, _ = await uploaded(api, headers, data, filename="손익계산서.csv")
    response = await api.post(f"/api/projects/{project['id']}/extract", json={}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["report"]["passed"] is True


async def test_an_unsigned_statement_reports_that_signs_were_inferred(
    api: AsyncClient, uploads_dir: Path, tmp_path: Path
) -> None:
    """Spec §17: a derived sign is a fact about the reading, so the client is told."""
    headers = await sign_up(api, "owner@example.com")
    data = build_workbook(tmp_path / "unsigned.xlsx", style=SignStyle.UNSIGNED).read_bytes()

    _, body = await extracted(api, headers, data)

    assert body["report"]["signs_inferred"] is True
    assert body["report"]["passed"] is True


async def test_re_extraction_replaces_the_previous_statement(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    """Leaving the old lines behind would double count them."""
    headers = await sign_up(api, "owner@example.com")
    project, first = await extracted(api, headers, statement_bytes)

    again = await api.post(f"/api/projects/{project['id']}/extract", json={}, headers=headers)

    assert again.status_code == 200
    assert again.json()["statement"]["id"] != first["statement"]["id"]
    statements = await api.get(f"/api/projects/{project['id']}/statements", headers=headers)
    assert len(statements.json()) == 1
    count = await db_session.scalar(
        select(func.count())
        .select_from(FinancialStatement)
        .where(FinancialStatement.project_id == project["id"])
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Lines
# ---------------------------------------------------------------------------


async def test_lines_carry_their_source_cell(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Spec §18: a figure on screen can always be traced back to the document."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, headers, statement_bytes)

    body = (await api.get(f"/api/projects/{project['id']}/lines", headers=headers)).json()

    locator = body["items"][0]["source_locator"]
    assert locator["sheet"] == "손익계산서"
    assert locator["row"] and locator["column"] and locator["cell"]
    assert all(item["source_locator"]["cell"] for item in body["items"])


async def test_amounts_cross_the_api_as_strings(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """A JSON number is a double, and this product's whole claim is exactness."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, headers, statement_bytes)

    body = (await api.get(f"/api/projects/{project['id']}/lines", headers=headers)).json()

    assert all(isinstance(item["amount"], str) for item in body["items"])
    by_label = {item["raw_label"]: item["amount"] for item in body["items"]}
    assert by_label["매출액"] == "1000000.000000"
    assert by_label["매출원가"] == "-700000.000000"


async def test_lines_report_the_reconciliation_that_verified_them(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, headers, statement_bytes)

    body = (await api.get(f"/api/projects/{project['id']}/lines", headers=headers)).json()

    assert body["report"]["passed"] is True
    assert {check["check"] for check in body["report"]["checks"]}


async def test_lines_before_extraction_are_empty(api: AsyncClient) -> None:
    headers = await sign_up(api, "owner@example.com")
    project = await create(api, headers)

    response = await api.get(f"/api/projects/{project['id']}/lines", headers=headers)

    assert response.status_code == 200
    assert response.json()["items"] == []


# ---------------------------------------------------------------------------
# Correcting a misread cell
# ---------------------------------------------------------------------------


async def line_named(api: AsyncClient, headers: dict[str, str], project_id: str, label: str) -> Any:
    body = (await api.get(f"/api/projects/{project_id}/lines", headers=headers)).json()
    return next(item for item in body["items"] if item["raw_label"] == label)


async def test_correcting_an_amount_is_stored_and_audited(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, headers, statement_bytes)
    line = await line_named(api, headers, project["id"], "기타수익")

    response = await api.patch(
        f"/api/projects/{project['id']}/lines/{line['id']}",
        json={"amount": "12500"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["amount"] == "12500.000000"
    entry = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.entity_type == "financial_statement_lines")
        )
    ).scalar_one()
    assert entry.before is not None and entry.after is not None
    assert entry.before["amount"] == "12000.000000"
    # Recorded as stored, so before and after are comparable strings.
    assert entry.after["amount"] == "12500.000000"
    assert entry.before["raw_label"] == entry.after["raw_label"] == "기타수익"


async def test_a_correction_reopens_the_review(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    """A classification decided against the old figure is no longer a decision
    about this statement, so the project returns to EXTRACTED."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, headers, statement_bytes)
    line = await line_named(api, headers, project["id"], "기타수익")
    stored = await db_session.get(Project, project["id"])
    assert stored is not None
    stored.status = ProjectStatus.CLASSIFIED
    await db_session.flush()

    await api.patch(
        f"/api/projects/{project['id']}/lines/{line['id']}",
        json={"amount": "12500"},
        headers=headers,
    )

    await db_session.refresh(stored)
    assert stored.status == ProjectStatus.EXTRACTED


async def test_a_finalized_project_refuses_corrections(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes, db_session: AsyncSession
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, headers, statement_bytes)
    line = await line_named(api, headers, project["id"], "기타수익")
    stored = await db_session.get(Project, project["id"])
    assert stored is not None
    stored.reconciliation_status = ReconciliationStatus.PASSED
    stored.finalized_at = dt.datetime.now(dt.UTC)
    stored.status = ProjectStatus.FINALIZED
    await db_session.flush()

    response = await api.patch(
        f"/api/projects/{project['id']}/lines/{line['id']}",
        json={"amount": "12500"},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/project-finalized")


async def test_another_users_line_cannot_be_corrected(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    owner = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, owner, statement_bytes)
    line = await line_named(api, owner, project["id"], "기타수익")
    intruder = await sign_up(api, "intruder@example.com")
    intruders_project = await create(api, intruder)

    through_own_project = await api.patch(
        f"/api/projects/{intruders_project['id']}/lines/{line['id']}",
        json={"amount": "1"},
        headers=intruder,
    )
    through_owners_project = await api.patch(
        f"/api/projects/{project['id']}/lines/{line['id']}",
        json={"amount": "1"},
        headers=intruder,
    )

    assert through_own_project.status_code == 404
    assert through_owners_project.status_code == 404
    unchanged = await line_named(api, owner, project["id"], "기타수익")
    assert unchanged["amount"] == "12000.000000"


# ---------------------------------------------------------------------------
# Mapping a line onto the account dictionary
# ---------------------------------------------------------------------------


async def test_a_line_can_be_mapped_to_an_account(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, headers, statement_bytes)
    line = await line_named(api, headers, project["id"], "기타수익")

    response = await api.patch(
        f"/api/projects/{project['id']}/lines/{line['id']}",
        json={"normalized_account_code": "INTEREST_INCOME"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["normalized_account_code"] == "INTEREST_INCOME"
    reread = await line_named(api, headers, project["id"], "기타수익")
    assert reread["normalized_account_code"] == "INTEREST_INCOME"


async def test_a_mapping_can_be_cleared(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    headers = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, headers, statement_bytes)
    line = await line_named(api, headers, project["id"], "기타수익")
    await api.patch(
        f"/api/projects/{project['id']}/lines/{line['id']}",
        json={"normalized_account_code": "INTEREST_INCOME"},
        headers=headers,
    )

    response = await api.patch(
        f"/api/projects/{project['id']}/lines/{line['id']}",
        json={"normalized_account_code": None},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["normalized_account_code"] is None


async def test_an_unknown_account_code_is_refused(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """A mapping to an account that does not exist would survive review looking
    like a decision."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, headers, statement_bytes)
    line = await line_named(api, headers, project["id"], "기타수익")

    response = await api.patch(
        f"/api/projects/{project['id']}/lines/{line['id']}",
        json={"normalized_account_code": "NO_SUCH_ACCOUNT"},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/unknown-account")
    reread = await line_named(api, headers, project["id"], "기타수익")
    assert reread["normalized_account_code"] is None


async def test_a_subtotal_cannot_be_given_an_account(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """A subtotal is a reconciliation target, never a classifiable fact."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, headers, statement_bytes)
    subtotal = await line_named(api, headers, project["id"], "영업이익")
    assert subtotal["is_subtotal"] is True

    response = await api.patch(
        f"/api/projects/{project['id']}/lines/{subtotal['id']}",
        json={"normalized_account_code": "INTEREST_INCOME"},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/subtotal-has-no-account")


async def test_marking_a_line_as_a_subtotal_drops_its_account(
    api: AsyncClient, uploads_dir: Path, statement_bytes: bytes
) -> None:
    """Otherwise the database constraint would reject the correction outright."""
    headers = await sign_up(api, "owner@example.com")
    project, _ = await extracted(api, headers, statement_bytes)
    line = await line_named(api, headers, project["id"], "기타수익")
    await api.patch(
        f"/api/projects/{project['id']}/lines/{line['id']}",
        json={"normalized_account_code": "INTEREST_INCOME"},
        headers=headers,
    )

    response = await api.patch(
        f"/api/projects/{project['id']}/lines/{line['id']}",
        json={"is_subtotal": True, "subtotal_kind": "GROSS_PROFIT"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_subtotal"] is True
    assert response.json()["normalized_account_code"] is None
