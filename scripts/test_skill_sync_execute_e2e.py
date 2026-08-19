"""
Trigger a real Skill Sync run and verify MongoDB results.

Env vars:
    COOKIE       Session cookie for the registry UI/API (required)
    BASE_URL     Registry base URL (default: http://localhost)
    MONGO_URI    MongoDB URI (loaded from .env; default: mongodb://127.0.0.1:27017/jarvis)
    SOURCE_ID    Optional skill_sync_sources ObjectId to sync
    SYNC_PATHS   Optional comma-separated repo paths to sync
    UPDATE_SOURCE_PATHS
                 Set to false to leave the source paths unchanged (default: true)

Usage:
    uv run python scripts/test_skill_sync_execute_e2e.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import dotenv
import httpx
from bson import ObjectId
from pymongo import MongoClient
from pymongo.database import Database

dotenv.load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("skill_sync_execute_e2e")

DEFAULT_BASE_URL = "http://localhost"
DEFAULT_MONGO_URI = "mongodb://127.0.0.1:27017/jarvis"
DEFAULT_SYNC_PATHS = [
    "jarvis-backend-code-review-practices",
    "jarvis-python-fastapi-backend-practices",
    "jarvis-registry-backend-practices",
]
FULL_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TERMINAL_JOB_STATUSES = {"success", "partial_success", "failed"}
TOKEN_TYPES = ["skill_sync_github_access", "skill_sync_github_refresh"]
POLL_INTERVAL_SECONDS = 2
MAX_WAIT_SECONDS = 180


def _extract_csrf(cookie: str) -> str | None:
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == "jarvis_registry_csrf" and value:
            return value
    return None


def _get_database() -> Database:
    uri = os.environ.get("MONGO_URI", DEFAULT_MONGO_URI)
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    parsed = urlparse(uri)
    db_name = (parsed.path or "/jarvis").lstrip("/") or "jarvis"
    return client[db_name]


def _configured_sync_paths() -> list[str]:
    raw_paths = os.environ.get("SYNC_PATHS")
    if not raw_paths:
        return DEFAULT_SYNC_PATHS

    paths = [path.strip().strip("/") for path in raw_paths.split(",")]
    paths = [path for path in paths if path]
    if not paths:
        raise ValueError("SYNC_PATHS was provided but no non-empty paths were found")
    if len(paths) != len(set(paths)):
        raise ValueError("SYNC_PATHS must not contain duplicates")
    return paths


def _should_update_source_paths() -> bool:
    return os.environ.get("UPDATE_SOURCE_PATHS", "true").lower() not in {"0", "false", "no"}


def _json_default(value: Any) -> str:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _print_json(label: str, data: dict[str, Any]) -> None:
    logger.info("%s:\n%s", label, json.dumps(data, indent=2, default=_json_default))


def _safe_source_projection() -> dict[str, int]:
    return {
        "githubAppClientSecretEncrypted": 0,
    }


def _find_source_by_id(db: Database, source_id: str) -> dict[str, Any]:
    try:
        object_id = ObjectId(source_id)
    except Exception as exc:
        raise ValueError(f"Invalid SOURCE_ID ObjectId: {source_id}") from exc
    source = db.skill_sync_sources.find_one({"_id": object_id}, _safe_source_projection())
    if source is None:
        raise ValueError(f"Skill sync source not found: {source_id}")
    return source


def _find_token_backed_source(db: Database) -> dict[str, Any]:
    now = datetime.now(UTC).replace(tzinfo=None)
    tokens = db.tokens.find(
        {
            "type": {"$in": TOKEN_TYPES},
            "identifier": {"$regex": r"^skillsync:"},
            "expiresAt": {"$gt": now},
        },
        {"token": 0},
    ).sort("expiresAt", -1)

    for token in tokens:
        identifier = token.get("identifier") or ""
        source_id = identifier.split(":", 1)[1]
        source = db.skill_sync_sources.find_one(
            {
                "_id": ObjectId(source_id),
                "status": {"$ne": "deleted"},
            },
            _safe_source_projection(),
        )
        if source is not None:
            logger.info(
                "Selected source %s from %s token expiring at %s",
                source_id,
                token.get("type"),
                token.get("expiresAt"),
            )
            return source

    raise ValueError("No non-deleted skill sync source with an unexpired GitHub skill-sync token was found")


def _summarize_tokens_for_source(db: Database, source_id: ObjectId) -> list[dict[str, Any]]:
    now = datetime.now(UTC).replace(tzinfo=None)
    rows: list[dict[str, Any]] = []
    tokens = db.tokens.find(
        {
            "identifier": f"skillsync:{source_id}",
            "type": {"$in": TOKEN_TYPES},
        },
        {"token": 0},
    ).sort("expiresAt", -1)
    for token in tokens:
        expires_at = token.get("expiresAt")
        rows.append(
            {
                "type": token.get("type"),
                "userId": str(token.get("userId")),
                "expiresAt": expires_at,
                "isExpired": bool(expires_at and expires_at <= now),
            }
        )
    return rows


def _mongo_skill_summary(db: Database, source_id: ObjectId) -> dict[str, Any]:
    source_id_str = str(source_id)
    live_skills = list(
        db.skills.find(
            {
                "source": "github",
                "sourceMetadata.sourceId": source_id_str,
                "deletedAt": None,
            },
            {"body": 0, "frontmatter": 0},
        ).sort("updatedAt", -1)
    )
    skill_ids = [skill["_id"] for skill in live_skills]
    skillfiles_count = db.skillfiles.count_documents({"skillId": {"$in": skill_ids}}) if skill_ids else 0
    return {
        "liveSkillCount": len(live_skills),
        "skillFileCount": skillfiles_count,
        "skills": [
            {
                "id": skill["_id"],
                "name": skill.get("name"),
                "path": skill.get("path"),
                "fileCount": skill.get("fileCount"),
                "updatedAt": skill.get("updatedAt"),
                "commitSha": (skill.get("sourceMetadata") or {}).get("commitSha"),
            }
            for skill in live_skills
        ],
    }


def _expected_skill_paths(paths: list[str]) -> set[str]:
    expected_paths: set[str] = set()
    for path in paths:
        normalized = path.strip().strip("/")
        if normalized.endswith(".md"):
            expected_paths.add(normalized)
            continue
        expected_paths.add(f"{normalized}/SKILL.md")
    return expected_paths


def _missing_expected_skill_paths(summary: dict[str, Any], paths: list[str]) -> list[str]:
    expected_paths = _expected_skill_paths(paths)
    synced_paths = {str(skill.get("path")) for skill in summary["skills"]}
    return sorted(expected_paths - synced_paths)


def _is_safe_commit_sha(value: Any) -> bool:
    if value in (None, "unknown"):
        return True
    return isinstance(value, str) and bool(FULL_COMMIT_SHA_RE.fullmatch(value))


class SkillSyncExecuteE2E:
    def __init__(self) -> None:
        cookie = os.environ.get("COOKIE", "")
        if not cookie:
            raise ValueError("COOKIE env var is required")

        csrf = os.environ.get("CSRF") or _extract_csrf(cookie)
        if not csrf:
            raise ValueError("CSRF env var is required, or COOKIE must include jarvis_registry_csrf")

        base_url = os.environ.get("BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.api = f"{base_url}/api/v1/skill-sync-sources"
        self.client = httpx.Client(
            headers={
                "Content-Type": "application/json",
                "Cookie": cookie,
                "X-Jarvis-CSRF": csrf,
            },
            follow_redirects=False,
            trust_env=False,
            timeout=30,
        )
        self.db = _get_database()

    def select_source(self) -> dict[str, Any]:
        source_id = os.environ.get("SOURCE_ID")
        source = _find_source_by_id(self.db, source_id) if source_id else _find_token_backed_source(self.db)
        _print_json(
            "Selected source",
            {
                "id": source["_id"],
                "displayName": source.get("displayName"),
                "owner": source.get("owner"),
                "repo": source.get("repo"),
                "ref": source.get("ref"),
                "paths": source.get("paths"),
                "status": source.get("status"),
                "syncStatus": source.get("syncStatus"),
                "tokens": _summarize_tokens_for_source(self.db, source["_id"]),
            },
        )
        return source

    def ensure_source_paths(self, source: dict[str, Any], paths: list[str]) -> dict[str, Any]:
        if not _should_update_source_paths():
            logger.info("Leaving source paths unchanged because UPDATE_SOURCE_PATHS=false")
            return source

        source_id = source["_id"]
        current_paths = source.get("paths") or []
        if current_paths == paths:
            logger.info("Source %s already has target paths: %s", source_id, paths)
            return source

        response = self.client.put(
            f"{self.api}/{source_id}",
            json={
                "paths": paths,
                "skillDiscoveryDepth": 2,
            },
        )
        logger.info("PUT source paths -> %s", response.status_code)
        if response.status_code != 200:
            raise RuntimeError(f"Source path update failed: status={response.status_code}, body={response.text}")

        updated = self.db.skill_sync_sources.find_one({"_id": source_id}, _safe_source_projection())
        if updated is None:
            raise RuntimeError(f"Source disappeared after path update: {source_id}")
        _print_json(
            "Updated source paths",
            {
                "id": updated["_id"],
                "previousPaths": current_paths,
                "paths": updated.get("paths"),
            },
        )
        return updated

    def trigger_sync(self, source_id: ObjectId) -> dict[str, Any]:
        response = self.client.post(f"{self.api}/{source_id}/sync")
        logger.info("POST /sync -> %s", response.status_code)
        if response.status_code != 200:
            raise RuntimeError(f"Sync trigger failed: status={response.status_code}, body={response.text}")

        body = response.json()
        _print_json("Trigger response", body)
        if body.get("needsAuthorization") is True:
            raise RuntimeError("Sync requires GitHub authorization; no usable access/refresh token for this API user")

        job = body.get("job")
        if not job or not job.get("id"):
            raise RuntimeError(f"Sync response did not include a job: {body}")
        return job

    def poll_job(self, source_id: ObjectId, job_id: str) -> dict[str, Any]:
        deadline = time.time() + MAX_WAIT_SECONDS
        last_status = None
        while time.time() < deadline:
            response = self.client.get(f"{self.api}/{source_id}/jobs/{job_id}")
            if response.status_code != 200:
                raise RuntimeError(f"Job poll failed: status={response.status_code}, body={response.text}")

            job = response.json()
            status = job.get("status")
            phase = job.get("phase")
            if (status, phase) != last_status:
                logger.info("Job %s status=%s phase=%s", job_id, status, phase)
                last_status = (status, phase)

            if status in TERMINAL_JOB_STATUSES:
                _print_json("Final job", job)
                return job
            time.sleep(POLL_INTERVAL_SECONDS)

        raise TimeoutError(f"Job {job_id} did not finish within {MAX_WAIT_SECONDS}s")

    def run(self) -> None:
        paths = _configured_sync_paths()
        logger.info("Target sync paths: %s", paths)
        source = self.select_source()
        source = self.ensure_source_paths(source, paths)
        source_id = source["_id"]
        before = _mongo_skill_summary(self.db, source_id)
        _print_json("Mongo skill summary before sync", before)

        job = self.trigger_sync(source_id)
        final_job = self.poll_job(source_id, job["id"])
        after = _mongo_skill_summary(self.db, source_id)
        _print_json("Mongo skill summary after sync", after)

        if final_job.get("status") == "failed":
            raise RuntimeError(f"Sync job failed: {final_job.get('errorCode')} {final_job.get('error')}")
        missing_paths = _missing_expected_skill_paths(after, paths)
        if missing_paths:
            raise RuntimeError(f"Sync finished but expected skill paths are missing in MongoDB: {missing_paths}")
        unsafe_commit_values = [
            {"path": skill.get("path"), "commitSha": skill.get("commitSha")}
            for skill in after["skills"]
            if not _is_safe_commit_sha(skill.get("commitSha"))
        ]
        if unsafe_commit_values:
            raise RuntimeError(f"Unsafe commitSha values found in MongoDB: {unsafe_commit_values}")

        source_after = self.db.skill_sync_sources.find_one({"_id": source_id}, _safe_source_projection())
        if source_after is None:
            raise RuntimeError(f"Source disappeared after sync: {source_id}")
        stats = source_after.get("stats") or {}
        if stats.get("skillCount") != after["liveSkillCount"] or stats.get("fileCount") != after["skillFileCount"]:
            raise RuntimeError(
                "Source stats do not match live MongoDB state: "
                f"stats={stats}, liveSkillCount={after['liveSkillCount']}, "
                f"skillFileCount={after['skillFileCount']}"
            )


def main() -> None:
    try:
        SkillSyncExecuteE2E().run()
    except Exception as exc:
        logger.error("[FAIL] %s", exc)
        sys.exit(1)
    logger.info("[PASS] Skill sync completed and MongoDB contains live GitHub skills")


if __name__ == "__main__":
    main()
