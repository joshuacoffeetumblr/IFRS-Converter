"""Running a real statement through the whole pipeline, without a database.

This exists for one job: somebody hands us an actual filing — an anonymised
Korean income statement, a client's trial workbook, a PDF pulled from DART —
and we need to know, in one command, whether this product would read it
correctly. Not whether the tests pass. Whether *that file* works.

What it answers, in the order the pipeline runs:

1. **Did we read the document at all?** The extracted detail lines are summed
   against the subtotals the document itself printed (spec §17). A statement
   whose own arithmetic we cannot reproduce has been misread, and every number
   downstream is worthless. This is the check that matters most, because it is
   the one a synthetic fixture can never fail in an interesting way.
2. **Did the dictionary recognise the captions?** Coverage, and — more useful —
   the exact list of captions it did not, which is the work item: those are the
   synonyms to add.
3. **Which rules actually fired?** A rule set that looks comprehensive in a
   catalog and never fires on a real filing is not comprehensive.
4. **What would we ask the reviewer?** Every unanswered fact, company-scoped
   and line-scoped, is a question the product would put on screen.
5. **Would the gate open?** The §19 validation, verbatim — the same call the
   API makes before it shows anybody a result.

Deliberately no database and no network. It reads a file and prints a report,
so it runs against a file somebody is not allowed to upload anywhere, on a
laptop, with nothing configured. The AI assistant is off unless asked for: a
coverage number that depends on a model is not a measurement of the rules.

On the figures in the output (spec §32): this writes a working document for
the person holding the file, not a log. Nothing here goes through structured
logging. Per-line amounts stay out of it by default — the report names captions,
because the captions are the actionable part, and prints only the arithmetic
the reconciliation turns on.
"""

from __future__ import annotations

import shutil
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path

from app.adapters.ai.factory import build_advisors
from app.adapters.ingest.grid import ExtractOptions, StatementNotFoundError
from app.adapters.storage.files import (
    SUFFIX_BY_MIME,
    UploadRejectedError,
    sniff_mime_type,
)
from app.core.config import get_settings
from app.data.catalog import get_dictionary
from app.data.rule_catalog import get_engine
from app.domain.classification import ClassificationEngine
from app.domain.enums import Ifrs18Category
from app.domain.extraction import (
    ExtractedStatement,
    ReconciliationReport,
    infer_signs_from_subtotals,
    reconcile_extraction,
)
from app.domain.impact import ImpactAnalysis, analyse
from app.domain.normalization import NormalizationReport, normalize_statement
from app.domain.rules import EntityFacts
from app.domain.statement import ClassifiedLine, reconstruct
from app.domain.validation import Tolerances, ValidationReport, validate
from app.services.extraction import ExtractionError, read_statement
from app.services.pipeline import classify_normalized

#: Enough bytes for every magic number we recognise.
SNIFF_BYTES = 8


@dataclass(frozen=True, slots=True)
class Question:
    """A fact the engine is waiting on, and how many lines wait with it."""

    scope: str
    key: str
    lines: int


