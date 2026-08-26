import copy
import logging
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from registry_pkgs.models.enums import SkillSyncJobErrorCode

logger = logging.getLogger(__name__)

MAX_TARBALL_SIZE = 100 * 1024 * 1024
MAX_EXTRACTED_SIZE = 500 * 1024 * 1024
MAX_SINGLE_FILE_SIZE = 5 * 1024 * 1024

_GITHUB_API_BASE = "https://api.github.com"
_SKILL_ENTRY_FILENAME = "SKILL.md"


class GitHubDownloadError(Exception):
    def __init__(self, message: str, error_code: SkillSyncJobErrorCode) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass
class ExtractedAuxFile:
    relative_path: str
    absolute_path: Path
    size: int


@dataclass
class ExtractedSkillFolder:
    root_relative_path: str
    skill_md_path: Path
    aux_files: list[ExtractedAuxFile] = field(default_factory=list)


@dataclass
class ExtractionResult:
    skill_folders: list[ExtractedSkillFolder] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    oversized_skill_paths: list[str] = field(default_factory=list)


class SkillSyncGitHubService:
    """Materialize an immutable GitHub repository snapshot safely on local disk.

    The service resolves a mutable ref to a commit SHA, streams that SHA's tarball under
    download limits, and extracts configured paths with traversal, link, file-size, and
    decompression safeguards. It does not interpret Skill metadata or write database state.
    """

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client

    async def resolve_commit_sha(
        self,
        *,
        owner: str,
        repo: str,
        ref: str,
        access_token: str,
    ) -> str:
        """Resolve a mutable branch or tag to the immutable commit used by this job."""
        url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{ref}"
        headers = {
            "Authorization": f"token {access_token}",
            "Accept": "application/vnd.github+json",
        }
        try:
            response = await self._http_client.get(url, headers=headers)
            _raise_for_github_status(response, owner, repo, ref)
            return response.json()["sha"]
        except GitHubDownloadError:
            raise
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise GitHubDownloadError(
                f"Failed to resolve commit SHA for {ref}: {exc}",
                SkillSyncJobErrorCode.DOWNLOAD_FAILED,
            ) from exc

    async def download_tarball(
        self,
        *,
        owner: str,
        repo: str,
        ref: str,
        access_token: str,
        dest_path: Path,
    ) -> str:
        """Resolve the ref once, then stream the tarball by immutable SHA with a size limit."""
        commit_sha = await self.resolve_commit_sha(owner=owner, repo=repo, ref=ref, access_token=access_token)
        url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/tarball/{commit_sha}"
        headers = {
            "Authorization": f"token {access_token}",
            "Accept": "application/vnd.github+json",
        }
        try:
            async with self._http_client.stream("GET", url, headers=headers, follow_redirects=True) as response:
                _raise_for_github_status(response, owner, repo, ref)

                total_size = 0
                with open(dest_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        total_size += len(chunk)
                        if total_size > MAX_TARBALL_SIZE:
                            raise GitHubDownloadError(
                                f"Tarball size exceeds limit {MAX_TARBALL_SIZE}",
                                SkillSyncJobErrorCode.DOWNLOAD_TOO_LARGE,
                            )
                        f.write(chunk)

                return commit_sha
        except GitHubDownloadError:
            raise
        except httpx.HTTPError as exc:
            raise GitHubDownloadError(
                f"GitHub API request failed: {exc}",
                SkillSyncJobErrorCode.DOWNLOAD_FAILED,
            ) from exc

    def extract_skill_folders(
        self,
        tarball_path: Path,
        *,
        paths: list[str],
        extraction_dir: Path,
    ) -> ExtractionResult:
        """Safely extract configured skill folders with traversal, link, and size defenses."""
        try:
            with tarfile.open(tarball_path, mode="r:gz") as tar:
                return _two_pass_extract(tar, paths=paths, extraction_dir=extraction_dir)
        except GitHubDownloadError:
            raise
        except Exception as exc:
            raise GitHubDownloadError(
                f"Failed to extract tarball: {exc}",
                SkillSyncJobErrorCode.EXTRACTION_FAILED,
            ) from exc


def _raise_for_github_status(response: httpx.Response, owner: str, repo: str, ref: str) -> None:
    if response.status_code in (401, 403):
        raise GitHubDownloadError(
            f"GitHub authentication failed (HTTP {response.status_code})",
            SkillSyncJobErrorCode.GITHUB_AUTH_FAILED,
        )
    if response.status_code == 429:
        raise GitHubDownloadError(
            "GitHub API rate limit exceeded",
            SkillSyncJobErrorCode.GITHUB_RATE_LIMITED,
        )
    if response.status_code == 404:
        raise GitHubDownloadError(
            f"Repository {owner}/{repo} ref {ref} not found",
            SkillSyncJobErrorCode.GITHUB_NOT_FOUND,
        )
    if response.status_code >= 400:
        raise GitHubDownloadError(
            f"GitHub API returned HTTP {response.status_code}",
            SkillSyncJobErrorCode.DOWNLOAD_FAILED,
        )


def _strip_top_dir(path: str) -> str:
    parts = path.split("/", 1)
    return parts[1] if len(parts) > 1 else ""


def _two_pass_extract(
    tar: tarfile.TarFile,
    *,
    paths: list[str],
    extraction_dir: Path,
) -> ExtractionResult:
    normalized_paths = [p.rstrip("/") for p in paths]
    members = tar.getmembers()

    # Pass 1: identify skill folders from tar headers only (no file content is read).
    # A configured path is treated purely as a container: only its direct child folders can
    # become skill folders (<path>/<skill>/SKILL.md). A SKILL.md sitting directly at the
    # configured path's root is a bare file and routes to skipped_paths — never a skill folder
    # that would swallow its sibling skill folders as auxiliary files.
    skipped_paths: list[str] = []
    # matched prefix -> [(member, relative_path, path relative to that prefix)]
    by_prefix: dict[str, list[tuple[tarfile.TarInfo, str, str]]] = {}

    for member in members:
        if not member.isfile():
            continue
        relative_path = _strip_top_dir(member.name)
        if not relative_path:
            continue

        matched_prefix = _match_prefix(relative_path, normalized_paths)
        if matched_prefix is None:
            continue

        # "." is the whole-repo sentinel: it contributes no prefix to strip.
        prefix_len = 0 if matched_prefix == "." else len(matched_prefix)
        suffix = relative_path[prefix_len:].lstrip("/")
        if not suffix:
            continue

        by_prefix.setdefault(matched_prefix, []).append((member, relative_path, suffix))

    confirmed_folders: dict[str, list[tuple[tarfile.TarInfo, str]]] = {}

    for matched_prefix, entries in by_prefix.items():
        root_key = "" if matched_prefix == "." else matched_prefix

        # Each direct subfolder is a skill-folder candidate; bare files (including a SKILL.md at
        # the configured path's root) are skipped.
        groups: dict[str, list[tuple[tarfile.TarInfo, str, str]]] = {}
        for member, relative_path, suffix in entries:
            parts = suffix.split("/", 1)
            if len(parts) == 1:
                skipped_paths.append(relative_path)
                continue
            folder_key = f"{root_key}/{parts[0]}" if root_key else parts[0]
            groups.setdefault(folder_key, []).append((member, relative_path, parts[1]))

        for folder_key, group in groups.items():
            if any(rest == _SKILL_ENTRY_FILENAME for _, _, rest in group):
                confirmed_folders[folder_key] = [(member, rel) for member, rel, _ in group]
            else:
                skipped_paths.append(folder_key)

    # Pass 2: size-check and extract confirmed folders
    result = ExtractionResult(skipped_paths=skipped_paths)
    total_extracted = 0

    for folder_key, folder_members in confirmed_folders.items():
        # Check if any member exceeds MAX_SINGLE_FILE_SIZE
        oversized = False
        for member, _rel_path in folder_members:
            if member.size > MAX_SINGLE_FILE_SIZE:
                result.oversized_skill_paths.append(folder_key)
                oversized = True
                break
        if oversized:
            continue

        # Extract all members in this folder
        skill_md_path: Path | None = None
        aux_files: list[ExtractedAuxFile] = []

        for member, relative_path in folder_members:
            total_extracted += member.size
            if total_extracted > MAX_EXTRACTED_SIZE:
                raise GitHubDownloadError(
                    f"Total extracted size exceeds {MAX_EXTRACTED_SIZE} bytes",
                    SkillSyncJobErrorCode.DECOMPRESSION_BOMB,
                )

            member_copy = copy.copy(member)
            member_copy.name = relative_path

            tar.extract(member_copy, path=extraction_dir, filter="data")
            on_disk = extraction_dir / relative_path

            suffix_in_folder = relative_path[len(folder_key) :].lstrip("/")
            if suffix_in_folder == _SKILL_ENTRY_FILENAME:
                skill_md_path = on_disk
            else:
                aux_files.append(
                    ExtractedAuxFile(
                        relative_path=relative_path,
                        absolute_path=on_disk,
                        size=member.size,
                    )
                )

        if skill_md_path is not None:
            result.skill_folders.append(
                ExtractedSkillFolder(
                    root_relative_path=folder_key,
                    skill_md_path=skill_md_path,
                    aux_files=aux_files,
                )
            )

    return result


def _match_prefix(relative_path: str, normalized_paths: list[str]) -> str | None:
    for prefix in normalized_paths:
        if prefix == ".":
            return "."
        if relative_path == prefix or relative_path.startswith(prefix + "/"):
            return prefix
    return None
