"""
Download pre-generated Beanie models from a jarvis-api GitHub release.

This script downloads Python model artifacts published by jarvis-api into:
`registry-pkgs/src/registry_pkgs/models/_generated/`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REQUEST_TIMEOUT_SECONDS = 30


def parse_github_repo(github_repo: str) -> tuple[str, str]:
    parts = github_repo.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise RuntimeError("Invalid --repo format. Expected owner/repo (for example: ascending-llc/jarvis-api).")
    return parts[0], parts[1]


def validate_asset_filename(name: str) -> str:
    if not name.endswith(".py"):
        raise RuntimeError(f"Refusing to write non-Python asset: {name}")
    path = Path(name)
    if path.name != name or path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Unsafe asset filename from release: {name}")
    return name


class ReleaseAssetDownloader:
    def __init__(self, github_repo: str, github_token: str | None = None):
        self.github_repo = github_repo
        self.github_token = github_token
        self.owner, self.repo = parse_github_repo(github_repo)

    def _api_headers(self, accept: str = "application/vnd.github.v3+json") -> dict[str, str]:
        headers = {
            "User-Agent": "Jarvis-Beanie-Model-Downloader",
            "Accept": accept,
        }
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"
        return headers

    def fetch_release(self, tag: str) -> dict[str, Any]:
        api_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/releases/tags/{tag}"
        req = urllib.request.Request(api_url, headers=self._api_headers())
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # nosec B310
                if response.status != 200:
                    raise RuntimeError(f"Failed to fetch release metadata: HTTP {response.status}")
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError(f"Release tag '{tag}' not found in {self.github_repo}") from e
            if e.code in {401, 403}:
                raise RuntimeError(
                    "GitHub token is missing, invalid, or does not have access to this repository."
                ) from e
            if e.code == 429:
                raise RuntimeError("GitHub API rate limit exceeded. Try again later.") from e
            raise RuntimeError(f"Failed to fetch release metadata: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Failed to fetch release metadata: network error ({e.reason})") from e

    def download_asset(self, asset_id: int) -> bytes:
        api_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/releases/assets/{asset_id}"
        req = urllib.request.Request(api_url, headers=self._api_headers(accept="application/octet-stream"))
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # nosec B310
                if response.status != 200:
                    raise RuntimeError(f"Failed to download asset: HTTP {response.status}")
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code in {401, 403}:
                raise RuntimeError(
                    "GitHub token is missing, invalid, or does not have access to this repository."
                ) from e
            if e.code == 429:
                raise RuntimeError("GitHub API rate limit exceeded. Try again later.") from e
            raise RuntimeError(f"Failed to download asset: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Failed to download asset: network error ({e.reason})") from e


def clean_output_dir(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)

    for path in output_dir.iterdir():
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def write_readme(output_dir: Path, repo: str, tag: str, downloaded_files: list[str]):
    listed_files = sorted(name for name in downloaded_files if name.endswith(".py"))
    lines = [
        "# Auto-Generated Beanie Models",
        "",
        "⚠️  **DO NOT EDIT FILES IN THIS DIRECTORY**",
        "",
        "This directory contains auto-generated Beanie ODM models from Mongoose schemas.",
        "",
        "## Generation Info",
        "",
        f"- **Repository**: {repo}",
        f"- **Version**: {tag}",
        f"- **Downloaded at**: `{datetime.now(UTC).isoformat()}`",
        f"- **Files**: {len(listed_files)}",
        "",
        "## Regenerate Models",
        "",
        "To regenerate these models from the latest schemas, run the following command from project root.",
        "",
        "```bash",
        "uv run poe generate-schemas",
        "```",
        "",
        "To regenerate from a specific version tag, provide a positional argument like below.",
        "",
        "```bash",
        f"uv run poe generate-schemas {tag or 'asc0.5.1'}",
        "```",
        "",
        "**The generated files should be Git tracked.**",
        "",
        "## Files Generated",
        "",
    ]
    lines.extend([f"- `{name}`" for name in listed_files])
    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def choose_files(release_assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [asset for asset in release_assets if asset["name"].endswith(".py")]

    if not selected:
        raise RuntimeError("No Python model artifacts found in release")

    return sorted(selected, key=lambda asset: asset["name"])


def main():
    parser = argparse.ArgumentParser(
        description="Download pre-generated Python Beanie models from jarvis-api GitHub release artifacts."
    )
    parser.add_argument("--tag", required=True, help="Release tag to download (e.g. asc0.5.1)")
    parser.add_argument("--output-dir", required=True, help="Output directory for downloaded models")
    parser.add_argument(
        "--repo",
        default="ascending-llc/jarvis-api",
        help="GitHub repository in owner/repo format (default: ascending-llc/jarvis-api)",
    )
    parser.add_argument("--token", help="GitHub token for private repo access")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    downloader = ReleaseAssetDownloader(github_repo=args.repo, github_token=args.token)
    release = downloader.fetch_release(args.tag)

    assets = release.get("assets", [])
    selected_assets = choose_files(assets)

    print(f"Downloading {len(selected_assets)} artifact(s) from {args.repo}@{args.tag}")
    clean_output_dir(output_dir)

    downloaded_files: list[str] = []
    for asset in selected_assets:
        name = validate_asset_filename(asset["name"])
        asset_id = asset["id"]
        content = downloader.download_asset(asset_id)
        (output_dir / name).write_bytes(content)
        downloaded_files.append(name)
        print(f"  downloaded: {name}")

    # Keep local metadata for traceability and existing tooling.
    (output_dir / ".schema-version").write_text(args.tag, encoding="utf-8")
    write_readme(output_dir, repo=args.repo, tag=args.tag, downloaded_files=downloaded_files)

    print(f"Done. Models saved to: {output_dir}")


if __name__ == "__main__":
    main()
