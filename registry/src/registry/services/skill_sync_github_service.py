import io
import logging
import tarfile
from dataclasses import dataclass

import httpx

from registry_pkgs.models.enums import SkillSyncJobErrorCode

logger = logging.getLogger(__name__)

MAX_TARBALL_SIZE = 100 * 1024 * 1024
MAX_EXTRACTED_SIZE = 500 * 1024 * 1024
MAX_SINGLE_FILE_SIZE = 5 * 1024 * 1024

_GITHUB_API_BASE = "https://api.github.com"


class GitHubDownloadError(Exception):
    def __init__(self, message: str, error_code: SkillSyncJobErrorCode) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass
class DiscoveredFile:
    relative_path: str
    content: bytes
    size: int


class SkillSyncGitHubService:
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
        """
        docs: https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28#get-a-commit
        Resolve a ref to a full commit SHA via the documented REST API.
        """
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
    ) -> tuple[bytes, str]:
        """Resolve commit SHA then stream-download the GitHub tarball."""
        commit_sha = await self.resolve_commit_sha(owner=owner, repo=repo, ref=ref, access_token=access_token)
        url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/tarball/{ref}"
        headers = {
            "Authorization": f"token {access_token}",
            "Accept": "application/vnd.github+json",
        }
        try:
            async with self._http_client.stream("GET", url, headers=headers, follow_redirects=True) as response:
                _raise_for_github_status(response, owner, repo, ref)

                chunks: list[bytes] = []
                total_size = 0
                async for chunk in response.aiter_bytes():
                    total_size += len(chunk)
                    if total_size > MAX_TARBALL_SIZE:
                        raise GitHubDownloadError(
                            f"Tarball size exceeds limit {MAX_TARBALL_SIZE}",
                            SkillSyncJobErrorCode.DOWNLOAD_TOO_LARGE,
                        )
                    chunks.append(chunk)

                return b"".join(chunks), commit_sha
        except GitHubDownloadError:
            raise
        except httpx.HTTPError as exc:
            raise GitHubDownloadError(
                f"GitHub API request failed: {exc}",
                SkillSyncJobErrorCode.DOWNLOAD_FAILED,
            ) from exc

    def extract_files(
        self,
        tarball_bytes: bytes,
        *,
        paths: list[str],
        max_depth: int,
    ) -> list[DiscoveredFile]:
        """In-memory extraction filtered by paths + depth, with decompression bomb guard."""
        try:
            tar_io = io.BytesIO(tarball_bytes)
            with tarfile.open(fileobj=tar_io, mode="r:gz") as tar:
                return _extract_from_tar(tar, paths=paths, max_depth=max_depth)
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


def _extract_from_tar(
    tar: tarfile.TarFile,
    *,
    paths: list[str],
    max_depth: int,
) -> list[DiscoveredFile]:
    files: list[DiscoveredFile] = []
    total_extracted = 0
    normalized_paths = [p.rstrip("/") for p in paths]

    for member in tar.getmembers():
        if not member.isfile():
            continue
        relative_path = _strip_top_dir(member.name)
        if not relative_path:
            continue
        if not _matches_paths(relative_path, normalized_paths, max_depth):
            continue
        if member.size > MAX_SINGLE_FILE_SIZE:
            logger.warning("Skipping oversized file: %s (%d bytes)", relative_path, member.size)
            continue

        total_extracted += member.size
        if total_extracted > MAX_EXTRACTED_SIZE:
            raise GitHubDownloadError(
                f"Total extracted size exceeds {MAX_EXTRACTED_SIZE} bytes",
                SkillSyncJobErrorCode.DECOMPRESSION_BOMB,
            )

        file_obj = tar.extractfile(member)
        if file_obj is None:
            continue
        content = file_obj.read()
        files.append(DiscoveredFile(relative_path=relative_path, content=content, size=len(content)))

    return files


def _strip_top_dir(path: str) -> str:
    parts = path.split("/", 1)
    return parts[1] if len(parts) > 1 else ""


def _matches_paths(relative_path: str, normalized_paths: list[str], max_depth: int) -> bool:
    for prefix in normalized_paths:
        if relative_path == prefix or relative_path.startswith(prefix + "/"):
            suffix = relative_path[len(prefix) :].lstrip("/")
            depth = suffix.count("/")
            if depth <= max_depth:
                return True
    return False
