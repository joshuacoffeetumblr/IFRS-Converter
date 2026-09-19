"""Architecture §5: binary floats must never appear in the accounting core.

Ruff cannot enforce this — ``flake8-tidy-imports`` only inspects imports and is
blind to a bare ``float`` annotation or call. So the rule is enforced here, by
walking the AST of every module under ``app/domain``.

The accounting engine works exclusively in ``decimal.Decimal``. A single
``float`` in a subtotal is enough to break the exact total-invariance check that
the whole reconciliation gate rests on.
"""

from __future__ import annotations

import ast
from pathlib import Path

DOMAIN_ROOT = Path(__file__).resolve().parent.parent / "app" / "domain"


def _float_usages(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, context) for every reference to the ``float`` builtin."""
    found: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "float":
            found.append((node.lineno, "name reference"))
        elif isinstance(node, ast.Attribute) and node.attr == "float":
            found.append((node.lineno, "attribute reference"))

    return found


def test_guard_detects_float() -> None:
    """The guard must fail on code that uses float, or it proves nothing."""
    sample = ast.parse("def rate(x: float) -> float:\n    return x * 1.5\n")

    assert _float_usages(sample), "guard failed to detect an obvious float usage"


def test_domain_layer_contains_no_float() -> None:
    if not DOMAIN_ROOT.exists():
        return

    offenders: list[str] = []
    for path in sorted(DOMAIN_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, context in _float_usages(tree):
            offenders.append(f"{path.relative_to(DOMAIN_ROOT.parent.parent)}:{lineno} ({context})")

    assert not offenders, (
        "float is banned in the accounting domain layer (architecture §5); "
        "use decimal.Decimal:\n  " + "\n  ".join(offenders)
    )
