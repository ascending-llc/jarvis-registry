import logging
import os
from dataclasses import dataclass, field
from typing import Any

import yaml

from registry_pkgs.models.enums import SkillSyncSkillErrorCode
from registry_pkgs.models.skill_sync_job import SkillSyncDiscoverySummary, SkillSyncSkillError

from .skill_sync_github_service import DiscoveredFile

logger = logging.getLogger(__name__)

MAX_FILES_PER_SKILL = 50

_FRONTMATTER_FIELDS = {
    "name",
    "description",
    "displayTitle",
    "category",
    "alwaysApply",
    "userInvocable",
    "disableModelInvocation",
    "allowedTools",
    "tags",
}


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
    files: list[DiscoveredFile] = field(default_factory=list)


@dataclass
class DiscoveryResult:
    skills: list[DiscoveredSkill]
    errors: list[SkillSyncSkillError]
    summary: SkillSyncDiscoverySummary


class SkillSyncDiscoveryService:
    def discover_skills(self, files: list[DiscoveredFile]) -> DiscoveryResult:
        md_files: dict[str, DiscoveredFile] = {}
        aux_files: dict[str, list[DiscoveredFile]] = {}

        for f in files:
            if f.relative_path.endswith(".md"):
                md_files[f.relative_path] = f
            else:
                parent = os.path.dirname(f.relative_path)
                aux_files.setdefault(parent, []).append(f)

        skills: list[DiscoveredSkill] = []
        errors: list[SkillSyncSkillError] = []
        skipped_paths: list[str] = []
        seen_names: dict[str, str] = {}

        for path, md_file in sorted(md_files.items()):
            try:
                content = md_file.content.decode("utf-8")
            except UnicodeDecodeError:
                skipped_paths.append(path)
                continue

            parsed = _parse_frontmatter(content)
            if parsed is None:
                skipped_paths.append(path)
                continue

            fm, body = parsed
            name = fm.get("name")
            if not name or not isinstance(name, str):
                errors.append(
                    SkillSyncSkillError(
                        skillPath=path,
                        upstreamId=path,
                        errorCode=SkillSyncSkillErrorCode.SKILL_NAME_MISSING,
                        errorMessage="Frontmatter missing required 'name' field",
                        phase="discovery",
                    )
                )
                continue

            name = name.strip()
            description = fm.get("description", "")
            if not description:
                errors.append(
                    SkillSyncSkillError(
                        skillPath=path,
                        upstreamId=path,
                        errorCode=SkillSyncSkillErrorCode.SKILL_PARSE_FAILED,
                        errorMessage="Frontmatter missing required 'description' field",
                        phase="discovery",
                    )
                )
                continue

            if name in seen_names:
                errors.append(
                    SkillSyncSkillError(
                        skillPath=path,
                        upstreamId=path,
                        errorCode=SkillSyncSkillErrorCode.DUPLICATE_SKILL_NAME,
                        errorMessage=f"Duplicate skill name '{name}', first seen at {seen_names[name]}",
                        phase="discovery",
                    )
                )
                continue

            seen_names[name] = path

            parent_dir = os.path.dirname(path)
            skill_files = aux_files.get(parent_dir, [])
            if len(skill_files) > MAX_FILES_PER_SKILL:
                errors.append(
                    SkillSyncSkillError(
                        skillPath=path,
                        upstreamId=path,
                        errorCode=SkillSyncSkillErrorCode.TOO_MANY_FILES,
                        errorMessage=f"Skill has {len(skill_files)} auxiliary files, max {MAX_FILES_PER_SKILL}",
                        phase="discovery",
                    )
                )
                continue

            skills.append(
                DiscoveredSkill(
                    upstream_id=path,
                    name=name,
                    description=str(description).strip(),
                    display_title=fm.get("displayTitle"),
                    body=body,
                    frontmatter={k: v for k, v in fm.items() if k in _FRONTMATTER_FIELDS},
                    category=fm.get("category", "general"),
                    always_apply=bool(fm.get("alwaysApply", False)),
                    user_invocable=bool(fm.get("userInvocable", True)),
                    disable_model_invocation=bool(fm.get("disableModelInvocation", False)),
                    allowed_tools=fm.get("allowedTools"),
                    tags=fm.get("tags", []),
                    files=skill_files,
                )
            )

        summary = SkillSyncDiscoverySummary(
            discoveredSkillCount=len(skills),
            discoveredFileCount=sum(1 + len(s.files) for s in skills),
            skippedPaths=skipped_paths,
        )
        return DiscoveryResult(skills=skills, errors=errors, summary=summary)


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
