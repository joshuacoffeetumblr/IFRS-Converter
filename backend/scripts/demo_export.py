"""Run the whole pipeline over the synthetic fixture and write the export.

    python -m scripts.demo_export [output directory]

For looking at the actual deliverable rather than at assertions about it.

This lives outside ``app/`` on purpose: it reads the test fixtures, and
production code must not depend on the test package. It also **simulates the
review step** — decomposing the aggregate captions from their notes, and
answering the IFRS 18 B65 question about which item produced a foreign exchange
difference. Without that, the run stops at the reconciliation gate, because
those are decisions only a person can make. The simulated answers are labelled
as such in the file.
"""

from __future__ import annotations

import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from app.adapters.export.xlsx import ExportPackage, export_to_path
from app.adapters.ingest.xlsx import read_income_statement
from app.data.catalog import catalog_version
from app.data.rule_catalog import rule_set_version
from app.domain.classification import ClassificationDecision
from app.domain.decomposition import Component, decompose
from app.domain.enums import (
    ActivityType,
    ClassificationMethod,
    ConfidenceBand,
    Ifrs18Category,
)
from app.domain.extraction import ExtractedStatement
from app.domain.impact import analyse
from app.domain.rules import EntityFacts
from app.domain.statement import ClassifiedLine, reconstruct
from app.domain.validation import validate
from app.services.pipeline import classify_statement
from tests.fixtures.korean_income_statement import SignStyle, build_workbook

FACTS = EntityFacts(
    {
        ActivityType.INVESTING_IN_ASSETS: False,
        ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS: False,
    }
)

#: What a preparer would read out of the notes. Each set sums to its caption.
NOTE_DETAIL: dict[str, tuple[tuple[str, str], ...]] = {
    "기타수익": (("유형자산처분이익", "12000"),),
    "금융수익": (("이자수익", "5000"), ("매출채권 외환차익", "3000")),
    "금융비용": (("이자비용", "-14000"),),
    "기타비용": (("유형자산처분손실", "-9000"),),
}


def decompose_aggregates(source: ExtractedStatement) -> ExtractedStatement:
    lines = []
    next_ordinal = 100
    for extracted in source.lines:
        detail = NOTE_DETAIL.get(extracted.raw_label)
        if detail is None:
            lines.append(extracted)
            continue
        parent, children = decompose(
            extracted,
            tuple(Component(label, Decimal(amount)) for label, amount in detail),
            next_ordinal=next_ordinal,
        )
        next_ordinal += len(children)
        lines.extend([parent, *children])
    return ExtractedStatement(
        source_file=source.source_file,
        sheet=source.sheet,
        lines=tuple(lines),
        currency=source.currency,
        scale=source.scale,
    )


def answer_open_questions(classified: tuple[ClassifiedLine, ...]) -> tuple[ClassifiedLine, ...]:
    """Stand in for a reviewer answering the line-scoped questions."""
    answered = []
    for item in classified:
        decision = item.decision
        if decision.blocked_on_line_fact == "FX_UNDERLYING_ITEM":
            decision = ClassificationDecision(
                line_id=decision.line_id,
                category=Ifrs18Category.OPERATING,
                subcategory=None,
                method=ClassificationMethod.USER,
                rule_id=decision.rule_id,
                confidence=Decimal(1),
                confidence_band=ConfidenceBand.HIGH,
                requires_human_review=False,
                evidence=decision.evidence,
                reasoning=(
                    "SIMULATED REVIEW: arose on a trade receivable, so it "
                    "follows the receivable into operating (IFRS 18 B65)."
                ),
            )
        answered.append(
            ClassifiedLine(
                line=item.line,
                decision=decision,
                normalized_account_code=item.normalized_account_code,
            )
        )
    return tuple(answered)


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path(tempfile.mkdtemp())
    target.mkdir(parents=True, exist_ok=True)

    source = decompose_aggregates(
        read_income_statement(
            build_workbook(target / "demo_input.xlsx", style=SignStyle.PARENTHESES)
        )
    )
    classified = answer_open_questions(classify_statement(source, FACTS, use_advisor=False))
    reconstructed = reconstruct(source, classified, facts=FACTS)
    report = validate(source, classified, reconstructed)

    written = export_to_path(
        ExportPackage(
            source=source,
            classified=classified,
            reconstructed=reconstructed,
            validation=report,
            impact=analyse(source, classified, reconstructed),
            project_name="Synthetic fixture — demonstration only, simulated review",
            rule_set_version=rule_set_version(),
            catalog_version=catalog_version(),
        ),
        target / "demo_export.xlsx",
    )

    print(f"wrote {written}")
    print(f"reconciled: {report.passed}")
    if not report.passed:
        for finding in report.blocking_failures:
            print(f"  blocked by {finding.check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
