"""Upload validation (spec §31). The most exposed surface this product has."""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.adapters.storage.files import (
    CSV_MIME,
    PDF_MIME,
    XBRL_MIME,
    XLSX_MIME,
    StoredFile,
    UploadRejectedError,
    _human_size,
    safe_display_name,
    sniff_mime_type,
    store_upload,
)

ALLOWED = (XLSX_MIME, CSV_MIME, PDF_MIME)


async def feed(data: bytes, chunk: int = 4096) -> AsyncIterator[bytes]:
    for index in range(0, len(data), chunk):
        yield data[index : index + chunk]


async def store(
    tmp_path: Path, data: bytes, *, filename: str, max_bytes: int = 25 * 1024 * 1024
) -> StoredFile:
    return await store_upload(
        feed(data),
        filename=filename,
        directory=tmp_path,
        max_bytes=max_bytes,
        allowed_mime_types=ALLOWED,
    )


def workbook_bytes() -> bytes:
    buffer = io.BytesIO()
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet["A1"] = "매출액"
    book.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Format is sniffed, never taken from the client
# ---------------------------------------------------------------------------


async def test_a_real_workbook_is_accepted(tmp_path: Path) -> None:
    stored = await store(tmp_path, workbook_bytes(), filename="fs.xlsx")

    assert stored.mime_type == XLSX_MIME
    assert stored.size_bytes > 0
    assert len(stored.sha256) == 64


async def test_csv_and_pdf_are_accepted(tmp_path: Path) -> None:
    csv = await store(tmp_path, "매출액,1000\n".encode(), filename="fs.csv")
    pdf = await store(tmp_path, b"%PDF-1.7\ncontent", filename="fs.pdf")

    assert csv.mime_type == CSV_MIME
    assert pdf.mime_type == PDF_MIME


async def test_an_executable_renamed_as_a_workbook_is_refused(tmp_path: Path) -> None:
    """The extension is not evidence; the leading bytes are."""
    with pytest.raises(UploadRejectedError) as exc:
        await store(tmp_path, b"MZ\x90\x00" + b"\x00" * 200, filename="payload.xlsx")

    assert exc.value.code == "unsupported-media-type"


@pytest.mark.parametrize(
    ("head", "filename", "expected"),
    [
        (b"PK\x03\x04rest", "x.xlsx", XLSX_MIME),
        (b"%PDF-1.4", "x.pdf", PDF_MIME),
        (b"col,col\n", "x.csv", CSV_MIME),
        (b"col\tcol\n", "x.tsv", CSV_MIME),
    ],
)
def test_sniffing(head: bytes, filename: str, expected: str) -> None:
    assert sniff_mime_type(head, filename=filename) == expected


def test_an_unknown_format_is_refused() -> None:
    with pytest.raises(UploadRejectedError):
        sniff_mime_type(b"\x7fELF\x02\x01", filename="statement.bin")


# ---------------------------------------------------------------------------
# Size is enforced while reading
# ---------------------------------------------------------------------------


async def test_an_oversized_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UploadRejectedError) as exc:
        await store(tmp_path, b"a" * 5000, filename="big.csv", max_bytes=1000)

    assert exc.value.code == "file-too-large"


async def test_an_oversized_file_leaves_nothing_on_disk(tmp_path: Path) -> None:
    """The point of a size limit is not to hold the bytes."""
    with pytest.raises(UploadRejectedError):
        await store(tmp_path, b"a" * 5000, filename="big.csv", max_bytes=1000)

    assert list(tmp_path.iterdir()) == []


async def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UploadRejectedError) as exc:
        await store(tmp_path, b"", filename="empty.csv")

    assert exc.value.code == "empty-file"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(26214400, "25 MB"), (524288, "512 KB"), (1000, "1000 bytes")],
)
def test_size_limits_read_sensibly(limit: int, expected: str) -> None:
    """A small limit must not round down to "0 MB"."""
    assert _human_size(limit) == expected


# ---------------------------------------------------------------------------
# A workbook is a ZIP archive
# ---------------------------------------------------------------------------


