"""Authoritative enumerations for the IFRS 18 domain.

These live in the domain layer, not the ORM, for two reasons:

1. `02-erd.md` §4.1 stores enum-like columns as ``text`` with a ``CHECK``
   constraint rather than a PostgreSQL ``ENUM`` type, because spec §9 requires
   the classification taxonomy to stay extensible and altering a PG enum is
   awkward to migrate and to roll back. The constraints are *generated* from
   the members here, so Python remains the single source of truth.
2. The classification engine must be testable without a database.
"""

from __future__ import annotations

from enum import StrEnum

# ---------------------------------------------------------------------------
# IFRS 18 classification taxonomy (spec §9)
# ---------------------------------------------------------------------------


class Ifrs18Category(StrEnum):
    """The five IFRS 18 categories for profit or loss, plus a technical state.

    ``OPERATING`` is the *residual* category: income and expenses not classified
    into investing, financing, income taxes or discontinued operations fall here
    by definition of the standard (`07-ifrs18-source-verification.md` F2).

    ``UNCLASSIFIED`` is **not** an IFRS 18 category. It is a technical state
    meaning the engine could not decide and no human has yet (open question Q1,
    resolved 2026-09-19). It never appears in a finalized statement and its
    presence blocks finalization.
    """

    OPERATING = "OPERATING"
    INVESTING = "INVESTING"
    FINANCING = "FINANCING"
    INCOME_TAX = "INCOME_TAX"
    DISCONTINUED_OPERATION = "DISCONTINUED_OPERATION"
    UNCLASSIFIED = "UNCLASSIFIED"

    @property
    def is_ifrs18_category(self) -> bool:
        """False only for the technical ``UNCLASSIFIED`` state."""
        return self is not Ifrs18Category.UNCLASSIFIED


class Ifrs18Subcategory(StrEnum):
    """Presentational detail. No subtotal ever depends on a subcategory."""

    OPERATING_REVENUE = "OPERATING_REVENUE"
    OPERATING_EXPENSE = "OPERATING_EXPENSE"
    OPERATING_OTHER = "OPERATING_OTHER"
    INVESTING_INCOME = "INVESTING_INCOME"
    INVESTING_EXPENSE = "INVESTING_EXPENSE"
    FINANCING_INCOME = "FINANCING_INCOME"
    FINANCING_EXPENSE = "FINANCING_EXPENSE"
    INCOME_TAX_EXPENSE = "INCOME_TAX_EXPENSE"
    INCOME_TAX_INCOME = "INCOME_TAX_INCOME"
    DISCONTINUED_RESULT = "DISCONTINUED_RESULT"


#: Every subcategory belongs to exactly one category. Enforced by
#: ``tests/test_enums.py`` so a subcategory added later cannot be orphaned.
SUBCATEGORY_TO_CATEGORY: dict[Ifrs18Subcategory, Ifrs18Category] = {
    Ifrs18Subcategory.OPERATING_REVENUE: Ifrs18Category.OPERATING,
    Ifrs18Subcategory.OPERATING_EXPENSE: Ifrs18Category.OPERATING,
    Ifrs18Subcategory.OPERATING_OTHER: Ifrs18Category.OPERATING,
    Ifrs18Subcategory.INVESTING_INCOME: Ifrs18Category.INVESTING,
    Ifrs18Subcategory.INVESTING_EXPENSE: Ifrs18Category.INVESTING,
    Ifrs18Subcategory.FINANCING_INCOME: Ifrs18Category.FINANCING,
    Ifrs18Subcategory.FINANCING_EXPENSE: Ifrs18Category.FINANCING,
    Ifrs18Subcategory.INCOME_TAX_EXPENSE: Ifrs18Category.INCOME_TAX,
    Ifrs18Subcategory.INCOME_TAX_INCOME: Ifrs18Category.INCOME_TAX,
    Ifrs18Subcategory.DISCONTINUED_RESULT: Ifrs18Category.DISCONTINUED_OPERATION,
}


