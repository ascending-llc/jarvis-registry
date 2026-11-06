"""Content digest, SKILL.md rendering, and the temp-dir materializer for skill scanning.

This module reads only stored skill fields and must not import from ``registry`` (the worker depends on it).
Path validation here deliberately repeats ``skill_service._validate_relative_path``: registry-pkgs cannot
import registry, and file data written by Jarvis Chat never passed through that validator.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

import yaml

from ..models.extended_skill import ExtendedSkill
from ..models.extended_skill_file import REGISTRY_SKILL_FILE_SOURCE, ExtendedSkillFile

CONTENT_DIGEST_VERSION = 1
SKILL_MD_FILENAME = "SKILL.md"
_DIR_MODE = 0o700
_FILE_MODE = 0o600


class SkillNotScannableError(Exception):
    """The skill has content Registry cannot read, or an unsafe file path; it must not be scanned."""


def is_readable_file(file: ExtendedSkillFile) -> bool:
    """True when Registry stores this file's bytes inline and can read them."""
    return file.source == REGISTRY_SKILL_FILE_SOURCE and file.body is not None


def has_unreadable_files(skill: ExtendedSkill, files: Sequence[ExtendedSkillFile]) -> bool:
    """True for a Chat-created skill with supporting files, or any skill with a file Registry cannot read.

    When true the skill is never scanned: scanning only the readable part could miss the dangerous file.

    ``files`` MUST be the skill's complete file set: a Registry-created skill with a Chat-added file is only
    detected when that file is present here (otherwise it would wrongly look scannable).
    """
    return (not skill.createdByRegistry and (skill.fileCount or 0) > 0) or any(not is_readable_file(f) for f in files)


def _file_digest_entry(file: ExtendedSkillFile) -> list[str]:
    if is_readable_file(file):
        assert file.body is not None  # noqa: S101 — is_readable_file guarantees it; narrows the type
        return [file.relativePath, "sha256:" + hashlib.sha256(file.body).hexdigest()]
    # A file Chat stored in its own storage: key the digest off its stored metadata so it still changes
    # when Chat changes the file, without Registry being able to read the bytes.
    updated = file.updatedAt.isoformat() if file.updatedAt else ""
    return [file.relativePath, "meta:" + f"{file.bytes}:{updated}"]


def compute_content_digest(skill: ExtendedSkill, files: Sequence[ExtendedSkillFile]) -> str:
    """SHA-256 over a canonical JSON of the skill's content. Depends on stored fields only, not rendering."""
    canonical = json.dumps(
        {
            "v": CONTENT_DIGEST_VERSION,
            "name": skill.name,
            "description": skill.description,
            "frontmatter": skill.frontmatter or {},
            "body": skill.body or "",
            "files": sorted(_file_digest_entry(f) for f in files),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_skill_md(skill: ExtendedSkill) -> str:
    """Render SKILL.md from stored fields. Not byte-identical to the CLI's Go renderer; key order is irrelevant."""
    front: dict = {"name": skill.name, "description": skill.description, **(skill.frontmatter or {})}
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
    return "---\n" + header + "---\n\n" + (skill.body or "")


def _validate_relative_path(path: str, root: Path) -> None:
    """Reject anything that could escape ``root``; repeats skill_service._validate_relative_path."""
    if not path or "\\" in path:
        raise SkillNotScannableError(f"unsafe file path: {path!r}")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        raise SkillNotScannableError(f"control characters in file path: {path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute() or not pure.parts or str(pure) != path:
        raise SkillNotScannableError(f"file path must be a normalized relative POSIX path: {path!r}")
    if any(part in ("..", ".") for part in pure.parts):
        raise SkillNotScannableError(f"file path must not contain '.' or '..' segments: {path!r}")
    # SKILL.md is reserved for the rendered main file; a supporting file named this (case-insensitively,
    # for case-insensitive filesystems) would overwrite it. Mirrors skill_service.RESERVED_SKILL_FILE_NAMES.
    if path.lower() == SKILL_MD_FILENAME.lower():
        raise SkillNotScannableError(f"'{path}' is reserved and cannot be a supporting file")
    resolved = (root / path).resolve()
    if resolved != root.resolve() and root.resolve() not in resolved.parents:
        raise SkillNotScannableError(f"file path escapes the skill root: {path!r}")


def materialize_skill(skill: ExtendedSkill, files: Sequence[ExtendedSkillFile], root: Path) -> None:
    """Write SKILL.md and every supporting file under ``root`` (dirs 0700, files 0600, never executable).

    Only valid when ``has_unreadable_files`` is false; raises ``SkillNotScannableError`` for an unreadable
    file or an unsafe path, having written nothing outside ``root``.
    """
    root.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    skill_md = root / SKILL_MD_FILENAME
    skill_md.write_text(render_skill_md(skill), encoding="utf-8")
    skill_md.chmod(_FILE_MODE)
    for file in files:
        if not is_readable_file(file):
            raise SkillNotScannableError(f"file is not readable by Registry: {file.relativePath!r}")
        _validate_relative_path(file.relativePath, root)
        target = root / file.relativePath
        assert file.body is not None  # noqa: S101 — is_readable_file guarantees it; narrows the type
        try:
            target.parent.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
            target.write_bytes(file.body)
            target.chmod(_FILE_MODE)
        except OSError as e:
            # Each path is valid alone, but the set may not fit on disk: e.g. files "a" and "a/b" need "a"
            # to be both a file and a directory. That is a property of the skill's content, not a worker
            # fault, so it must not be retried as an internal error.
            raise SkillNotScannableError(f"cannot write file path {file.relativePath!r}: {e.strerror}") from e
