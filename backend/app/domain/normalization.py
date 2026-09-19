"""Applying the account dictionary to an extracted statement (spec §4 step 3).

The pipeline is deliberately ordered so a weaker source of truth never
overrides a stronger one:

    exact → synonym → fuzzy → AI advisor → human

The AI advisor is reached only for captions the dictionary could not resolve,
and — like classification (spec §1) — it **proposes**. It cannot mark a mapping
as settled; anything it touches carries ``requires_human_review``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.domain.accounts import AccountDictionary, NormalizationResult
from app.domain.enums import NormalizationMethod
from app.domain.extraction import ExtractedLine, ExtractedStatement


@dataclass(frozen=True, slots=True)
class AccountSuggestion:
    """What an AI advisor may return for an unresolved caption."""

    code: str | None
    confidence: Decimal
    reasoning: str


class AccountAdvisor(Protocol):
    """The boundary the AI layer sits behind.

    Declared as a protocol so the domain never imports a model client: tests
    substitute a stub and the whole suite runs offline and deterministically
    (architecture §3).
    """

    def suggest(self, label: str, *, context: str | None = None) -> AccountSuggestion: ...


class NullAccountAdvisor:
    """The default: no AI. Every unresolved caption goes straight to a human."""

    def suggest(self, label: str, *, context: str | None = None) -> AccountSuggestion:
        return AccountSuggestion(
            code=None, confidence=Decimal(0), reasoning="no advisor configured"
        )


@dataclass(frozen=True, slots=True)
class NormalizedLine:
    line: ExtractedLine
    code: str | None
    method: NormalizationMethod | None
    score: Decimal
    requires_human_review: bool
    #: The matched account is an aggregate whose contents cannot be inferred
    #: from its caption; a candidate for note-based decomposition (Q5).
    needs_decomposition: bool = False
    reasoning: str | None = None

    @property
    def matched(self) -> bool:
        return self.code is not None


@dataclass(frozen=True, slots=True)
class NormalizationReport:
    lines: tuple[NormalizedLine, ...]

    @property
    def classifiable(self) -> tuple[NormalizedLine, ...]:
        """Detail lines only. Subtotals are never normalized."""
        return tuple(item for item in self.lines if item.line.is_summable)

    @property
    def matched_count(self) -> int:
        return sum(1 for item in self.classifiable if item.matched)

    @property
    def dictionary_matched_count(self) -> int:
        """Matched **without** the AI advisor — what the Phase 4 target measures."""
        return sum(
            1
            for item in self.classifiable
            if item.matched and item.method is not NormalizationMethod.AI
        )

    @property
    def coverage(self) -> Decimal:
        """Share of detail lines resolved by the dictionary alone."""
        total = len(self.classifiable)
        if total == 0:
            return Decimal(0)
        return (Decimal(self.dictionary_matched_count) / Decimal(total)).quantize(Decimal("0.0001"))

    @property
    def unresolved(self) -> tuple[NormalizedLine, ...]:
        return tuple(item for item in self.classifiable if not item.matched)

    @property
    def needing_decomposition(self) -> tuple[NormalizedLine, ...]:
        return tuple(item for item in self.classifiable if item.needs_decomposition)


def _from_dictionary(line: ExtractedLine, result: NormalizationResult) -> NormalizedLine:
    return NormalizedLine(
        line=line,
        code=result.code,
        method=result.method,
        score=result.score,
        # A fuzzy match is a guess the engine is confident about, not a fact;
        # a reviewer should see it. Exact and synonym matches are facts.
        requires_human_review=result.method is NormalizationMethod.FUZZY,
        needs_decomposition=result.ambiguous,
    )


def normalize_statement(
    statement: ExtractedStatement,
    dictionary: AccountDictionary,
    *,
    advisor: AccountAdvisor | None = None,
    advisor_min_confidence: Decimal = Decimal("0.80"),
) -> NormalizationReport:
    """Map every detail line to a canonical account.

    Subtotals are passed through untouched: they are reconciliation targets, not
    classifiable facts, and mapping them would invite double counting.
    """
    advisor = advisor or NullAccountAdvisor()
    results: list[NormalizedLine] = []

    for line in statement.lines:
        if line.is_subtotal:
            results.append(
                NormalizedLine(
                    line=line,
                    code=None,
                    method=None,
                    score=Decimal(0),
                    requires_human_review=False,
                )
            )
            continue

        match = dictionary.match(line.raw_label)
        if match.matched:
            results.append(_from_dictionary(line, match))
            continue

        suggestion = advisor.suggest(
            line.raw_label,
            context=", ".join(line.note_references) or None,
        )
        if suggestion.code and suggestion.confidence >= advisor_min_confidence:
            results.append(
                NormalizedLine(
                    line=line,
                    code=suggestion.code,
                    method=NormalizationMethod.AI,
                    score=suggestion.confidence,
                    # Spec §1: an AI proposal is never settled on its own.
                    requires_human_review=True,
                    needs_decomposition=dictionary.is_ambiguous(suggestion.code),
                    reasoning=suggestion.reasoning,
                )
            )
            continue

        results.append(
            NormalizedLine(
                line=line,
                code=None,
                method=None,
                score=match.score,
                requires_human_review=True,
                reasoning=suggestion.reasoning if suggestion.code is None else None,
            )
        )

    return NormalizationReport(lines=tuple(results))