@dataclass(frozen=True, slots=True)
class DryRun:
    """Everything one pass over one file established."""

    path: Path
    mime_type: str
    source: ExtractedStatement
    extraction: ReconciliationReport
    signs_inferred: bool
    normalization: NormalizationReport
    classified: tuple[ClassifiedLine, ...]
    validation: ValidationReport
    impact: ImpactAnalysis | None
    used_ai: bool
    rule_hits: Counter[str] = field(default_factory=Counter)

    @property
    def extraction_passed(self) -> bool:
        return self.extraction.passed

    @property
    def gate_passed(self) -> bool:
        return self.validation.passed

    @property
    def detail_lines(self) -> int:
        return len(self.normalization.classifiable)

    @property
    def subtotal_lines(self) -> int:
        return sum(1 for line in self.source.lines if line.is_subtotal)

    @property
    def unresolved_captions(self) -> tuple[str, ...]:
        return tuple(item.line.raw_label for item in self.normalization.unresolved)

    @property
    def review_count(self) -> int:
        return sum(1 for line in self.classified if line.decision.requires_human_review)

    @property
    def method_counts(self) -> Counter[str]:
        return Counter(line.decision.method.value for line in self.classified)

    @property
    def category_counts(self) -> Counter[str]:
        return Counter(line.decision.category.value for line in self.classified)

    @property
    def unclassified(self) -> tuple[ClassifiedLine, ...]:
        return tuple(
            line
            for line in self.classified
            if line.decision.category is Ifrs18Category.UNCLASSIFIED
        )

    @property
    def questions(self) -> tuple[Question, ...]:
        """What the reviewer would be asked, most-blocking first."""
        company: Counter[str] = Counter()
        per_line: Counter[str] = Counter()
        for line in self.classified:
            if line.decision.blocked_on_activity is not None:
                company[line.decision.blocked_on_activity.value] += 1
            if line.decision.blocked_on_line_fact is not None:
                per_line[line.decision.blocked_on_line_fact] += 1
        items = [Question("COMPANY", key, count) for key, count in company.items()]
        items += [Question("LINE", key, count) for key, count in per_line.items()]
        return tuple(sorted(items, key=lambda q: (-q.lines, q.scope, q.key)))

    @property
    def verdict(self) -> str:
        """One word for the whole run, chosen by the first thing that failed."""
        if not self.extraction_passed:
            return "MISREAD"
        if not self.gate_passed:
            return "BLOCKED"
        if self.review_count:
            return "NEEDS REVIEW"
        return "CLEAN"


def detect_mime_type(path: Path) -> str:
    with path.open("rb") as handle:
        head = handle.read(SNIFF_BYTES)
    return sniff_mime_type(head, filename=path.name)


@contextmanager
def _readable_as(path: Path, mime_type: str) -> Iterator[Path]:
    """The file under an extension matching what it actually is.

    Readers refuse a file whose extension they do not recognise — openpyxl
    raises outright on one — and a real filing arrives named whatever somebody
    named it: `.xls` for an XLSX, no extension at all, `.txt` from an email
    attachment. The format is decided by the bytes (spec §31), so when the name
    disagrees the file is copied under the right one rather than renamed: this
    command must never modify the document it was handed.
    """
    expected = SUFFIX_BY_MIME.get(mime_type)
    if expected is None or path.suffix.lower() == expected:
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="ifrs18-dryrun-") as directory:
        copy = Path(directory) / f"{path.stem}{expected}"
        shutil.copyfile(path, copy)
        yield copy


def _reattribute(statement: ExtractedStatement, source_file: str) -> ExtractedStatement:
    return replace(
        statement,
        source_file=source_file,
        lines=tuple(
            replace(line, locator=replace(line.locator, source_file=source_file))
            for line in statement.lines
        ),
    )


