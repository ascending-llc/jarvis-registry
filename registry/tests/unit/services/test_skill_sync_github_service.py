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
    _match_prefix,
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
async def test_download_tarball_success(tmp_path):
    tarball = _make_tarball({"skills/hello/SKILL.md": b"# Hello"})
    sha = "a" * 40
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_sha_response(sha))
    streamed_urls: list[str] = []

    @asynccontextmanager
    async def _stream(method, url, **kwargs):
        streamed_urls.append(url)
        yield _FakeResponse(content=tarball)

    client.stream = _stream
    service = SkillSyncGitHubService(client)
    dest = tmp_path / "tarball.tar.gz"
    commit_sha = await service.download_tarball(
        owner="org", repo="repo", ref="main", access_token="tok", dest_path=dest
    )
    assert commit_sha == sha
    assert streamed_urls == [f"https://api.github.com/repos/org/repo/tarball/{sha}"]
    assert dest.exists()
    assert dest.stat().st_size == len(tarball)


@pytest.mark.asyncio
async def test_download_tarball_auth_failed(tmp_path):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(status_code=401))
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(
            owner="o", repo="r", ref="main", access_token="bad", dest_path=tmp_path / "t.tar.gz"
        )
    assert exc_info.value.error_code == SkillSyncJobErrorCode.GITHUB_AUTH_FAILED


@pytest.mark.asyncio
async def test_download_tarball_forbidden(tmp_path):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(status_code=403))
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(
            owner="o", repo="r", ref="main", access_token="bad", dest_path=tmp_path / "t.tar.gz"
        )
    assert exc_info.value.error_code == SkillSyncJobErrorCode.GITHUB_AUTH_FAILED


@pytest.mark.asyncio
async def test_download_tarball_rate_limited(tmp_path):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(status_code=429))
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(
            owner="o", repo="r", ref="main", access_token="tok", dest_path=tmp_path / "t.tar.gz"
        )
    assert exc_info.value.error_code == SkillSyncJobErrorCode.GITHUB_RATE_LIMITED


@pytest.mark.asyncio
async def test_download_tarball_not_found(tmp_path):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(status_code=404))
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(
            owner="o", repo="r", ref="main", access_token="tok", dest_path=tmp_path / "t.tar.gz"
        )
    assert exc_info.value.error_code == SkillSyncJobErrorCode.GITHUB_NOT_FOUND


@pytest.mark.asyncio
async def test_download_tarball_server_error(tmp_path):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_FakeResponse(status_code=500))
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(
            owner="o", repo="r", ref="main", access_token="tok", dest_path=tmp_path / "t.tar.gz"
        )
    assert exc_info.value.error_code == SkillSyncJobErrorCode.DOWNLOAD_FAILED


@pytest.mark.asyncio
async def test_download_tarball_too_large(tmp_path):
    client = _make_client(
        get_response=_sha_response(),
        stream_response=_FakeResponse(content=b"x" * (MAX_TARBALL_SIZE + 1)),
    )
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(
            owner="o", repo="r", ref="main", access_token="tok", dest_path=tmp_path / "t.tar.gz"
        )
    assert exc_info.value.error_code == SkillSyncJobErrorCode.DOWNLOAD_TOO_LARGE


@pytest.mark.asyncio
async def test_download_tarball_network_error(tmp_path):
    client = _make_streaming_error_client(
        get_response=_sha_response(),
        stream_exc=httpx.ConnectError("connection refused"),
    )
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(
            owner="o", repo="r", ref="main", access_token="tok", dest_path=tmp_path / "t.tar.gz"
        )
    assert exc_info.value.error_code == SkillSyncJobErrorCode.DOWNLOAD_FAILED


@pytest.mark.asyncio
async def test_download_tarball_stream_auth_failed(tmp_path):
    client = _make_client(
        get_response=_sha_response(),
        stream_response=_FakeResponse(status_code=401),
    )
    service = SkillSyncGitHubService(client)
    with pytest.raises(GitHubDownloadError) as exc_info:
        await service.download_tarball(
            owner="o", repo="r", ref="main", access_token="tok", dest_path=tmp_path / "t.tar.gz"
        )
    assert exc_info.value.error_code == SkillSyncJobErrorCode.GITHUB_AUTH_FAILED


# ── extract_skill_folders ─────────────────────────────────────


