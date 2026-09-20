"""What the model is allowed to return, and how it is checked (spec §12).

A model's output is never trusted as an object. It arrives as JSON constrained
by a schema, is validated against that schema here, and is converted into a
domain value only if it survives. Anything that does not is either repaired by
one more round trip or discarded — and discarding is safe, because a missing
suggestion means a human looks at the line, which is where it was going anyway.

The schemas are built from the domain enums rather than written out, so a
category added to the engine cannot be one the model is never allowed to
suggest — or, worse, one it may suggest and nothing can store.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.classification import ClassificationSuggestion, Evidence
from app.domain.enums import (
    SUBCATEGORY_TO_CATEGORY,
    EvidenceProducer,
    EvidenceType,
    Ifrs18Category,
    Ifrs18Subcategory,
)
from app.domain.normalization import AccountSuggestion

#: Categories a model may propose. `UNCLASSIFIED` is deliberately absent: it is
#: what the engine says when nothing decided, not something to suggest.
SUGGESTIBLE_CATEGORIES: tuple[str, ...] = tuple(
    category.value for category in Ifrs18Category if category is not Ifrs18Category.UNCLASSIFIED
)

MAX_REASONING_CHARS = 1200
MAX_EVIDENCE_ITEMS = 4


class InvalidSuggestionError(ValueError):
    """The model's output cannot be read as a suggestion.

    Carries a message written to be sent *back* to the model as the repair
    instruction, so the same sentence that explains the problem here is the one
    that asks for it to be fixed.
    """


@dataclass(frozen=True, slots=True)
class SchemaSpec:
    """A JSON schema plus the instruction that goes with it."""

    name: str
    schema: dict[str, Any]


CLASSIFICATION_SCHEMA = SchemaSpec(
    name="ifrs18_classification_suggestion",
    schema={
        "type": "object",
        "properties": {
            "category": {
                "type": ["string", "null"],
                "enum": [*SUGGESTIBLE_CATEGORIES, None],
                "description": (
                    "The IFRS 18 category this line belongs to, or null if the "
                    "caption does not say. Null is a valid and useful answer."
                ),
            },
            "subcategory": {
                "type": ["string", "null"],
                "enum": [*(item.value for item in Ifrs18Subcategory), None],
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "How certain this is, from 0 to 1.",
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "One or two sentences a reviewer can check, naming what in "
                    "the caption led to the category."
                ),
            },
            "evidence": {
                "type": "array",
                "maxItems": MAX_EVIDENCE_ITEMS,
                "items": {
                    "type": "object",
                    "properties": {
                        "reference": {"type": "string"},
                        "note": {"type": ["string", "null"]},
                    },
                    "required": ["reference", "note"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["category", "subcategory", "confidence", "reasoning", "evidence"],
        "additionalProperties": False,
    },
)


def account_schema(codes: tuple[str, ...]) -> SchemaSpec:
    """The dictionary's codes, as the only answers a model may give.

    Enumerating them is the whole point: a model that cannot name an account
    outside the catalog cannot invent one, and an invented code would fail at
    the database's foreign key anyway — after a reviewer had already seen it on
    screen.
    """
    return SchemaSpec(
        name="normalized_account_suggestion",
        schema={
            "type": "object",
            "properties": {
                "code": {
                    "type": ["string", "null"],
                    "enum": [*codes, None],
                    "description": (
                        "The canonical account this caption maps to, or null if "
                        "none of them fits. Null is expected and safe."
                    ),
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
            },
            "required": ["code", "confidence", "reasoning"],
            "additionalProperties": False,
        },
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _confidence(raw: object) -> Decimal:
    try:
        # Through `str`, never `Decimal(float)`: a float's binary value is not
        # the number the model wrote, and confidence is compared to thresholds.
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError) as exc:
        raise InvalidSuggestionError(f"confidence {raw!r} is not a number") from exc
    if not (Decimal(0) <= value <= Decimal(1)):
        raise InvalidSuggestionError(f"confidence {value} is outside 0 to 1")
    return value


def _text(raw: object, field: str, *, limit: int) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidSuggestionError(f"{field} must be a non-empty string")
    return raw.strip()[:limit]


def read_classification(payload: dict[str, Any]) -> ClassificationSuggestion:
    """Turn validated JSON into a domain suggestion, or refuse it."""
    raw_category = payload.get("category")
    raw_subcategory = payload.get("subcategory")

    category: Ifrs18Category | None = None
    if raw_category is not None:
        try:
            category = Ifrs18Category(raw_category)
        except ValueError as exc:
            raise InvalidSuggestionError(
                f"{raw_category!r} is not an IFRS 18 category. Use one of: "
                f"{', '.join(SUGGESTIBLE_CATEGORIES)}, or null."
            ) from exc
        if category is Ifrs18Category.UNCLASSIFIED:
            raise InvalidSuggestionError("UNCLASSIFIED is not a suggestion. Return null instead.")

    subcategory: Ifrs18Subcategory | None = None
    if raw_subcategory is not None:
        try:
            subcategory = Ifrs18Subcategory(raw_subcategory)
        except ValueError as exc:
            raise InvalidSuggestionError(
                f"{raw_subcategory!r} is not an IFRS 18 subcategory."
            ) from exc
        if category is None:
            raise InvalidSuggestionError(
                "A subcategory without a category says nothing. Give both, or null."
            )
        if SUBCATEGORY_TO_CATEGORY[subcategory] is not category:
            raise InvalidSuggestionError(
                f"{subcategory.value} belongs to "
                f"{SUBCATEGORY_TO_CATEGORY[subcategory].value}, not {category.value}."
            )

    confidence = _confidence(payload.get("confidence"))
    if category is None:
        # "I don't know, but I'm sure of it" is not a thing the rest of the
        # system can act on, so it is normalized away here.
        confidence = Decimal(0)

    return ClassificationSuggestion(
        category=category,
        subcategory=subcategory,
        confidence=confidence,
        reasoning=_text(payload.get("reasoning"), "reasoning", limit=MAX_REASONING_CHARS),
        evidence=_evidence(payload.get("evidence")),
    )


def _evidence(raw: object) -> tuple[Evidence, ...]:
    if not isinstance(raw, list):
        return ()
    items: list[Evidence] = []
    for entry in raw[:MAX_EVIDENCE_ITEMS]:
        if not isinstance(entry, dict):
            continue
        reference = entry.get("reference")
        if not isinstance(reference, str) or not reference.strip():
            continue
        note = entry.get("note")
        items.append(
            Evidence(
                # Evidence a model offers is evidence *from the document* it was
                # shown — a note reference — and it is labelled as produced by
                # the AI so the audit trail never implies a rule said it.
                type=EvidenceType.NOTE.value,
                reference=reference.strip()[:300],
                produced_by=EvidenceProducer.AI.value,
                note=note.strip()[:MAX_REASONING_CHARS] if isinstance(note, str) else None,
            )
        )
    return tuple(items)


def read_account(payload: dict[str, Any], *, known_codes: frozenset[str]) -> AccountSuggestion:
    raw_code = payload.get("code")
    code: str | None = None
    if raw_code is not None:
        if not isinstance(raw_code, str) or raw_code not in known_codes:
            raise InvalidSuggestionError(
                f"{raw_code!r} is not an account in the dictionary. Return null "
                "if none of the listed codes fits."
            )
        code = raw_code

    confidence = _confidence(payload.get("confidence"))
    return AccountSuggestion(
        code=code,
        confidence=confidence if code else Decimal(0),
        reasoning=_text(payload.get("reasoning"), "reasoning", limit=MAX_REASONING_CHARS),
    )
