"""Operational commands.

python -m app.cli seed-accounts
"""

from __future__ import annotations

import asyncio
import sys

from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine, get_session_factory
from app.services.seeding import seed_accounts, seed_rules

log = get_logger(__name__)


async def _seed_accounts() -> int:
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


async def _seed_rules() -> int:
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


async def _seed_all() -> int:
    await _seed_accounts()
    return await _seed_rules()


COMMANDS = {
    "seed-accounts": _seed_accounts,
    "seed-rules": _seed_rules,
    "seed": _seed_all,
}


def main(argv: list[str]) -> int:
    configure_logging("INFO", json_output=False)
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(f"usage: python -m app.cli {{{'|'.join(COMMANDS)}}}", file=sys.stderr)
        return 2
    return asyncio.run(COMMANDS[argv[1]]())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