def test_extract_basic_skill_folder(tmp_path):
    tarball_path = tmp_path / "tarball.tar.gz"
    tarball_path.write_bytes(
        _make_tarball(
            {
                "skills/hello/SKILL.md": b"---\nname: hello\n---\nHello",
                "skills/hello/helper.py": b"print('hi')",
                "README.md": b"# Readme",
            }
        )
    )
    extraction_dir = tmp_path / "extracted"
    extraction_dir.mkdir()
    service = SkillSyncGitHubService(AsyncMock())
    result = service.extract_skill_folders(tarball_path, paths=["skills"], extraction_dir=extraction_dir)
    assert len(result.skill_folders) == 1
    folder = result.skill_folders[0]
    assert folder.root_relative_path == "skills/hello"
    assert folder.skill_md_path.exists()
    assert folder.skill_md_path.read_bytes() == b"---\nname: hello\n---\nHello"
    assert len(folder.aux_files) == 1
    assert folder.aux_files[0].relative_path == "skills/hello/helper.py"
    assert folder.aux_files[0].absolute_path.exists()
    assert folder.aux_files[0].absolute_path.read_bytes() == b"print('hi')"


def test_extract_multiple_skill_folders(tmp_path):
    tarball_path = tmp_path / "tarball.tar.gz"
    tarball_path.write_bytes(
        _make_tarball(
            {
                "skills/alpha/SKILL.md": b"---\nname: alpha\n---\nA",
                "skills/beta/SKILL.md": b"---\nname: beta\n---\nB",
            }
        )
    )
    extraction_dir = tmp_path / "extracted"
    extraction_dir.mkdir()
    service = SkillSyncGitHubService(AsyncMock())
    result = service.extract_skill_folders(tarball_path, paths=["skills"], extraction_dir=extraction_dir)
    assert len(result.skill_folders) == 2
    names = {f.root_relative_path for f in result.skill_folders}
    assert names == {"skills/alpha", "skills/beta"}


def test_extract_skips_folder_without_skill_md(tmp_path):
    tarball_path = tmp_path / "tarball.tar.gz"
    tarball_path.write_bytes(
        _make_tarball(
            {
                "skills/valid/SKILL.md": b"---\nname: valid\n---\nV",
                "skills/invalid/readme.md": b"# Not a skill",
            }
        )
    )
    extraction_dir = tmp_path / "extracted"
    extraction_dir.mkdir()
    service = SkillSyncGitHubService(AsyncMock())
    result = service.extract_skill_folders(tarball_path, paths=["skills"], extraction_dir=extraction_dir)
    assert len(result.skill_folders) == 1
    assert result.skill_folders[0].root_relative_path == "skills/valid"
    assert "skills/invalid" in result.skipped_paths


def test_extract_bare_files_under_path_skipped(tmp_path):
    tarball_path = tmp_path / "tarball.tar.gz"
    tarball_path.write_bytes(
        _make_tarball(
            {
                "skills/bare-file.md": b"Not in a folder",
                "skills/deploy/SKILL.md": b"---\nname: deploy\n---\nD",
            }
        )
    )
    extraction_dir = tmp_path / "extracted"
    extraction_dir.mkdir()
    service = SkillSyncGitHubService(AsyncMock())
    result = service.extract_skill_folders(tarball_path, paths=["skills"], extraction_dir=extraction_dir)
    assert len(result.skill_folders) == 1
    assert "skills/bare-file.md" in result.skipped_paths


def test_extract_configured_path_is_skill_folder(tmp_path):
    tarball_path = tmp_path / "tarball.tar.gz"
    tarball_path.write_bytes(
        _make_tarball(
            {
                "my-skill/SKILL.md": b"---\nname: my-skill\n---\nM",
                "my-skill/scripts/run.sh": b"echo hi",
                "other/README.md": b"# Other",
            }
        )
    )
    extraction_dir = tmp_path / "extracted"
    extraction_dir.mkdir()
    service = SkillSyncGitHubService(AsyncMock())
    result = service.extract_skill_folders(tarball_path, paths=["my-skill"], extraction_dir=extraction_dir)
    assert len(result.skill_folders) == 1
    folder = result.skill_folders[0]
    assert folder.root_relative_path == "my-skill"
    assert folder.skill_md_path.read_bytes() == b"---\nname: my-skill\n---\nM"
    assert [f.relative_path for f in folder.aux_files] == ["my-skill/scripts/run.sh"]
    assert result.skipped_paths == []


def test_extract_case_sensitive_skill_md(tmp_path):
    tarball_path = tmp_path / "tarball.tar.gz"
    tarball_path.write_bytes(
        _make_tarball(
            {
                "skills/lower/skill.md": b"---\nname: lower\n---\nL",
                "skills/upper/SKILL.md": b"---\nname: upper\n---\nU",
            }
        )
    )
    extraction_dir = tmp_path / "extracted"
    extraction_dir.mkdir()
    service = SkillSyncGitHubService(AsyncMock())
    result = service.extract_skill_folders(tarball_path, paths=["skills"], extraction_dir=extraction_dir)
    assert len(result.skill_folders) == 1
    assert result.skill_folders[0].root_relative_path == "skills/upper"
    assert "skills/lower" in result.skipped_paths