def dry_run(
    path: Path,
    *,
    options: ExtractOptions | None = None,
    facts: EntityFacts | None = None,
    tolerances: Tolerances | None = None,
    engine: ClassificationEngine | None = None,
    use_ai: bool = False,
) -> DryRun:
    """Read the file and run every stage over it.

    ``facts`` defaults to knowing nothing about the entity, which is the honest
    starting point for a statement nobody has answered questions about — and it
    makes the unanswered questions in the report the real ones (spec §10: a main
    business activity is never inferred, least of all by a harness).
    """
    mime_type = detect_mime_type(path)
    with _readable_as(path, mime_type) as readable:
        source = read_statement(readable, mime_type, options or ExtractOptions())
    # Provenance names the document the operator handed over, never the copy it
    # may have been read through. A locator pointing into a temporary directory
    # that no longer exists is not a trace back to the source (spec §18).
    source = _reattribute(source, path.name)

    # The same two calls the upload path makes, in the same order: a filing
    # that prints expenses as positive figures is not misread, it is
    # differently signed, and its own subtotals say which (spec §16). The
    # hypothesis is accepted only if they then reconcile — which is why this
    # must be the shared function and not a second implementation of it.
    source, signs_inferred = infer_signs_from_subtotals(source)
    extraction = reconcile_extraction(source)

    # `use_ai` is a request, not a fact: with no key configured there is no
    # advisor to consult, and the report has to say which of the two happened.
    if engine is None:
        advisor = build_advisors(get_settings()).classification if use_ai else None
        engine = ClassificationEngine(get_engine().rules, advisor=advisor)
    assisted = use_ai and engine.has_advisor

    facts = facts or EntityFacts.unknown()
    normalization = normalize_statement(source, get_dictionary())
    classified = classify_normalized(
        normalization,
        facts,
        use_advisor=assisted,
        engine=engine,
    )
    reconstructed = reconstruct(source, classified, facts=facts)
    report = validate(source, classified, reconstructed, tolerances=tolerances)

    impact: ImpactAnalysis | None = None
    if report.passed:
        # Spec §19: an impact analysis on numbers that did not reconcile would
        # be a confident answer to the wrong question, so it is simply absent.
        impact = analyse(source, classified, reconstructed)

    return DryRun(
        path=path,
        mime_type=mime_type,
        source=source,
        extraction=extraction,
        signs_inferred=signs_inferred,
        normalization=normalization,
        classified=classified,
        validation=report,
        impact=impact,
        used_ai=assisted,
        rule_hits=Counter(
            line.decision.rule_id for line in classified if line.decision.rule_id is not None
        ),
    )


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _pct(part: int, whole: int) -> str:
    if whole == 0:
        return "n/a"
    return f"{Decimal(part) / Decimal(whole) * 100:.1f}%"


def _bullets(items: tuple[str, ...], *, limit: int = 40) -> list[str]:
    lines = [f"- {item}" for item in items[:limit]]
    if len(items) > limit:
        lines.append(f"- …and {len(items) - limit} more")
    return lines


