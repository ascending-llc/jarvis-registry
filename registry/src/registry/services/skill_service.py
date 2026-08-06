import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from beanie import PydanticObjectId

from registry_pkgs.models import ExtendedSkill as Skill
from registry_pkgs.models import ExtendedSkillFile as SkillFile

logger = logging.getLogger(__name__)

CONTENT_HASH_FORMAT = "jarvis-skill-content-v1"
CONTENT_HASH_PREFIX = "sha256:"


class SkillContentUnavailableError(ValueError):
    """Raised when Registry cannot return every byte needed to sync a skill."""


def _validate_relative_path(relative_path: str) -> None:
    path = PurePosixPath(relative_path)
    if not relative_path or path.is_absolute() or ".." in path.parts or str(path) != relative_path:
        raise SkillContentUnavailableError(f"Skill file has an unsafe relative path: {relative_path!r}")


def _canonical_manifest(skill: Skill, skill_files: list[SkillFile]) -> dict[str, Any]:
    files: list[dict[str, str]] = []
    seen_paths: set[str] = set()

    for skill_file in sorted(skill_files, key=lambda item: item.relativePath.encode("utf-8")):
        _validate_relative_path(skill_file.relativePath)
        if skill_file.relativePath in seen_paths:
            raise SkillContentUnavailableError(f"Skill contains duplicate file path: {skill_file.relativePath}")
        seen_paths.add(skill_file.relativePath)

        if skill_file.isBinary is True:
            raise SkillContentUnavailableError(
                f"Skill file {skill_file.relativePath} is binary and its raw bytes are not available in Registry"
            )
        if skill_file.content is None:
            raise SkillContentUnavailableError(
                f"Skill file {skill_file.relativePath} has no cached content; Registry cannot reconstruct it"
            )
        files.append(
            {
                "relativePath": skill_file.relativePath,
                "content": skill_file.content,
                "isExecutable": skill_file.isExecutable,
            }
        )

    return {
        "format": CONTENT_HASH_FORMAT,
        "skill": {
            "name": skill.name,
            "description": skill.description,
            "alwaysApply": skill.alwaysApply,
            "disableModelInvocation": skill.disableModelInvocation,
            "userInvocable": skill.userInvocable,
            "allowedTools": skill.allowedTools,
            "category": skill.category,
            "frontmatter": skill.frontmatter,
            "body": skill.body,
        },
        "files": files,
    }


def compute_skill_content_hash(skill: Skill, skill_files: list[SkillFile]) -> str:
    """Return SHA-256 of the canonical, UTF-8 JSON skill manifest.

    JSON is serialized with sorted keys, no insignificant whitespace, and Unicode
    characters left unescaped. Files are ordered by the UTF-8 bytes of their safe
    POSIX relative paths. Missing or binary content fails closed because hashing an
    empty placeholder would let a client incorrectly skip an incomplete sync.
    """
    manifest = _canonical_manifest(skill, skill_files)
    try:
        encoded = json.dumps(
            manifest,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as e:
        raise SkillContentUnavailableError("Skill frontmatter is not canonical JSON data") from e
    return f"{CONTENT_HASH_PREFIX}{hashlib.sha256(encoded).hexdigest()}"


def _assign_content_hash(skill: Skill, skill_files: list[SkillFile]) -> None:
    skill.contentHash = compute_skill_content_hash(skill, skill_files)


async def _get_files_by_skill_id(skills: list[Skill]) -> dict[str, list[SkillFile]]:
    skill_ids = [skill.id for skill in skills if skill.id is not None]
    if not skill_ids:
        return {}

    skill_files = await SkillFile.find({"skillId": {"$in": skill_ids}}).to_list()
    files_by_skill_id: defaultdict[str, list[SkillFile]] = defaultdict(list)
    for skill_file in skill_files:
        files_by_skill_id[str(skill_file.skillId)].append(skill_file)
    return dict(files_by_skill_id)


async def list_skills_delta(since: datetime | None = None) -> list[Skill]:
    """Return skills updated at or after the cursor with freshly computed hashes."""
    query: dict[str, Any] = {}
    if since is not None:
        query["updatedAt"] = {"$gte": since}

    skills = await Skill.find(query).sort("+updatedAt").to_list()
    files_by_skill_id = await _get_files_by_skill_id(skills)
    for skill in skills:
        _assign_content_hash(skill, files_by_skill_id.get(str(skill.id), []))

    logger.debug("list_skills_delta: since=%s, returned %d skills", since, len(skills))
    return skills


async def get_skill_with_files(
    skill_id: PydanticObjectId,
) -> tuple[Skill, list[SkillFile]]:
    """Fetch a skill and its files, then compute its current hash without writing."""
    skill = await Skill.get(skill_id)
    if skill is None:
        raise ValueError(f"Skill {skill_id} not found")

    skill_files = await SkillFile.find(SkillFile.skillId == skill_id).to_list()
    _assign_content_hash(skill, skill_files)
    logger.debug("get_skill_with_files: skill=%s, files=%d", skill.name, len(skill_files))
    return skill, skill_files
