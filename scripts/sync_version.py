"""
Sync version across all pyproject.toml files in the workspace.

Usage:
    uv run poe version-sync 0.2.0
"""

import re
import sys
from pathlib import Path


def update_pyproject_version(pyproject_path: Path, new_version: str) -> None:
    """Update version in a pyproject.toml file."""
    content = pyproject_path.read_text()

    # Update version line (handle spaces around =)
    updated = re.sub(r'^version\s*=\s*"[^"]+"', f'version = "{new_version}"', content, flags=re.MULTILINE)

    if updated != content:
        pyproject_path.write_text(updated)
        print(f"✓ Updated {pyproject_path.relative_to(Path.cwd())}: {new_version}")
    else:
        print(f"⚠ No version found in {pyproject_path.relative_to(Path.cwd())}")


def find_pyproject_files() -> list[Path]:
    """Find all pyproject.toml files in the workspace."""
    root = Path(__file__).parent.parent
    return [
        root / "pyproject.toml",
        root / "registry-pkgs" / "pyproject.toml",
        root / "registry" / "pyproject.toml",
        root / "auth-server" / "pyproject.toml",
        root / "servers" / "mcpgw" / "pyproject.toml",
    ]


def main():
    """Main sync function."""
    if len(sys.argv) < 2:
        print("❌ Error: Version number required")
        print("Usage: uv run poe version-sync 0.2.0")
        sys.exit(1)

    new_version = sys.argv[1]
    print(f"\n🔄 Syncing version: {new_version}\n")

    # Update all pyproject.toml files
    pyproject_files = find_pyproject_files()

    for pyproject_path in pyproject_files:
        if pyproject_path.exists():
            update_pyproject_version(pyproject_path, new_version)

    print(f"\n✅ All versions synced to {new_version}\n")


if __name__ == "__main__":
    main()
