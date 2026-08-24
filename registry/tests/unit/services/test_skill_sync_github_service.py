import io
import tarfile
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from registry.services.skill_sync_github_service import (
    MAX_TARBALL_SIZE,
    GitHubDownloadError,
    SkillSyncGitHubService,
    _matches_paths,
    _strip_top_dir,
)
from registry_pkgs.models.enums import SkillSyncJobErrorCode


def _make_tarball(files: dict[str, bytes], top_dir: str = "owner-repo-abc1234") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            full_path = f"{top_dir}/{name}"
            info = tarfile.TarInfo(name=full_path)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b"",
        headers: dict | None = None,
        json_data: dict | None = None,
    ):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self._json_data = json_data

    def json(self):
        return self._json_data

    async def aiter_bytes(self):
        yield self.content


def _make_client(*, get_response: _FakeResponse, stream_response: _FakeResponse | None = None) -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=get_response)

    if stream_response is not None:

        @asynccontextmanager
        async def _stream(*args, **kwargs):
            yield stream_response

        client.stream = _stream

    return client


def _make_streaming_error_client(*, get_response: _FakeResponse, stream_exc: Exception) -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=get_response)

    @asynccontextmanager
    async def _stream(*args, **kwargs):
        raise stream_exc
        yield  # noqa: RUF027  # pragma: no cover

    client.stream = _stream
    return client


def _sha_response(sha: str = "a" * 40) -> _FakeResponse:
    return _FakeResponse(json_data={"sha": sha})


# ── resolve_commit_sha ───────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_commit_sha_success():
    sha = "a" * 40
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(json_data={"sha": sha}))
    service = SkillSyncGitHubService(client)
    result = await service.resolve_commit_sha(owner="org", repo="repo", ref="main", access_token="tok")
    assert result == sha


@pytest.mark.asyncio
async def test_resolve_commit_sha_auth_failed():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(status_code=401))
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.resolve_commit_sha(owner="o", repo="r", ref="main", access_token="bad")
    assert exc_info.value.error_code == SkillSyncJobErrorCode.GITHUB_AUTH_FAILED


@pytest.mark.asyncio
async def test_resolve_commit_sha_not_found():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(status_code=404))
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.resolve_commit_sha(owner="o", repo="r", ref="main", access_token="tok")
    assert exc_info.value.error_code == SkillSyncJobErrorCode.GITHUB_NOT_FOUND


# ── download_tarball ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_tarball_success():
    tarball = _make_tarball({"skills/hello.md": b"# Hello"})
    sha = "a" * 40
    client = _make_client(
        get_response=_sha_response(sha),
        stream_response=_FakeResponse(content=tarball),
    )
    service = SkillSyncGitHubService(client)
    result_bytes, commit_sha = await service.download_tarball(owner="org", repo="repo", ref="main", access_token="tok")
    assert result_bytes == tarball
    assert commit_sha == sha


@pytest.mark.asyncio
async def test_download_tarball_auth_failed():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(status_code=401))
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(owner="o", repo="r", ref="main", access_token="bad")
    assert exc_info.value.error_code == SkillSyncJobErrorCode.GITHUB_AUTH_FAILED


@pytest.mark.asyncio
async def test_download_tarball_forbidden():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(status_code=403))
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(owner="o", repo="r", ref="main", access_token="bad")
    assert exc_info.value.error_code == SkillSyncJobErrorCode.GITHUB_AUTH_FAILED


@pytest.mark.asyncio
async def test_download_tarball_rate_limited():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(status_code=429))
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(owner="o", repo="r", ref="main", access_token="tok")
    assert exc_info.value.error_code == SkillSyncJobErrorCode.GITHUB_RATE_LIMITED


@pytest.mark.asyncio
async def test_download_tarball_not_found():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(status_code=404))
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(owner="o", repo="r", ref="main", access_token="tok")
    assert exc_info.value.error_code == SkillSyncJobErrorCode.GITHUB_NOT_FOUND


@pytest.mark.asyncio
async def test_download_tarball_server_error():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(status_code=500))
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(owner="o", repo="r", ref="main", access_token="tok")
    assert exc_info.value.error_code == SkillSyncJobErrorCode.DOWNLOAD_FAILED


@pytest.mark.asyncio
async def test_download_tarball_too_large():
    client = _make_client(
        get_response=_sha_response(),
        stream_response=_FakeResponse(content=b"x" * (MAX_TARBALL_SIZE + 1)),
    )
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(owner="o", repo="r", ref="main", access_token="tok")
    assert exc_info.value.error_code == SkillSyncJobErrorCode.DOWNLOAD_TOO_LARGE