def report_markdown(run: DryRun) -> str:
    """The run as a document somebody can read, paste into an issue, or diff."""
    out: list[str] = [
        f"# Statement validation — {run.path.name}",
        "",
        f"**Verdict: {run.verdict}**",
        "",
        f"- Format: `{run.mime_type}`",
        f"- Currency / scale: {run.source.currency}, 10^{run.source.scale}",
        f"- Period: {run.source.period_label or 'not stated'}",
        f"- Sheet: {run.source.sheet or 'n/a'}",
        f"- Lines read: {len(run.source.lines)} "
        f"({run.detail_lines} detail, {run.subtotal_lines} subtotal)",
        f"- AI assistant: {'on' if run.used_ai else 'off — rules only'}",
        "",
        "## 1. Extraction (spec §17)",
        "",
        "Detail lines re-summed against the subtotals printed in the document. "
        "A failure here means we misread the file; nothing below it is meaningful.",
        "",
        f"**{'PASSED' if run.extraction_passed else 'FAILED'}** — "
        f"{len(run.extraction.checks)} check(s)"
        + (", signs inferred from subtotals" if run.signs_inferred else ""),
        "",
    ]

    if run.extraction.checks:
        out += [
            "| Check | Reported | Computed | Δ | Tolerance | OK |",
            "| --- | ---: | ---: | ---: | ---: | :-: |",
        ]
        for check in run.extraction.checks:
            delta = check.computed - check.reported
            ok = "✅" if abs(delta) <= check.tolerance else "❌"
            out.append(
                f"| {check.check} | {check.reported} | {check.computed} "
                f"| {delta} | {check.tolerance} | {ok} |"
            )
        out.append("")

    if run.extraction.blockers:
        out += ["Blockers:", *_bullets(run.extraction.blockers), ""]

    out += [
        "## 2. Normalization",
        "",
        f"- Dictionary coverage: **{run.normalization.coverage * 100:.1f}%** "
        f"({run.normalization.dictionary_matched_count}/{run.detail_lines} detail lines)",
        f"- Needing decomposition: {len(run.normalization.needing_decomposition)}",
        f"- Unresolved captions: {len(run.normalization.unresolved)}",
        "",
    ]
    if run.normalization.unresolved:
        out += [
            "These captions matched no account. Each one is a synonym to add, "
            "or a genuinely new account:",
            "",
            *_bullets(run.unresolved_captions),
            "",
        ]

    out += ["## 3. Classification", ""]
    if run.classified:
        out += ["| Method | Lines | Share |", "| --- | ---: | ---: |"]
        for method, count in sorted(run.method_counts.items()):
            out.append(f"| {method} | {count} | {_pct(count, len(run.classified))} |")
        out += ["", "| Category | Lines |", "| --- | ---: |"]
        for category, count in sorted(run.category_counts.items()):
            out.append(f"| {category} | {count} |")
        out.append("")

    out += [
        f"- Requiring human review: **{run.review_count}** "
        f"({_pct(run.review_count, len(run.classified))})",
        f"- Left UNCLASSIFIED: {len(run.unclassified)}",
        "",
    ]

    if run.rule_hits:
        out += [
            "Rules that fired:",
            "",
            "| Rule | Lines |",
            "| --- | ---: |",
            *(
                f"| {rule} | {count} |"
                for rule, count in sorted(run.rule_hits.items(), key=lambda p: (-p[1], p[0]))
            ),
            "",
        ]
    else:
        out += ["No rule fired on any line. Every decision came from the residual.", ""]

    out += ["## 4. Questions the reviewer would be asked (spec §10)", ""]
    if run.questions:
        out += [
            "Unanswered facts block a decision rather than being guessed at. "
            "A main business activity is never inferred from an industry code.",
            "",
            "| Scope | Fact | Lines blocked |",
            "| --- | --- | ---: |",
            *(f"| {q.scope} | {q.key} | {q.lines} |" for q in run.questions),
            "",
        ]
    else:
        out += ["None — every line resolved without needing a fact.", ""]

    out += [
        "## 5. Validation gate (spec §19)",
        "",
        f"**{'PASSED' if run.gate_passed else 'FAILED'}** — "
        f"{len(run.validation.findings)} finding(s), "
        f"{len(run.validation.blocking_failures)} blocking, "
        f"{len(run.validation.warnings)} warning(s)",
        "",
    ]
    if run.validation.findings:
        out += ["| Check | Severity | OK | Detail |", "| --- | --- | :-: | --- |"]
        for finding in run.validation.findings:
            out.append(
                f"| {finding.check} | {finding.severity.value} "
                f"| {'✅' if finding.passed else '❌'} | {finding.detail} |"
            )
        out.append("")

    if run.impact is not None:
        out += [
            "## 6. Impact",
            "",
            f"- Reclassified lines: {len(run.impact.reclassifications)}",
            f"- Waterfall steps: {len(run.impact.waterfall)}",
            f"- Comparable with the reported statement: {'yes' if run.impact.comparable else 'no'}",
            "",
        ]
    else:
        out += [
            "## 6. Impact",
            "",
            "Not computed: the validation gate did not open, and an impact "
            "analysis over numbers that did not reconcile would be a confident "
            "answer to the wrong question (spec §19).",
            "",
        ]

    return "\n".join(out)


def run_and_report(
    path: Path,
    *,
    options: ExtractOptions | None = None,
    use_ai: bool = False,
) -> tuple[str, bool]:
    """Report plus a boolean: did the file survive extraction *and* the gate?

    Extraction failure and a blocked gate are different outcomes, but both mean
    "do not ship a result from this file", which is the one bit a caller — a CI
    job, a shell — actually branches on.
    """
    try:
        result = dry_run(path, options=options, use_ai=use_ai)
    except (UploadRejectedError, ExtractionError) as exc:
        return (
            f"# Statement validation — {path.name}\n\n**Verdict: UNREADABLE**\n\n{exc.reason}\n",
            False,
        )
    except StatementNotFoundError as exc:
        return (
            f"# Statement validation — {path.name}\n\n**Verdict: NO STATEMENT FOUND**\n\n{exc}\n",
            False,
        )
    return report_markdown(result), result.extraction_passed and result.gate_passed
