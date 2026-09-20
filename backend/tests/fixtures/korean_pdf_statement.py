"""Synthetic Korean income statements as text-based PDFs.

Generated rather than committed as binary, for the same reason the XLSX
fixtures are: the arithmetic and the layout are visible as code, and a reviewer
can see exactly what the parser is being asked to read.

**These are synthetic.** They imitate the typography of a K-IFRS filing — a
cover page, a right-aligned figure column, indented sub-items, a note column —
but they are not drawn from any real filing, so they validate the reader, not
the account dictionary.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from tests.fixtures.korean_income_statement import STATEMENT_ROWS, Row, SignStyle, _render

#: A CID font, so Korean renders without shipping a font file. reportlab maps
#: it to a standard Adobe-Japan/Korea collection the viewer resolves.
FONT = "HYSMyeongJo-Medium"

LEFT = 60.0
NOTE_X = 300.0
AMOUNT_X = 470.0
#: One indentation level, matching `pdf.INDENT_WIDTH`.
INDENT = 12.0
LINE_HEIGHT = 18.0


def _register() -> None:
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))


def build_pdf(
    path: Path,
    *,
    rows: tuple[Row, ...] = STATEMENT_ROWS,
    style: SignStyle = SignStyle.PARENTHESES,
    cover_page: bool = True,
    scanned: bool = False,
) -> Path:
    """Write a synthetic statement as a PDF and return its path.

    ``cover_page`` puts unrelated content on page 1, so the reader has to find
    the statement rather than assume it is first. ``scanned`` produces a page
    with no text layer at all — what a photographed filing looks like.
    """
    _register()
    pdf = canvas.Canvas(str(path), pagesize=A4)
    _, height = A4

    if scanned:
        # A page with graphics and no text: exactly what a scan gives us, and
        # what the reader must refuse rather than guess at.
        pdf.rect(80, 400, 400, 300, stroke=1, fill=0)
        pdf.save()
        return path

    if cover_page:
        pdf.setFont(FONT, 14)
        pdf.drawString(LEFT, height - 100, "연결재무제표")
        pdf.setFont(FONT, 10)
        pdf.drawString(LEFT, height - 130, "제 55 기")
        pdf.drawString(LEFT, height - 150, "주식회사 합성전자와 그 종속기업")
        pdf.showPage()

    y = height - 80
    pdf.setFont(FONT, 13)
    pdf.drawString(LEFT, y, "연결 포괄손익계산서")
    pdf.setFont(FONT, 9)
    y -= 20
    pdf.drawString(LEFT, y, "제55기 2025.01.01 부터 2025.12.31 까지")
    y -= 16
    pdf.drawString(LEFT, y, "(단위: 백만원)")

    y -= 30
    pdf.setFont(FONT, 10)
    pdf.drawString(LEFT, y, "과목")
    pdf.drawString(NOTE_X, y, "주석")
    pdf.drawRightString(AMOUNT_X, y, "제55기")

    for row in rows:
        y -= LINE_HEIGHT
        pdf.drawString(LEFT + row.depth * INDENT, y, row.label)
        if row.note:
            pdf.drawString(NOTE_X, y, row.note)
        pdf.drawRightString(AMOUNT_X, y, _printed(row.amount, style))

    pdf.save()
    return path


def _printed(amount: Decimal, style: SignStyle) -> str:
    rendered = _render(amount, style)
    return f"{rendered:,}" if isinstance(rendered, Decimal) else str(rendered)


def build_notes_pdf(path: Path) -> Path:
    """A filing whose first pages are notes, with the statement further in.

    Closer to a real document, where the statement of profit or loss sits among
    the balance sheet, the cash flow statement and dozens of pages of notes.
    """
    _register()
    pdf = canvas.Canvas(str(path), pagesize=A4)
    _, height = A4

    pdf.setFont(FONT, 12)
    pdf.drawString(LEFT, height - 100, "연결재무상태표")
    pdf.setFont(FONT, 10)
    for offset, (label, value) in enumerate(
        (("유동자산", "500,000"), ("비유동자산", "800,000"), ("자산총계", "1,300,000"))
    ):
        y = height - 140 - offset * LINE_HEIGHT
        pdf.drawString(LEFT, y, label)
        pdf.drawRightString(AMOUNT_X, y, value)
    pdf.showPage()
    pdf.save()

    # Append the statement itself as a second document, then merge by writing
    # both pages in one canvas — simpler here than a PDF merge library.
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setFont(FONT, 12)
    pdf.drawString(LEFT, height - 100, "연결재무상태표")
    pdf.setFont(FONT, 10)
    for offset, (label, value) in enumerate(
        (("유동자산", "500,000"), ("비유동자산", "800,000"), ("자산총계", "1,300,000"))
    ):
        y = height - 140 - offset * LINE_HEIGHT
        pdf.drawString(LEFT, y, label)
        pdf.drawRightString(AMOUNT_X, y, value)
    pdf.showPage()

    y = height - 80
    pdf.setFont(FONT, 13)
    pdf.drawString(LEFT, y, "연결 포괄손익계산서")
    pdf.setFont(FONT, 9)
    y -= 20
    pdf.drawString(LEFT, y, "(단위: 백만원)")
    y -= 30
    pdf.setFont(FONT, 10)
    pdf.drawString(LEFT, y, "과목")
    pdf.drawRightString(AMOUNT_X, y, "제55기")
    for row in STATEMENT_ROWS:
        y -= LINE_HEIGHT
        pdf.drawString(LEFT + row.depth * INDENT, y, row.label)
        pdf.drawRightString(AMOUNT_X, y, _printed(row.amount, SignStyle.PARENTHESES))
    pdf.save()
    return path