class SubtotalKey(StrEnum):
    """Subtotals IFRS 18 requires in the statement of profit or loss.

    Operating profit and profit before financing and income taxes are both
    presented **even when they are equal** (F9 / open question Q3).
    """

    OPERATING_PROFIT = "OPERATING_PROFIT"
    PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES = "PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES"
    PROFIT_BEFORE_TAX = "PROFIT_BEFORE_TAX"
    PROFIT_FROM_CONTINUING_OPERATIONS = "PROFIT_FROM_CONTINUING_OPERATIONS"
    PROFIT_FOR_THE_PERIOD = "PROFIT_FOR_THE_PERIOD"


class ClassificationMethod(StrEnum):
    RULE = "RULE"
    RESIDUAL_DEFAULT = "RESIDUAL_DEFAULT"
    AI = "AI"
    USER = "USER"
    UNRESOLVED = "UNRESOLVED"


class ConfidenceBand(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RuleSourceType(StrEnum):
    """Ordered exactly as spec §25 ranks authority, most authoritative first."""

    IFRS_STANDARD = "IFRS_STANDARD"
    TAXONOMY = "TAXONOMY"
    IASB_EDUCATIONAL = "IASB_EDUCATIONAL"
    BIG4 = "BIG4"
    OTHER = "OTHER"


class RuleVerificationStatus(StrEnum):
    """How thoroughly a rule's citation has been checked (spec §25).

    ``VERIFIED_SECONDARY`` means the requirement was confirmed against
    reputable secondary sources but not against the issued text of the standard.
    See `07-ifrs18-source-verification.md`.
    """

    UNVERIFIED = "UNVERIFIED"
    VERIFIED_SECONDARY = "VERIFIED_SECONDARY"
    VERIFIED_PRIMARY = "VERIFIED_PRIMARY"


class EvidenceType(StrEnum):
    FINANCIAL_STATEMENT = "FINANCIAL_STATEMENT"
    NOTE = "NOTE"
    RULE_SOURCE = "RULE_SOURCE"
    USER_STATEMENT = "USER_STATEMENT"
    BUSINESS_ACTIVITY = "BUSINESS_ACTIVITY"


class EvidenceProducer(StrEnum):
    RULE = "RULE"
    AI = "AI"
    USER = "USER"


# ---------------------------------------------------------------------------
# Entity facts (spec §10)
# ---------------------------------------------------------------------------


class ActivityType(StrEnum):
    """Business activities.

    The first two are IFRS 18's *specified* main business activities (F10); an
    entity may have more than one (IFRS 18 B30). The remainder are descriptive
    and never drive classification on their own.
    """

    INVESTING_IN_ASSETS = "INVESTING_IN_ASSETS"
    PROVIDING_FINANCING_TO_CUSTOMERS = "PROVIDING_FINANCING_TO_CUSTOMERS"
    FINANCIAL_SERVICES = "FINANCIAL_SERVICES"
    INVESTMENT = "INVESTMENT"
    LENDING = "LENDING"
    REAL_ESTATE = "REAL_ESTATE"
    OTHER = "OTHER"

    @property
    def is_specified_main_business_activity(self) -> bool:
        return self in _SPECIFIED_MAIN_BUSINESS_ACTIVITIES


_SPECIFIED_MAIN_BUSINESS_ACTIVITIES = frozenset(
    {
        ActivityType.INVESTING_IN_ASSETS,
        ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS,
    }
)


class ActivitySource(StrEnum):
    USER = "USER"
    AI_SUGGESTED = "AI_SUGGESTED"
    DOCUMENT = "DOCUMENT"


class QuestionScope(StrEnum):
    """Whether a required fact settles once per entity or once per line.

    ``COMPANY`` — a specified main business activity; one answer settles every
    affected line. ``LINE`` — the risk a derivative manages under IFRS 18 B72;
    two derivative lines in one statement can manage different risks, so each is
    asked separately (open question Q4, resolved 2026-09-19).
    """

    COMPANY = "COMPANY"
    LINE = "LINE"


class QuestionAnswer(StrEnum):
    YES = "YES"
    NO = "NO"
    NOT_SURE = "NOT_SURE"


class ReviewAction(StrEnum):
    ACCEPTED = "ACCEPTED"
    OVERRIDDEN = "OVERRIDDEN"
    DEFERRED = "DEFERRED"


# ---------------------------------------------------------------------------
# Projects, files and statements
# ---------------------------------------------------------------------------


class UserRole(StrEnum):
    OWNER = "OWNER"
    PREPARER = "PREPARER"
    REVIEWER = "REVIEWER"
    VIEWER = "VIEWER"


class IdentifierScheme(StrEnum):
    KR_BRN = "KR_BRN"
    DART = "DART"
    LEI = "LEI"


class ProjectBasis(StrEnum):
    CONSOLIDATED = "CONSOLIDATED"
    SEPARATE = "SEPARATE"


class ProjectStatus(StrEnum):
    DRAFT = "DRAFT"
    UPLOADED = "UPLOADED"
    EXTRACTED = "EXTRACTED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    CLASSIFIED = "CLASSIFIED"
    IN_REVIEW = "IN_REVIEW"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    FINALIZED = "FINALIZED"


class ReconciliationStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"


class ScanStatus(StrEnum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    SKIPPED = "SKIPPED"


class ParseStatus(StrEnum):
    PENDING = "PENDING"
    PARSED = "PARSED"
    FAILED = "FAILED"


class StatementType(StrEnum):
    INCOME_STATEMENT = "INCOME_STATEMENT"
    OTHER_COMPREHENSIVE_INCOME = "OTHER_COMPREHENSIVE_INCOME"
    BALANCE_SHEET = "BALANCE_SHEET"
    CASH_FLOW = "CASH_FLOW"
    CHANGES_IN_EQUITY = "CHANGES_IN_EQUITY"


class StatementSection(StrEnum):
    PL = "PL"
    OCI = "OCI"
    BS = "BS"


class SubtotalKind(StrEnum):
    """What a subtotal printed in the *source* document represents.

    These are reconciliation targets, never inputs to our own arithmetic.
    ``REPORTED_OPERATING_PROFIT`` is the "before" figure on the impact screen
    (open question Q2, resolved 2026-09-19).
    """

    GROSS_PROFIT = "GROSS_PROFIT"
    REPORTED_OPERATING_PROFIT = "REPORTED_OPERATING_PROFIT"
    PROFIT_BEFORE_TAX = "PROFIT_BEFORE_TAX"
    PROFIT_FOR_THE_PERIOD = "PROFIT_FOR_THE_PERIOD"
    OTHER = "OTHER"


class SignNormalization(StrEnum):
    """How a raw cell value became a signed profit-or-loss effect.

    Architecture §5: income is positive, expense negative, always. Recording the
    transformation keeps it auditable.
    """

    AS_IS = "AS_IS"
    NEGATED = "NEGATED"
    PARENTHESES_NEGATED = "PARENTHESES_NEGATED"
    EXPENSE_COLUMN_NEGATED = "EXPENSE_COLUMN_NEGATED"


class DecompositionStatus(StrEnum):
    """State of an aggregate caption such as 영업외수익 (open question Q5).

    A ``DECOMPOSED`` parent is excluded from every category sum, exactly like a
    subtotal, so its components are counted once. ``ACCEPTED_AGGREGATE`` records
    that the user could not decompose it; the limitation is written to the audit
    trail and surfaced on the impact screen.
    """

    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"
    DECOMPOSED = "DECOMPOSED"
    ACCEPTED_AGGREGATE = "ACCEPTED_AGGREGATE"


class NormalizationMethod(StrEnum):
    EXACT = "EXACT"
    SYNONYM = "SYNONYM"
    FUZZY = "FUZZY"
    AI = "AI"
    MANUAL = "MANUAL"


class AccountNature(StrEnum):
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"


class SynonymMatchType(StrEnum):
    EXACT = "EXACT"
    NORMALIZED = "NORMALIZED"
    REGEX = "REGEX"


# ---------------------------------------------------------------------------
# Exports and audit
# ---------------------------------------------------------------------------


class ExportFormat(StrEnum):
    XLSX = "XLSX"
    PDF = "PDF"


class ExportStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


class ActorType(StrEnum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    AI = "AI"


class AuditAction(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    DELETED = "DELETED"
    EXTRACTED = "EXTRACTED"
    DECOMPOSED = "DECOMPOSED"
    CLASSIFIED = "CLASSIFIED"
    ACCEPTED = "ACCEPTED"
    OVERRIDDEN = "OVERRIDDEN"
    QUESTION_ANSWERED = "QUESTION_ANSWERED"
    FINALIZED = "FINALIZED"
    EXPORTED = "EXPORTED"
