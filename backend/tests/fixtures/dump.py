"""Write the synthetic fixtures to disk for manual inspection.

    make fixture

They are generated rather than committed so the statement's arithmetic is
reviewable as code rather than hidden in a binary.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tests.fixtures.korean_income_statement import SignStyle, build_workbook


def main(argv: list[str]) -> int:
    target = Path(argv[1] if len(argv) > 1 else "fixtures")
    target.mkdir(parents=True, exist_ok=True)
    for style in SignStyle:
        name = f"kr_income_statement_{style.value.lower()}.xlsx"
        path = build_workbook(target / name, style=style)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
