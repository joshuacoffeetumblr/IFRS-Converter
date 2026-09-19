"""Account normalization: printed caption → canonical account code (spec §4 step 3).

Pure domain logic. The catalog itself is data (``app/data/normalized_accounts.json``)
and is loaded by ``app.data.catalog``; everything here operates on definitions
passed in, so the matching rules are testable without touching a file or a
database.

Matching runs strongest-first and stops at the first hit, so a weaker method
never overrides a stronger one:

1. ``EXACT``   — the caption is the canonical label, character for character.
2. ``SYNONYM`` — the caption's canonical form matches a known surface form.
3. ``FUZZY``   — close enough to exactly one candidate, above a threshold.
4. *no match*  — left for the AI advisor, then for a human. Never guessed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher

from app.domain.enums import AccountNature, NormalizationMethod, StatementSection

#: Leading enumeration used by Korean statements: "Ⅰ. 매출액", "1. 매출액",
#: "가. 매출액", "(1) 매출액". Stripped before matching, since it is position
#: within the document rather than part of the account's identity.
_LEADING_ENUMERATION = re.compile(
    r"^\s*(?:"
    r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+"
    r"|[IVXivx]+(?=[.)\s])"
    r"|[가나다라마바사아자차카타파하]"
    r"|[0-9]+"
    r"|\([0-9]+\)"
    r"|[①-⑳]"
    r")\s*[.)]?\s*"
)

#: A trailing parenthetical is a cross-reference or a gloss, not part of the
#: account name: "매출액(주석 21)" and "매출액" are the same account.
#: Kept only when it is the *whole* label, e.g. "수익(매출액)".
_TRAILING_PARENTHETICAL = re.compile(r"[(（][^)）]*[)）]\s*$")

#: Characters carrying no identity: every kind of space, and separators used
#: for layout.
_STRIP_CHARS = re.compile(r"[\s　   ·ㆍ・,]+")


def normalize_label(label: str) -> str:
    """Reduce a printed caption to a comparable canonical form.

    Applied to both sides of every comparison, so ``판매비와 관리비``,
    ``Ⅱ. 판매비와관리비`` and ``판매비와관리비(주석 23)`` all reduce to the same
    string.
    """
    text = unicodedata.normalize("NFKC", label).strip()
    text = _LEADING_ENUMERATION.sub("", text)

    # Only drop a trailing parenthetical when something remains without it.
    stripped = _TRAILING_PARENTHETICAL.sub("", text).strip()
    if stripped:
        text = stripped

    text = _STRIP_CHARS.sub("", text)
    return text.casefold()


#: Captions denoting a subtotal rather than an account. Kept here, beside the
#: matcher, so the dictionary can refuse them outright; the ingest layer maps
#: them to a `SubtotalKind` separately.
SUBTOTAL_CAPTION_STEMS: tuple[str, ...] = (
    "매출총이익",
    "매출총손실",
    "영업이익",
    "영업손실",
    "법인세차감전순이익",
    "법인세비용차감전순이익",
    "법인세차감전계속영업이익",
    "법인세차감전순손실",
    "당기순이익",
    "당기순손실",
    "반기순이익",
    "분기순이익",
    "총포괄손익",
    "기타포괄손익",
)


def is_subtotal_caption(label: str) -> bool:
    """Whether a caption denotes a subtotal rather than an account."""
    compact = normalize_label(label)
    if not compact:
        return False
    return any(compact.startswith(normalize_label(stem)) for stem in SUBTOTAL_CAPTION_STEMS)


@dataclass(frozen=True, slots=True)
class AccountDefinition:
    code: str
    label_ko: str
    label_en: str
    section: StatementSection = StatementSection.PL
    nature: AccountNature | None = None
    parent_code: str | None = None
    #: Aggregate captions whose contents cannot be inferred from the caption
    #: alone. Always reach human review, and are candidates for note-based
    #: decomposition (open question Q5).
    ambiguous_by_default: bool = False
    synonyms_ko: tuple[str, ...] = ()
    synonyms_en: tuple[str, ...] = ()

    @property
    def surface_forms(self) -> tuple[str, ...]:
        return (self.label_ko, self.label_en, *self.synonyms_ko, *self.synonyms_en)


@dataclass(frozen=True, slots=True)
class NormalizationCandidate:
    code: str
    score: Decimal
    matched_on: str


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    code: str | None
    method: NormalizationMethod | None
    score: Decimal
    #: Runners-up, so a reviewer can see what else was close.
    candidates: tuple[NormalizationCandidate, ...] = field(default=())
    #: True when the matched account is an aggregate needing decomposition.
    ambiguous: bool = False

    @property
    def matched(self) -> bool:
        return self.code is not None


#: Default similarity a fuzzy match must reach. Configurable per project so a
#: cautious engagement can demand more (spec §29: no magic numbers).
#:
#: Set at 0.92 rather than something looser because Korean account captions are
#: short, so one character carries a great deal of weight: 영업이익 and 영업외이익
#: score 0.889 against each other and mean opposite things. A threshold that
#: admits that pair is not a threshold.
DEFAULT_FUZZY_THRESHOLD = Decimal("0.92")

#: Candidates scoring this far below the threshold are not even collected.
#: Purely a performance filter; it never affects which candidate wins.
_PREFILTER_MARGIN = Decimal("0.10")

#: Similarity is reported to four decimal places, matching the NUMERIC(5,4)
#: column that stores it.
_SCORE_PRECISION = Decimal("0.0001")


def _as_decimal(ratio: object) -> Decimal:
    """Convert a similarity ratio to Decimal at the boundary.

    ``difflib`` returns a binary float, which must not propagate into the
    domain. Rounding through ``str`` avoids the binary artefacts that
    ``Decimal(some_float)`` would introduce.
    """
    return Decimal(str(ratio)).quantize(_SCORE_PRECISION)


#: How far ahead of the runner-up the best fuzzy candidate must be. Without
#: this, 지분법이익 and 지분법손실 — which differ by one character — would be
#: separated by noise rather than by evidence.
DEFAULT_FUZZY_MARGIN = Decimal("0.04")


class AccountDictionary:
    """Matches printed captions to canonical account codes."""

    def __init__(
        self,
        definitions: tuple[AccountDefinition, ...],
        *,
        fuzzy_threshold: Decimal = DEFAULT_FUZZY_THRESHOLD,
        fuzzy_margin: Decimal = DEFAULT_FUZZY_MARGIN,
    ) -> None:
        self._definitions = definitions
        self._by_code = {definition.code: definition for definition in definitions}
        self._fuzzy_threshold = fuzzy_threshold
        self._fuzzy_margin = fuzzy_margin

        self._exact: dict[str, str] = {}
        self._synonyms: dict[str, str] = {}
        for definition in definitions:
            self._exact.setdefault(definition.label_ko, definition.code)
            self._exact.setdefault(definition.label_en, definition.code)
            for form in definition.surface_forms:
                key = normalize_label(form)
                if key:
                    self._synonyms.setdefault(key, definition.code)

    @property
    def definitions(self) -> tuple[AccountDefinition, ...]:
        return self._definitions

    def get(self, code: str) -> AccountDefinition | None:
        return self._by_code.get(code)

    def is_ambiguous(self, code: str | None) -> bool:
        definition = self._by_code.get(code) if code else None
        return bool(definition and definition.ambiguous_by_default)

    def match(self, label: str) -> NormalizationResult:
        """Resolve a caption, strongest method first."""
        raw = label.strip()
        if not raw:
            return NormalizationResult(None, None, Decimal(0))

        if is_subtotal_caption(raw):
            # A subtotal aggregates other lines; it is not an account. Letting
            # one through here would double count it and, worse, expose it to
            # fuzzy matching against an account with a similar name.
            return NormalizationResult(None, None, Decimal(0))

        if raw in self._exact:
            code = self._exact[raw]
            return NormalizationResult(
                code, NormalizationMethod.EXACT, Decimal(1), ambiguous=self.is_ambiguous(code)
            )

        key = normalize_label(raw)
        if not key:
            return NormalizationResult(None, None, Decimal(0))

        if key in self._synonyms:
            code = self._synonyms[key]
            return NormalizationResult(
                code, NormalizationMethod.SYNONYM, Decimal(1), ambiguous=self.is_ambiguous(code)
            )

        return self._fuzzy(key)

    def _fuzzy(self, key: str) -> NormalizationResult:
        scored: list[NormalizationCandidate] = []
        prefilter = self._fuzzy_threshold - _PREFILTER_MARGIN
        for form, code in self._synonyms.items():
            # SequenceMatcher returns a binary float. It is converted to Decimal
            # at once, so every comparison and every score that leaves this
            # method is decimal — the same boundary discipline applied to
            # spreadsheet values in `app.domain.money` (architecture §5).
            ratio = _as_decimal(SequenceMatcher(None, key, form).ratio())
            if ratio >= prefilter:
                scored.append(NormalizationCandidate(code, ratio, form))

        scored.sort(key=lambda candidate: candidate.score, reverse=True)
        if not scored:
            return NormalizationResult(None, None, Decimal(0))

        best = scored[0]
        if best.score < self._fuzzy_threshold:
            # Below threshold: report the near misses so a reviewer sees what
            # the engine considered, but claim no match.
            return NormalizationResult(None, None, best.score, candidates=tuple(scored[:5]))

        runner_up = next(
            (candidate for candidate in scored[1:] if candidate.code != best.code), None
        )
        if runner_up is not None and (best.score - runner_up.score) < self._fuzzy_margin:
            # Two different accounts are almost equally close. Choosing between
            # them would be a coin flip, so the engine declines and escalates.
            return NormalizationResult(None, None, best.score, candidates=tuple(scored[:5]))

        return NormalizationResult(
            best.code,
            NormalizationMethod.FUZZY,
            best.score,
            candidates=tuple(scored[1:5]),
            ambiguous=self.is_ambiguous(best.code),
        )
