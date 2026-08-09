import logging

from beanie import PydanticObjectId

from registry_pkgs.models import ExtendedSkill as Skill
from registry_pkgs.models import ExtendedSkillFile as SkillFile

logger = logging.getLogger(__name__)


async def list_skills() -> list[Skill]:
    """Return body-only skills (fileCount == 0)."""
    skills = await Skill.find({"fileCount": 0}).sort("+updatedAt").to_list()
    logger.debug("list_skills: returned %d skills (fileCount=0)", len(skills))
    return skills


async def get_skill_with_files(
    skill_id: PydanticObjectId,
) -> tuple[Skill, list[SkillFile]]:
    """Fetch a skill and its files."""
    skill = await Skill.get(skill_id)
    if skill is None:
        raise ValueError(f"Skill {skill_id} not found")

    skill_files = await SkillFile.find(SkillFile.skillId == skill_id).to_list()
    logger.debug("get_skill_with_files: skill=%s, files=%d", skill.name, len(skill_files))
    return skill, skill_files