async def test_a_zip_bomb_is_refused(tmp_path: Path) -> None:
    """A few hundred kilobytes that expand to hundreds of megabytes.

    openpyxl would attempt it, so the archive is inspected before it is ever
    opened as a spreadsheet.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"\0" * (300 * 1024 * 1024))
    bomb = buffer.getvalue()

    assert len(bomb) < 2 * 1024 * 1024, "the bomb must be small enough to pass the size check"

    with pytest.raises(UploadRejectedError) as exc:
        await store(tmp_path, bomb, filename="bomb.xlsx")

    assert exc.value.code == "archive-too-large"
    assert list(tmp_path.iterdir()) == []


async def test_a_corrupt_workbook_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UploadRejectedError) as exc:
        await store(tmp_path, b"PK\x03\x04" + b"garbage" * 100, filename="broken.xlsx")

    assert exc.value.code == "corrupt-file"


# ---------------------------------------------------------------------------
# The client's filename never becomes a path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("a/b/c.xlsx", "c.xlsx"),
        ("", "upload"),
        (None, "upload"),
        ("fs.xlsx", "fs.xlsx"),
    ],
)
def test_display_names_cannot_reintroduce_a_path(given: str | None, expected: str) -> None:
    result = safe_display_name(given)

    assert result == expected
    assert "/" not in result
    assert "\\" not in result


def test_a_windows_path_is_flattened() -> None:
    result = safe_display_name("..\\..\\windows\\system32")

    assert "\\" not in result
    assert ".." not in Path(result).parts


def test_a_null_byte_is_stripped() -> None:
    assert "\x00" not in safe_display_name("evil\x00.xlsx")


def test_a_very_long_name_is_truncated() -> None:
    assert len(safe_display_name("x" * 5000)) <= 255


async def test_the_storage_key_is_generated_not_derived(tmp_path: Path) -> None:
    """So a hostile filename cannot influence where the bytes land."""
    stored = await store(tmp_path, workbook_bytes(), filename="../../evil.xlsx")

    stem, _, suffix = stored.storage_key.partition(".")
    assert stem.isalnum() and len(stem) == 32
    # The extension comes from the sniffed format, never from the client: it is
    # there because a reader such as openpyxl refuses a file it cannot place by
    # extension, and a generated key has none of its own.
    assert suffix == "xlsx"
    assert stored.original_filename == "evil.xlsx"
    assert (tmp_path / stored.storage_key).exists()
    assert stored.path.parent == tmp_path


async def test_a_misleading_extension_is_not_carried_over(tmp_path: Path) -> None:
    """The stored name reports the format we identified, not the one claimed."""
    stored = await store(tmp_path, b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", filename="report.xlsx")

    assert stored.mime_type == PDF_MIME
    assert stored.storage_key.endswith(".pdf")
    assert stored.original_filename == "report.xlsx"


async def test_identical_content_produces_the_same_digest(tmp_path: Path) -> None:
    data = workbook_bytes()

    first = await store(tmp_path, data, filename="a.xlsx")
    second = await store(tmp_path, data, filename="b.xlsx")

    assert first.sha256 == second.sha256
    assert first.storage_key != second.storage_key


# ---------------------------------------------------------------------------
# XBRL, which is XML — and so is everything shipped beside it
# ---------------------------------------------------------------------------

XBRL_HEAD = b'<?xml version="1.0" encoding="UTF-8"?><xbrli:xbrl'


def test_an_xbrl_instance_is_recognised() -> None:
    assert sniff_mime_type(XBRL_HEAD, filename="entity_2026-06-30.xbrl") == XBRL_MIME


def test_a_taxonomy_schema_is_not_a_filing() -> None:
    """A DART filing is a set of files and only one of them holds figures.

    The schema and the linkbases are XML too. Accepting them here would put an
    empty statement in front of a reviewer instead of saying which file to send.
    """
    with pytest.raises(UploadRejectedError):
        sniff_mime_type(XBRL_HEAD, filename="entity_2026-06-30.xsd")


def test_xml_content_without_an_xml_extension_is_refused() -> None:
    with pytest.raises(UploadRejectedError):
        sniff_mime_type(XBRL_HEAD, filename="filing.pdf")


def test_an_xbrl_extension_without_xml_content_is_refused() -> None:
    """The extension alone is the client's word for it."""
    with pytest.raises(UploadRejectedError):
        sniff_mime_type(b"MZ\x90\x00 not xml at all", filename="filing.xbrl")
