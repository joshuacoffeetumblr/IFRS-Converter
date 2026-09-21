"""The taxonomy concepts this product recognises (data in `xbrl_concepts.json`).

A concept is a **language-independent identifier**. `ifrs-full:Revenue` means
the same thing whether the filing's labels are Korean or English, so mapping on
it removes caption matching entirely — and caption matching is where every
ingest defect so far has come from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.domain.enums import AccountNature, SubtotalKind

_DATA = Path(__file__).with_name("xbrl_concepts.json")


@dataclass(frozen=True, slots=True)
class ConceptDefinition:
    """What one taxonomy concept is, and what this product does with it."""

    concept: str
    order: int
    label_ko: str
    label_en: str
    nature: AccountNature
    #: The canonical account a detail line maps to. `None` for a subtotal.
    account: str | None = None
    #: Set when the filing itself computed this figure. A subtotal is a
    #: reconciliation target, never an input to our own arithmetic (spec §17).
    subtotal: SubtotalKind | None = None
    #: For a note component, the statement caption it breaks down.
    parent: str | None = None

    @property
    def is_subtotal(self) -> bool:
        return self.subtotal is not None

    @property
    def is_deduction(self) -> bool:
        """Whether a positive reported figure is a negative profit effect.

        An XBRL filing reports a deduction as a positive number and leaves the
        sign to the taxonomy. Nothing is inferred from the caption here.
        """
        return self.nature is AccountNature.EXPENSE


@dataclass(frozen=True, slots=True)
class ConceptCatalog:
    version: str
    statement: tuple[ConceptDefinition, ...]
    notes: tuple[ConceptDefinition, ...]

    def get(self, concept: str) -> ConceptDefinition | None:
        return self._by_concept.get(concept)

    @property
    def _by_concept(self) -> dict[str, ConceptDefinition]:
        return {item.concept: item for item in (*self.statement, *self.notes)}

    def components_of(self, concept: str) -> tuple[ConceptDefinition, ...]:
        """The note concepts that break down a statement caption."""
        return tuple(item for item in self.notes if item.parent == concept)


def _read(raw: dict[str, object]) -> ConceptDefinition:
    subtotal = raw.get("subtotal")
    return ConceptDefinition(
        concept=str(raw["concept"]),
        order=int(str(raw["order"])),
        label_ko=str(raw["label_ko"]),
        label_en=str(raw["label_en"]),
        nature=AccountNature(str(raw["nature"])),
        account=str(raw["account"]) if raw.get("account") else None,
        subtotal=SubtotalKind(str(subtotal)) if subtotal else None,
        parent=str(raw["parent"]) if raw.get("parent") else None,
    )


@lru_cache(maxsize=1)
def load_concepts() -> ConceptCatalog:
    payload = json.loads(_DATA.read_text(encoding="utf-8"))
    return ConceptCatalog(
        version=str(payload["version"]),
        statement=tuple(_read(item) for item in payload["concepts"]),
        notes=tuple(_read(item) for item in payload["note_concepts"]),
    )
