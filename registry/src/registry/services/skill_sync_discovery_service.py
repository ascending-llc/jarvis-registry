import logging
from dataclasses import dataclass, field
from typing import Any

import yaml
from pydantic import ValidationError

from registry_pkgs.models.enums import SkillSyncSkillErrorCode
from registry_pkgs.models.skill_sync_job import SkillSyncDiscoverySummary, SkillSyncSkillError

from ..models.skill_frontmatter import SkillFrontmatter
from .skill_sync_github_service import ExtractedAuxFile, ExtractedSkillFolder, ExtractionResult

logger = logging.getLogger(__name__)

MAX_FILES_PER_SKILL = 50


@dataclass
class DiscoveredSkill:
    upstream_id: str
    name: str
    description: str
    display_title: str | None
    body: str
    frontmatter: dict[str, Any]
    category: str
    always_apply: bool
    user_invocable: bool
    disable_model_invocation: bool
    allowed_tools: list[str] | None
    tags: list[str]
    files: list[ExtractedAuxFile] = field(default_factory=list)


@dataclass
class DiscoveryResult:
    skills: list[DiscoveredSkill]
    errors: list[SkillSyncSkillError]
    summary: SkillSyncDiscoverySummary


class SkillSyncDiscoveryService:
    """Convert safely extracted folders into validated Skill candidates and item errors.

    Discovery reads ``SKILL.md`` files, validates frontmatter and per-Skill limits, and
    returns valid candidates plus structured errors and summary data. It is deliberately
    side-effect free with respect to GitHub, jobs, sources, and synchronized Skill records.
    """

    def discover_skills(self, extraction: ExtractionResult) -> DiscoveryResult:
        """Validate each extracted SKILL.md independently and retain item-level errors.

        Returning valid skills and errors together lets the apply phase make partial progress
        while preserving previously synced entries for paths that failed this discovery run.
        """
        skills: list[DiscoveredSkill] = []
        errors: list[SkillSyncSkillError] = []
        skipped_paths: list[str] = list(extraction.skipped_paths)
        seen_names: dict[str, str] = {}

        for folder_path in extraction.oversized_skill_paths:
            errors.append(
                SkillSyncSkillError(
                    skillPath=folder_path,
                    upstreamId=folder_path,
                    errorCode=SkillSyncSkillErrorCode.FILE_TOO_LARGE,
                    errorMessage=f"Skill folder '{folder_path}' contains a file exceeding the size limit",
                    phase="extraction",
                )
            )

        for folder in extraction.skill_folders:
            skill_or_error = _process_skill_folder(folder, seen_names)
            if isinstance(skill_or_error, DiscoveredSkill):
                skills.append(skill_or_error)
            else:
                errors.append(skill_or_error)

        summary = SkillSyncDiscoverySummary(
            discoveredSkillCount=len(skills),
            discoveredFileCount=sum(1 + len(s.files) for s in skills),
            skippedPaths=skipped_paths,
        )
        return DiscoveryResult(skills=skills, errors=errors, summary=summary)


def _process_skill_folder(
    folder: ExtractedSkillFolder,
    seen_names: dict[str, str],
) -> DiscoveredSkill | SkillSyncSkillError:
    path = folder.root_relative_path

    try:
        raw = folder.skill_md_path.read_bytes()
    except Exception as exc:
        return SkillSyncSkillError(
            skillPath=path,
            upstreamId=path,
            errorCode=SkillSyncSkillErrorCode.SKILL_PARSE_FAILED,
            errorMessage=f"Failed to read SKILL.md: {exc}",
            phase="discovery",
        )

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return SkillSyncSkillError(
            skillPath=path,
            upstreamId=path,
            errorCode=SkillSyncSkillErrorCode.SKILL_PARSE_FAILED,
            errorMessage="SKILL.md contains non-UTF-8 content",
            phase="discovery",
        )

    parsed = _parse_frontmatter(content)
    if parsed is None:
        return SkillSyncSkillError(
            skillPath=path,
            upstreamId=path,
            errorCode=SkillSyncSkillErrorCode.SKILL_PARSE_FAILED,
            errorMessage="SKILL.md has no valid YAML frontmatter",
            phase="discovery",
        )

    raw_frontmatter, body = parsed
    try:
        frontmatter = SkillFrontmatter.model_validate(raw_frontmatter)
    except ValidationError as exc:
        name_missing = any(error["loc"] == ("name",) for error in exc.errors())
        return SkillSyncSkillError(
            skillPath=path,
            upstreamId=path,
            errorCode=(
                SkillSyncSkillErrorCode.SKILL_NAME_MISSING
                if name_missing
                else SkillSyncSkillErrorCode.SKILL_PARSE_FAILED
            ),
            errorMessage=f"SKILL.md frontmatter validation failed: {exc.errors(include_url=False)}",
            phase="discovery",
        )

    if frontmatter.name in seen_names:
        return SkillSyncSkillError(
            skillPath=path,
            upstreamId=path,
            errorCode=SkillSyncSkillErrorCode.DUPLICATE_SKILL_NAME,
            errorMessage=f"Duplicate skill name '{frontmatter.name}', first seen at {seen_names[frontmatter.name]}",
            phase="discovery",
        )

    seen_names[frontmatter.name] = path

    if len(folder.aux_files) > MAX_FILES_PER_SKILL:
        return SkillSyncSkillError(
            skillPath=path,
            upstreamId=path,
            errorCode=SkillSyncSkillErrorCode.TOO_MANY_FILES,
            errorMessage=f"Skill has {len(folder.aux_files)} auxiliary files, max {MAX_FILES_PER_SKILL}",
            phase="discovery",
        )

    return DiscoveredSkill(
        upstream_id=path,
        name=frontmatter.name,
        description=frontmatter.description,
        display_title=frontmatter.displayTitle,
        body=body,
        frontmatter=frontmatter.model_dump(exclude_unset=True, exclude_none=True),
        category=frontmatter.category,
        always_apply=frontmatter.alwaysApply,
        user_invocable=frontmatter.userInvocable,
        disable_model_invocation=frontmatter.disableModelInvocation,
        allowed_tools=frontmatter.allowedTools,
        tags=frontmatter.tags,
        files=folder.aux_files,
    )


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str] | None:
    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return None
    after_first_fence = stripped[3:]
    if after_first_fence and after_first_fence[0] not in ("\n", "\r"):
        return None
    end_idx = after_first_fence.find("\n---")
    if end_idx == -1:
        return None
    yaml_str = after_first_fence[:end_idx]
    body_start = end_idx + 4
    body = after_first_fence[body_start:].lstrip("\n")
    try:
        fm = yaml.safe_load(yaml_str)
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    return fm, body
