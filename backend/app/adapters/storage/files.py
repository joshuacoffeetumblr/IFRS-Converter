"""Accepting and storing uploaded files (spec §31).

An upload endpoint is the most exposed surface this product has, so the checks
here are deliberately unforgiving:

**Size is enforced while reading, not after.** Measuring a file once it is in
memory is too late — the memory is already gone. The stream is read in chunks
and abandoned the moment it exceeds the limit.

**The content type is sniffed, never taken from the client.** A browser will
send whatever it likes in the ``Content-Type`` header, and a caller with intent
will send something else again.

**The client's filename never becomes part of a path.** Storage keys are
generated. A name like ``../../etc/passwd`` is kept for display only, which is
the only thing it is fit for.

**A workbook is a ZIP archive, so its uncompressed size is checked too.** A few
hundred kilobytes of XLSX can expand to gigabytes; openpyxl would happily try.
"""

from __future__ import annotations

import hashlib
import uuid
import zipfile
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path

#: Magic numbers, checked against the first bytes of the stream.
XLSX_MAGIC = b"PK\x03\x04"  # XLSX and XLSM are ZIP archives
XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # the older OLE2 format
PDF_MAGIC = b"%PDF-"
#: An XBRL instance is XML. It has no magic number of its own, so it is
#: recognised by the XML declaration plus the extension a filing uses.
XML_MAGIC = b"<?xml"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLS_MIME = "application/vnd.ms-excel"
CSV_MIME = "text/csv"
PDF_MIME = "application/pdf"
XBRL_MIME = "application/xbrl+xml"

CHUNK_SIZE = 64 * 1024

#: The extension a stored file is given, chosen from the **sniffed** format
#: rather than from the client's filename. Readers refuse a file whose
#: extension they do not recognise — openpyxl raises on one it cannot place —
#: and a generated storage key has none of its own.
SUFFIX_BY_MIME = {
    XLSX_MIME: ".xlsx",
    XLS_MIME: ".xls",
    CSV_MIME: ".csv",
    PDF_MIME: ".pdf",
    XBRL_MIME: ".xbrl",
}

#: The ratio of uncompressed to compressed bytes beyond which an archive is
#: treated as hostile. Real spreadsheets compress well — XML with repetitive
#: markup — so the limit is generous; a zip bomb exceeds it by orders of
#: magnitude, not by a margin.
MAX_COMPRESSION_RATIO = 200

#: Absolute ceiling on what a workbook may expand to, whatever its ratio.
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024


def _human_size(num_bytes: int) -> str:
    """A readable limit, without rounding a small one down to "0 MB"."""
    for unit, size in (("MB", 1024 * 1024), ("KB", 1024)):
        if num_bytes >= size:
            return f"{num_bytes / size:.0f} {unit}"
    return f"{num_bytes} bytes"


class UploadRejectedError(Exception):
    """The file was refused. ``reason`` is safe to show a user."""

    def __init__(self, reason: str, *, code: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True, slots=True)
class StoredFile:
    storage_key: str
    path: Path
    size_bytes: int
    sha256: str
    mime_type: str
    original_filename: str


def sniff_mime_type(head: bytes, *, filename: str) -> str:
    """Identify the format from its leading bytes.

    CSV has no magic number, so it is the only format identified by extension —
    and then only after it is shown to be decodable text, which is checked
    separately.
    """
    if head.startswith(XLSX_MAGIC):
        return XLSX_MIME
    if head.startswith(XLS_MAGIC):
        return XLS_MIME
    if head.startswith(PDF_MAGIC):
        return PDF_MIME
    suffix = Path(filename).suffix.lower()
    # XBRL is XML, and so is a taxonomy schema and a linkbase — which a filing
    # ships alongside the instance and which carry no figures at all. Both the
    # content and the extension have to agree before this is read as a filing,
    # so uploading the wrong file out of the set is refused here rather than
    # producing an empty statement later.
    if head.startswith(XML_MAGIC) and suffix in {".xbrl", ".xml"}:
        return XBRL_MIME
    if suffix in {".csv", ".tsv", ".txt"}:
        return CSV_MIME
    raise UploadRejectedError(
        "The file is not a workbook, CSV, PDF or XBRL filing. Its contents do "
        "not match any supported format.",
        code="unsupported-media-type",
    )


def safe_display_name(filename: str | None) -> str:
    """Reduce a client filename to something safe to store and show.

    Only the final component is kept, and separators are stripped, so a name
    can never reintroduce a path. The result is for display; it is never used
    to build a storage location.
    """
    candidate = Path(filename or "").name.strip()
    candidate = candidate.replace("\x00", "").replace("/", "").replace("\\", "")
    return candidate[:255] or "upload"


def _assert_archive_is_sane(path: Path, compressed_size: int) -> None:
    """Refuse an archive that expands far beyond its stored size."""
    try:
        with zipfile.ZipFile(path) as archive:
            uncompressed = sum(entry.file_size for entry in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise UploadRejectedError(
            "The workbook is corrupt and could not be opened.", code="corrupt-file"
        ) from exc

    if uncompressed > MAX_UNCOMPRESSED_BYTES:
        raise UploadRejectedError(
            "The workbook expands to more than the maximum permitted size.",
            code="archive-too-large",
        )
    if compressed_size and uncompressed / compressed_size > MAX_COMPRESSION_RATIO:
        raise UploadRejectedError(
            "The workbook's compression ratio is implausible for a spreadsheet.",
            code="archive-too-large",
        )


async def store_upload(
    chunks: AsyncIterator[bytes],
    *,
    filename: str | None,
    directory: Path,
    max_bytes: int,
    allowed_mime_types: Iterable[str],
) -> StoredFile:
    """Stream an upload to disk, checking it as it arrives."""
    display_name = safe_display_name(filename)
    storage_key = f"{uuid.uuid4().hex}"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / storage_key

    digest = hashlib.sha256()
    written = 0
    head = b""

    try:
        with target.open("wb") as handle:
            async for chunk in chunks:
                if not chunk:
                    continue
                written += len(chunk)
                if written > max_bytes:
                    # Abandoned mid-stream: the point of a size limit is to
                    # avoid holding the bytes, so the check cannot wait.
                    raise UploadRejectedError(
                        f"The file exceeds the {_human_size(max_bytes)} limit.",
                        code="file-too-large",
                    )
                if len(head) < 16:
                    head += chunk[: 16 - len(head)]
                digest.update(chunk)
                handle.write(chunk)

        if written == 0:
            raise UploadRejectedError("The file is empty.", code="empty-file")

        mime_type = sniff_mime_type(head, filename=display_name)
        if mime_type not in set(allowed_mime_types):
            raise UploadRejectedError(
                f"{mime_type} is not an accepted format.",
                code="unsupported-media-type",
            )
        if mime_type == XLSX_MIME:
            _assert_archive_is_sane(target, written)

        suffix = SUFFIX_BY_MIME.get(mime_type, "")
        if suffix:
            target = target.rename(target.with_name(storage_key + suffix))
            storage_key = target.name

    except Exception:
        target.unlink(missing_ok=True)
        raise

    return StoredFile(
        storage_key=storage_key,
        path=target,
        size_bytes=written,
        sha256=digest.hexdigest(),
        mime_type=mime_type,
        original_filename=display_name,
    )