@pytest.mark.asyncio
async def test_download_tarball_network_error():
    client = _make_streaming_error_client(
        get_response=_sha_response(),
        stream_exc=httpx.ConnectError("connection refused"),
    )
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(owner="o", repo="r", ref="main", access_token="tok")
    assert exc_info.value.error_code == SkillSyncJobErrorCode.DOWNLOAD_FAILED


@pytest.mark.asyncio
async def test_download_tarball_stream_auth_failed():
    client = _make_client(
        get_response=_sha_response(),
        stream_response=_FakeResponse(status_code=401),
    )
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(owner="o", repo="r", ref="main", access_token="tok")
    assert exc_info.value.error_code == SkillSyncJobErrorCode.GITHUB_AUTH_FAILED


# ── extract_files ─────────────────────────────────────────────


def test_extract_files_basic():
    tarball = _make_tarball(
        {
            "skills/hello.md": b"# Hello",
            "skills/world.md": b"# World",
            "README.md": b"# Readme",
        }
    )
    service = SkillSyncGitHubService(AsyncMock())
    files = service.extract_files(tarball, paths=["skills"], max_depth=2)
    assert len(files) == 2
    paths = {f.relative_path for f in files}
    assert paths == {"skills/hello.md", "skills/world.md"}


def test_extract_files_respects_depth():
    tarball = _make_tarball(
        {
            "skills/a.md": b"a",
            "skills/sub/b.md": b"b",
            "skills/sub/deep/c.md": b"c",
        }
    )
    service = SkillSyncGitHubService(AsyncMock())
    files = service.extract_files(tarball, paths=["skills"], max_depth=1)
    paths = {f.relative_path for f in files}
    assert "skills/a.md" in paths
    assert "skills/sub/b.md" in paths
    assert "skills/sub/deep/c.md" not in paths


def test_extract_files_multiple_paths():
    tarball = _make_tarball(
        {
            "skills/a.md": b"a",
            "docs/b.md": b"b",
            "other/c.md": b"c",
        }
    )
    service = SkillSyncGitHubService(AsyncMock())
    files = service.extract_files(tarball, paths=["skills", "docs"], max_depth=2)
    paths = {f.relative_path for f in files}
    assert paths == {"skills/a.md", "docs/b.md"}


def test_extract_files_skips_oversized(monkeypatch):
    monkeypatch.setattr("registry.services.skill_sync_github_service.MAX_SINGLE_FILE_SIZE", 10)
    tarball = _make_tarball(
        {
            "skills/small.md": b"ok",
            "skills/big.md": b"x" * 20,
        }
    )
    service = SkillSyncGitHubService(AsyncMock())
    files = service.extract_files(tarball, paths=["skills"], max_depth=2)
    assert len(files) == 1
    assert files[0].relative_path == "skills/small.md"


def test_extract_files_decompression_bomb(monkeypatch):
    monkeypatch.setattr("registry.services.skill_sync_github_service.MAX_EXTRACTED_SIZE", 10)
    tarball = _make_tarball(
        {
            "skills/a.md": b"x" * 6,
            "skills/b.md": b"y" * 6,
        }
    )
    service = SkillSyncGitHubService(AsyncMock())
    with pytest.raises(GitHubDownloadError) as exc_info:
        service.extract_files(tarball, paths=["skills"], max_depth=2)
    assert exc_info.value.error_code == SkillSyncJobErrorCode.DECOMPRESSION_BOMB


def test_extract_files_invalid_tarball():
    service = SkillSyncGitHubService(AsyncMock())
    with pytest.raises(GitHubDownloadError) as exc_info:
        service.extract_files(b"not a tarball", paths=["skills"], max_depth=2)
    assert exc_info.value.error_code == SkillSyncJobErrorCode.EXTRACTION_FAILED


# ── helpers ───────────────────────────────────────────────────


def test_strip_top_dir():
    assert _strip_top_dir("owner-repo-abc1234/skills/hello.md") == "skills/hello.md"
    assert _strip_top_dir("single") == ""
    assert _strip_top_dir("top/") == ""


def test_matches_paths():
    assert _matches_paths("skills/hello.md", ["skills"], 2) is True
    assert _matches_paths("skills/sub/hello.md", ["skills"], 2) is True
    assert _matches_paths("other/hello.md", ["skills"], 2) is False
    assert _matches_paths("skills/a/b/c.md", ["skills"], 1) is False
    assert _matches_paths("skills/a/b/c.md", ["skills"], 3) is True
