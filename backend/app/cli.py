"""Operational commands.

    python -m app.cli seed
    python -m app.cli validate statement.xlsx

``seed`` loads the account dictionary and rule set into the database.
``validate`` is the opposite kind of command: it touches no database at all and
runs one real statement through the whole pipeline, printing what happened at
every stage. That is the command to reach for when somebody hands over an
actual filing and the question is whether this product reads *it* correctly.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path

from app.adapters.ingest.grid import ExtractOptions
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine, get_session_factory
from app.services.dry_run import run_and_report
from app.services.seeding import seed_accounts, seed_rules

log = get_logger(__name__)


async def _seed_accounts(_argv: list[str]) -> int:
    async with get_session_factory()() as session:
        result = await seed_accounts(session)
        await session.commit()

    log.info(
        "accounts_seeded",
        version=result.version,
        created=result.created,
        updated=result.updated,
        deactivated=result.deactivated,
        synonyms_created=result.synonyms_created,
        synonyms_removed=result.synonyms_removed,
    )
    print(
        f"catalog {result.version}: {result.created} created, {result.updated} updated, "
        f"{result.deactivated} deactivated, {result.synonyms_created} synonyms added, "
        f"{result.synonyms_removed} synonyms removed"
    )
    return 0


async def _seed_rules(_argv: list[str]) -> int:
    async with get_session_factory()() as session:
        result = await seed_rules(session)
        await session.commit()

    log.info(
        "rules_seeded",
        version=result.version,
        created=result.created,
        updated=result.updated,
        deactivated=result.deactivated,
    )
    print(
        f"rule set {result.version}: {result.created} created, {result.updated} updated, "
        f"{result.deactivated} deactivated"
    )
    await dispose_engine()
    return 0


async def _seed_all(argv: list[str]) -> int:
    await _seed_accounts(argv)
    return await _seed_rules(argv)


async def _validate(argv: list[str]) -> int:
    """Run one file through every stage and print the report.

    Exits non-zero when the file would not produce a shippable result, so this
    is usable as a check — but "non-zero" here means *this file needs work*,
    which on a first run against a real filing is the expected outcome and not
    a bug.
    """
    parser = argparse.ArgumentParser(
        prog="python -m app.cli validate",
        description="Run a real income statement through the pipeline. No database, no network.",
    )
    parser.add_argument("file", type=Path, help="the statement: .xlsx, .xls, .csv or .pdf")
    parser.add_argument("--sheet", help="worksheet name, if detection picks the wrong one")
    parser.add_argument("--header-row", type=int, help="1-based row holding the column headers")
    parser.add_argument("--label-column", type=int, help="1-based column holding the captions")
    parser.add_argument("--amount-column", type=int, help="1-based column holding the figures")
    parser.add_argument("--note-column", type=int, help="1-based column holding note references")
    parser.add_argument(
        "--period",
        type=int,
        default=0,
        help="which period column to read; 0 is the first, normally the current one",
    )
    parser.add_argument(
        "--basis",
        choices=["CONSOLIDATED", "SEPARATE"],
        default="CONSOLIDATED",
        help="XBRL only: a filing carries both, so which one to read has to be said",
    )
    parser.add_argument(
        "--from",
        dest="period_start",
        type=dt.date.fromisoformat,
        help="XBRL only: reporting period start, e.g. 2026-01-01",
    )
    parser.add_argument(
        "--to",
        dest="period_end",
        type=dt.date.fromisoformat,
        help="XBRL only: reporting period end, e.g. 2026-06-30",
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        help=(
            "let the assistant suggest for lines no rule decided. Off by default: "
            "a coverage number that depends on a model does not measure the rules."
        ),
    )
    parser.add_argument("--out", type=Path, help="write the report here instead of stdout")
    args = parser.parse_args(argv)

    if not args.file.is_file():
        print(f"{args.file} is not a file", file=sys.stderr)
        return 2

    report, shippable = run_and_report(
        args.file,
        options=ExtractOptions(
            sheet=args.sheet,
            header_row=args.header_row,
            label_column=args.label_column,
            amount_column=args.amount_column,
            note_column=args.note_column,
            period_index=args.period,
            basis=args.basis,
            period_start=args.period_start,
            period_end=args.period_end,
        ),
        use_ai=args.ai,
    )

    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"report written to {args.out}")
    else:
        print(report)
    return 0 if shippable else 1


COMMANDS: dict[str, Callable[[list[str]], Coroutine[None, None, int]]] = {
    "seed-accounts": _seed_accounts,
    "seed-rules": _seed_rules,
    "seed": _seed_all,
    "validate": _validate,
}


def main(argv: list[str]) -> int:
    # Human-readable, not JSON: every one of these commands is run by a person
    # at a terminal who wants to read the output.
    configure_logging("INFO", json_output=False)
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(f"usage: python -m app.cli {{{'|'.join(COMMANDS)}}} [options]", file=sys.stderr)
        return 2
    return asyncio.run(COMMANDS[argv[1]](argv[2:]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
