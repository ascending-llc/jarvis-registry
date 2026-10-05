"""CLI: `python -m registry_pkgs.migrations {up|status|wait}`. Configuration comes from env / `.env` only."""

import argparse
import asyncio
import logging
import sys

from pydantic import ValidationError
from pymongo.errors import PyMongoError

from registry_pkgs.core.config import MongoSettings
from registry_pkgs.database.mongodb import create_mongo_client

from . import runner
from .discovery import MigrationDiscoveryError

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m registry_pkgs.migrations", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("up", help="Apply every pending migration (takes the migration lock)")
    subparsers.add_parser("status", help="Show each migration's state; exit 1 on a checksum mismatch")
    subparsers.add_parser("wait", help="Block until no migration is pending (does not take the lock)")
    return parser


async def _run(command: str, settings: MongoSettings) -> int:
    client, db_name = create_mongo_client(settings.mongo_config)
    try:
        await client.admin.command("ping")
        # Never log the URI: it can carry credentials.
        logger.info("Connected to MongoDB database %s", db_name)
        db = client[db_name]
        if command == "up":
            return await runner.up(db, settings.build_version)
        if command == "wait":
            return await runner.wait(db)
        return await runner.status(db)
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    args = _build_parser().parse_args(argv)
    try:
        settings = MongoSettings()
    except ValidationError as exc:
        # include_input=False: the rejected values may be credentials.
        logger.error("Invalid MongoDB settings: %s", exc.errors(include_input=False, include_url=False))
        return runner.EXIT_FAILURE
    try:
        return asyncio.run(_run(args.command, settings))
    except (ValueError, PyMongoError, MigrationDiscoveryError) as exc:
        logger.error("Migration command '%s' failed: %s", args.command, exc)
    except Exception:  # Last resort so the process always exits non-zero with a traceback.
        logger.exception("Migration command '%s' failed unexpectedly", args.command)
    return runner.EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