def test_extract_recursive_aux_files(tmp_path):
    tarball_path = tmp_path / "tarball.tar.gz"
    tarball_path.write_bytes(
        _make_tarball(
            {
                "skills/deploy/SKILL.md": b"---\nname: deploy\n---\nD",
                "skills/deploy/scripts/run.sh": b"#!/bin/bash",
                "skills/deploy/scripts/utils/helper.sh": b"helper",
                "skills/deploy/config.yml": b"key: val",
            }
        )
    )
    extraction_dir = tmp_path / "extracted"
    extraction_dir.mkdir()
    service = SkillSyncGitHubService(AsyncMock())
    result = service.extract_skill_folders(tarball_path, paths=["skills"], extraction_dir=extraction_dir)
    assert len(result.skill_folders) == 1
    assert len(result.skill_folders[0].aux_files) == 3
    aux_paths = {af.relative_path for af in result.skill_folders[0].aux_files}
    assert "skills/deploy/scripts/run.sh" in aux_paths
    assert "skills/deploy/scripts/utils/helper.sh" in aux_paths
    assert "skills/deploy/config.yml" in aux_paths


def test_extract_oversized_file_rejects_whole_folder(tmp_path, monkeypatch):
    monkeypatch.setattr("registry.services.skill_sync_github_service.MAX_SINGLE_FILE_SIZE", 30)
    tarball_path = tmp_path / "tarball.tar.gz"
    tarball_path.write_bytes(
        _make_tarball(
            {
                "skills/big/SKILL.md": b"---\nname: big\n---\nB",
                "skills/big/huge.bin": b"x" * 50,
                "skills/small/SKILL.md": b"---\nname: small\n---\nS",
            }
        )
    )
    extraction_dir = tmp_path / "extracted"
    extraction_dir.mkdir()
    service = SkillSyncGitHubService(AsyncMock())
    result = service.extract_skill_folders(tarball_path, paths=["skills"], extraction_dir=extraction_dir)
    assert len(result.skill_folders) == 1
    assert result.skill_folders[0].root_relative_path == "skills/small"
    assert "skills/big" in result.oversized_skill_paths


def test_extract_decompression_bomb(tmp_path, monkeypatch):
    monkeypatch.setattr("registry.services.skill_sync_github_service.MAX_EXTRACTED_SIZE", 10)
    tarball_path = tmp_path / "tarball.tar.gz"
    tarball_path.write_bytes(
        _make_tarball(
            {
                "skills/a/SKILL.md": b"x" * 6,
                "skills/a/big.txt": b"y" * 6,
            }
        )
    )
    extraction_dir = tmp_path / "extracted"
    extraction_dir.mkdir()
    service = SkillSyncGitHubService(AsyncMock())
    with pytest.raises(GitHubDownloadError) as exc_info:
        service.extract_skill_folders(tarball_path, paths=["skills"], extraction_dir=extraction_dir)
    assert exc_info.value.error_code == SkillSyncJobErrorCode.DECOMPRESSION_BOMB


def test_extract_invalid_tarball(tmp_path):
    tarball_path = tmp_path / "bad.tar.gz"
    tarball_path.write_bytes(b"not a tarball")
    extraction_dir = tmp_path / "extracted"
    extraction_dir.mkdir()
    service = SkillSyncGitHubService(AsyncMock())
    with pytest.raises(GitHubDownloadError) as exc_info:
        service.extract_skill_folders(tarball_path, paths=["skills"], extraction_dir=extraction_dir)
    assert exc_info.value.error_code == SkillSyncJobErrorCode.EXTRACTION_FAILED


def test_extract_multiple_paths(tmp_path):
    tarball_path = tmp_path / "tarball.tar.gz"
    tarball_path.write_bytes(
        _make_tarball(
            {
                "skills/a/SKILL.md": b"---\nname: a\n---\nA",
                "docs/b/SKILL.md": b"---\nname: b\n---\nB",
                "other/c/SKILL.md": b"---\nname: c\n---\nC",
            }
        )
    )
    extraction_dir = tmp_path / "extracted"
    extraction_dir.mkdir()
    service = SkillSyncGitHubService(AsyncMock())
    result = service.extract_skill_folders(tarball_path, paths=["skills", "docs"], extraction_dir=extraction_dir)
    names = {f.root_relative_path for f in result.skill_folders}
    assert names == {"skills/a", "docs/b"}


# ── helpers ───────────────────────────────────────────────────


def test_strip_top_dir():
    assert _strip_top_dir("owner-repo-abc1234/skills/hello.md") == "skills/hello.md"
    assert _strip_top_dir("single") == ""
    assert _strip_top_dir("top/") == ""


def test_match_prefix():
    assert _match_prefix("skills/hello/SKILL.md", ["skills"]) == "skills"
    assert _match_prefix("docs/hello/SKILL.md", ["skills", "docs"]) == "docs"
    assert _match_prefix("other/hello.md", ["skills"]) is None
    assert _match_prefix("skills-extra/hello.md", ["skills"]) is None
