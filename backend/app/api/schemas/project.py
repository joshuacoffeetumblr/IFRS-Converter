"""Project payloads."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import Field, model_validator

from app.api.schemas.common import ApiModel
from app.domain.enums import (
    IdentifierScheme,
    ProjectBasis,
    ProjectStatus,
    ReconciliationStatus,
)


class CompanyInput(ApiModel):
    name: str = Field(min_length=1, max_length=300)
    identifier: str | None = Field(default=None, max_length=100)
    identifier_scheme: IdentifierScheme | None = None
    jurisdiction: str = Field(default="KR", min_length=2, max_length=2)
    #: Informational only. Spec §10 forbids inferring main business activities
    #: from an industry code, and the API must not imply otherwise.
    industry_code: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _scheme_requires_identifier(self) -> CompanyInput:
        if self.identifier_scheme and not self.identifier:
            raise ValueError("identifier_scheme requires an identifier")
        if self.identifier and not self.identifier_scheme:
            raise ValueError("identifier requires an identifier_scheme")
        return self


class CompanyResponse(ApiModel):
    id: uuid.UUID
    name: str
    identifier: str | None
    identifier_scheme: IdentifierScheme | None
    jurisdiction: str


class CreateProjectRequest(ApiModel):
    name: str = Field(min_length=1, max_length=300)
    company: CompanyInput
    fiscal_year: int = Field(ge=1900, le=2200)
    period_start: dt.date
    period_end: dt.date
    basis: ProjectBasis
    presentation_currency: str = Field(default="KRW", min_length=3, max_length=3)
    #: Power of ten the source is stated in: 6 means 백만원.
    presentation_scale: int = Field(default=0, ge=0, le=12)

    @model_validator(mode="after")
    def _period_is_ordered(self) -> CreateProjectRequest:
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        return self


class BlockingReason(ApiModel):
    code: str
    count: int
    detail: str | None = None


class ProjectProgress(ApiModel):
    """Everything the review screen needs to decide what to show next.

    ``can_finalize`` is computed server-side and the client must not re-derive
    it: the rule is an accounting one, and two implementations of it would
    eventually disagree.
    """

    lines_total: int = 0
    lines_classified: int = 0
    requires_review: int = 0
    reviewed: int = 0
    open_questions: int = 0
    can_finalize: bool = False
    blocking_reasons: list[BlockingReason] = Field(default_factory=list)


class ProjectResponse(ApiModel):
    id: uuid.UUID
    name: str
    company: CompanyResponse
    fiscal_year: int
    period_start: dt.date
    period_end: dt.date
    basis: ProjectBasis
    presentation_currency: str
    presentation_scale: int
    status: ProjectStatus
    reconciliation_status: ReconciliationStatus
    rule_set_version: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    finalized_at: dt.datetime | None
    progress: ProjectProgress = Field(default_factory=ProjectProgress)


class ProjectSummary(ApiModel):
    id: uuid.UUID
    name: str
    fiscal_year: int
    basis: ProjectBasis
    status: ProjectStatus
    reconciliation_status: ReconciliationStatus
    created_at: dt.datetime
