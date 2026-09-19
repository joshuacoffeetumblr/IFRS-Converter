"""Loading the account catalog from its data file.

The catalog is data rather than code (``normalized_accounts.json``) so an
accountant can review and diff it without reading Python — the same reasoning
that applies to classification rules in spec §25. This module is the only place
that reads it, which keeps ``app.domain.accounts`` pure and testable against
definitions constructed in the test itself.
"""

from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from typing import Any

from app.domain.accounts import (
    DEFAULT_FUZZY_MARGIN,
    DEFAULT_FUZZY_THRESHOLD,
    AccountDefinition,
    AccountDictionary,
)
from app.domain.enums import AccountNature, StatementSection

CATALOG_RESOURCE = "normalized_accounts.json"


class CatalogError(ValueError):
    """The catalog file is malformed."""


def _definition(entry: dict[str, Any]) -> AccountDefinition:
    try:
        return AccountDefinition(
            code=entry["code"],
            label_ko=entry["label_ko"],
            label_en=entry["label_en"],
            section=StatementSection(entry.get("section", "PL")),
            nature=AccountNature(entry["nature"]) if entry.get("nature") else None,
            parent_code=entry.get("parent_code"),
            ambiguous_by_default=bool(entry.get("ambiguous_by_default", False)),
            synonyms_ko=tuple(entry.get("synonyms_ko", ())),
            synonyms_en=tuple(entry.get("synonyms_en", ())),
        )
    except (KeyError, ValueError) as exc:
        raise CatalogError(f"invalid catalog entry {entry.get('code', entry)!r}: {exc}") from exc


def load_catalog() -> tuple[str, tuple[AccountDefinition, ...]]:
    """Return the catalog version and its definitions."""
    raw = resources.files("app.data").joinpath(CATALOG_RESOURCE).read_text(encoding="utf-8")
    document = json.loads(raw)

    version = document.get("version")
    if not version:
        raise CatalogError("catalog has no version")

    definitions = tuple(_definition(entry) for entry in document.get("accounts", ()))
    if not definitions:
        raise CatalogError("catalog contains no accounts")

    codes = [definition.code for definition in definitions]
    duplicates = {code for code in codes if codes.count(code) > 1}
    if duplicates:
        raise CatalogError(f"duplicate account codes: {sorted(duplicates)}")

    known = set(codes)
    orphans = {
        definition.code: definition.parent_code
        for definition in definitions
        if definition.parent_code and definition.parent_code not in known
    }
    if orphans:
        raise CatalogError(f"accounts reference unknown parents: {orphans}")

    return version, definitions


@lru_cache(maxsize=1)
def get_dictionary(
    fuzzy_threshold: Decimal = DEFAULT_FUZZY_THRESHOLD,
    fuzzy_margin: Decimal = DEFAULT_FUZZY_MARGIN,
) -> AccountDictionary:
    _, definitions = load_catalog()
    return AccountDictionary(
        definitions, fuzzy_threshold=fuzzy_threshold, fuzzy_margin=fuzzy_margin
    )


@lru_cache(maxsize=1)
def catalog_version() -> str:
    version, _ = load_catalog()
    return version
