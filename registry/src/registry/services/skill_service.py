import hashlib
import json
import logging
from typing import Any

from beanie import PydanticObjectId

from registry_pkgs.models import ExtendedSkill as Skill
from registry_pkgs.models import ExtendedSkillFile as SkillFile

logger = logging.getLogger(__name__)

CONTENT_HASH_FORMAT = "jarvis-skill-content-v1"
CONTENT_HASH_PREFIX = "sha256:"


class SkillContentUnavailableError(ValueError):
    """Raised when Registry cannot return every byte needed to sync a skill."""


def _canonical_manifest(skill: Skill) -> dict[str, Any]:
    # The manifest uses a structured envelope so additional fields (e.g. files,
    # frontmatter metadata) can be added in the future without changing the format
    # version or invalidating existing hashes.
    return {
        "format": CONTENT_HASH_FORMAT,
        "skill": {
            "body": skill.body,
        },
    }


def compute_skill_content_hash(skill: Skill) -> str:
    """Return SHA-256 of the canonical, UTF-8 JSON skill manifest.

    Currently the manifest only includes skill.body. The envelope structure is
    preserved so additional fields can be added in future versions without
    changing the format identifier.
    """
    manifest = _canonical_manifest(skill)
    try:
        encoded = json.dumps(
            manifest,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as e:
        raise SkillContentUnavailableError("Skill body is not serializable as canonical JSON") from e
    return f"{CONTENT_HASH_PREFIX}{hashlib.sha256(encoded).hexdigest()}"


def _assign_content_hash(skill: Skill) -> None:
    skill.contentHash = compute_skill_content_hash(skill)


async def list_skills() -> list[Skill]:
    """Return body-only skills (fileCount == 0) with freshly computed content hashes."""
    skills = await Skill.find({"fileCount": 0}).sort("+updatedAt").to_list()
    for skill in skills:
        _assign_content_hash(skill)

    logger.debug("list_skills: returned %d skills (fileCount=0)", len(skills))
    return skills


async def get_skill_with_files(
    skill_id: PydanticObjectId,
) -> tuple[Skill, list[SkillFile]]:
    """Fetch a skill and its files, then compute its current hash without writing."""
    skill = await Skill.get(skill_id)
    if skill is None:
        raise ValueError(f"Skill {skill_id} not found")

    skill_files = await SkillFile.find(SkillFile.skillId == skill_id).to_list()
    _assign_content_hash(skill)
    logger.debug("get_skill_with_files: skill=%s, files=%d", skill.name, len(skill_files))
    return skill, skill_files
