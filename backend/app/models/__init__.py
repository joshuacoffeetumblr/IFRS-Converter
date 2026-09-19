"""SQLAlchemy models, implementing `docs/02-erd.md`.

Importing this package registers every table on ``app.db.base.Base.metadata``,
which is what Alembic autogenerate and ``alembic check`` rely on.
"""

from __future__ import annotations

from app.models.account import AccountSynonym, NormalizedAccount
from app.models.audit import AuditLog
from app.models.classification import ClassificationEvidence, Ifrs18Classification
from app.models.company import BusinessActivity, Company
from app.models.impact import Export, ImpactAnalysis
from app.models.project import Project, UploadedFile
from app.models.review import ReviewQuestion, UserReview
from app.models.rule import ClassificationRule
from app.models.statement import FinancialStatement, FinancialStatementLine
from app.models.user import User

__all__ = [
    "AccountSynonym",
    "AuditLog",
    "BusinessActivity",
    "ClassificationEvidence",
    "ClassificationRule",
    "Company",
    "Export",
    "FinancialStatement",
    "FinancialStatementLine",
    "Ifrs18Classification",
    "ImpactAnalysis",
    "NormalizedAccount",
    "Project",
    "ReviewQuestion",
    "UploadedFile",
    "User",
    "UserReview",
]
