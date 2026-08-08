#!/usr/bin/env python3
"""Backfill persisted contentHash values for Skills.

The script is a dry run by default. Pass ``--apply`` to update MongoDB.

Examples:
    MONGO_URI='<mongo-uri>' uv run python scripts/backfill_skill_content_hashes.py
    MONGO_URI='<mongo-uri>' uv run python scripts/backfill_skill_content_hashes.py --apply
    MONGO_URI='<mongo-uri>' uv run python scripts/backfill_skill_content_hashes.py --apply --force
"""

import argparse
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from beanie import PydanticObjectId

from registry.services.skill_service import SkillContentUnavailableError, compute_skill_content_hash
from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import ExtendedSkill as Skill

logger = logging.getLogger(__name__)


@dataclass
class BackfillStats:
    scanned: int = 0
    would_update: int = 0
    updated: int = 0
    unchanged: int = 0
    unavailable: int = 0
    conflicts: int = 0


def _database_name(mongo_uri: str) -> str:
    database_name = urlsplit(mongo_uri).path.lstrip("/")
    if not database_name or "/" in database_name:
        raise ValueError("MONGO_URI must contain exactly one database name")
    return database_name


def _skill_query(force: bool, skill_id: PydanticObjectId | None) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if not force:
        query["$or"] = [{"contentHash": None}, {"contentHash": {"$exists": False}}]
    if skill_id is not None:
        query["_id"] = skill_id
    return query


def _update_filter(skill: Skill) -> dict[str, Any]:
    query: dict[str, Any] = {"_id": skill.id, "updatedAt": skill.updatedAt}
    if skill.contentHash is None:
        query["$or"] = [{"contentHash": None}, {"contentHash": {"$exists": False}}]
    else:
        query["contentHash"] = skill.contentHash
    return query


async def backfill_content_hashes(
    *,
    apply: bool,
    force: bool,
    skill_id: PydanticObjectId | None,
    limit: int | None,
) -> BackfillStats:
    skills_query = Skill.find(_skill_query(force, skill_id)).sort("+_id")
    skills = await skills_query.to_list(length=limit)
    skills_collection = MongoDB.get_database()["skills"]
    stats = BackfillStats()

    for skill in skills:
        stats.scanned += 1
        try:
            content_hash = compute_skill_content_hash(skill)
        except SkillContentUnavailableError as e:
            stats.unavailable += 1
            logger.warning("SKIP %s (%s): %s", skill.id, skill.name, e)
            continue

        if skill.contentHash == content_hash:
            stats.unchanged += 1
            logger.info("UNCHANGED %s (%s): %s", skill.id, skill.name, content_hash)
            continue

        if not apply:
            stats.would_update += 1
            logger.info("WOULD UPDATE %s (%s): %s", skill.id, skill.name, content_hash)
            continue

        result = await skills_collection.update_one(
            _update_filter(skill),
            {"$set": {"contentHash": content_hash}},
        )
        if result.modified_count == 1:
            stats.updated += 1
            logger.info("UPDATED %s (%s): %s", skill.id, skill.name, content_hash)
            continue

        stats.conflicts += 1
        logger.warning("CONFLICT %s (%s): document changed during backfill", skill.id, skill.name)

    return stats


async def run(args: argparse.Namespace) -> int:
    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis")
    database_name = _database_name(mongo_uri)
    skill_id = PydanticObjectId(args.skill_id) if args.skill_id else None

    logger.info("Connecting to MongoDB database %s", database_name)
    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=mongo_uri,
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
        db_name=database_name,
    )
    try:
        stats = await backfill_content_hashes(
            apply=args.apply,
            force=args.force,
            skill_id=skill_id,
            limit=args.limit,
        )
    finally:
        await MongoDB.close_db()

    mode = "APPLY" if args.apply else "DRY RUN"
    logger.info(
        "%s complete: scanned=%d would_update=%d updated=%d unchanged=%d unavailable=%d conflicts=%d",
        mode,
        stats.scanned,
        stats.would_update,
        stats.updated,
        stats.unchanged,
        stats.unavailable,
        stats.conflicts,
    )
    return 1 if stats.unavailable or stats.conflicts else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill persisted contentHash values for Skills")
    parser.add_argument("--apply", action="store_true", help="Write hashes to MongoDB; default is dry-run")
    parser.add_argument("--force", action="store_true", help="Recompute Skills that already have contentHash")
    parser.add_argument("--skill-id", help="Process only one Skill ObjectID")
    parser.add_argument("--limit", type=int, help="Process at most this many Skills")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
    )
    raise SystemExit(asyncio.run(run(_parse_args())))


if __name__ == "__main__":
    main()
