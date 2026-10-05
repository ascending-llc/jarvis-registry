"""Find, validate, checksum and load the migration scripts in `registry_pkgs.migrations.versions`."""

import hashlib
import importlib
import inspect
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from pymongo.asynchronous.database import AsyncDatabase

from . import versions

logger = logging.getLogger(__name__)

VERSIONS_PACKAGE = versions.__name__
# `versions.__file__` resolves the same way from the source tree and from the installed wheel.
VERSIONS_DIR = Path(versions.__file__).parent
MIGRATION_FILE_PATTERN = re.compile(r"^m(\d{4})_([a-z0-9_]+)\.py$")

MigrationUp = Callable[[AsyncDatabase], Awaitable[None]]


class MigrationDiscoveryError(Exception):
    """A migration file is misnamed, duplicated, or does not satisfy the `up` contract."""


class MigrationScript(BaseModel):
    """A migration file found in `versions/`, with its loaded `up` coroutine function."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    version: str
    name: str
    path: Path
    checksum: str
    up: MigrationUp


def _parse_file_name(path: Path) -> tuple[str, str]:
    match = MIGRATION_FILE_PATTERN.match(path.name)
    if match is None:
        raise MigrationDiscoveryError(
            f"Invalid migration file name '{path.name}' in {path.parent}: "
            f"expected m<NNNN>_<name>.py matching {MIGRATION_FILE_PATTERN.pattern}"
        )
    return match.group(1), match.group(2)


def _load_up(package: str, path: Path) -> MigrationUp:
    module = importlib.import_module(f"{package}.{path.stem}")
    up = getattr(module, "up", None)
    if up is None:
        raise MigrationDiscoveryError(f"Migration '{path.name}' does not define a module-level `up(db)` function")
    if not inspect.iscoroutinefunction(up):
        raise MigrationDiscoveryError(f"Migration '{path.name}' must define `up` as `async def up(db)`")
    return up


def compute_checksum(path: Path) -> str:
    """SHA-256 hex of the file's bytes, with CRLF normalised to LF so Windows checkouts match."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def discover_migrations(
    versions_dir: Path = VERSIONS_DIR,
    package: str = VERSIONS_PACKAGE,
) -> list[MigrationScript]:
    """Return every migration in `versions_dir`, sorted by version.

    Every name is validated (and duplicates rejected) before any module is imported, so a bad file
    fails discovery without running another migration's import-time code.
    """
    paths = sorted(p for p in versions_dir.glob("*.py") if p.name != "__init__.py")
    parsed: dict[str, tuple[str, Path]] = {}
    for path in paths:
        version, name = _parse_file_name(path)
        if version in parsed:
            raise MigrationDiscoveryError(
                f"Duplicate migration version {version}: '{parsed[version][1].name}' and '{path.name}'"
            )
        parsed[version] = (name, path)

    scripts = [
        MigrationScript(
            version=version,
            name=name,
            path=path,
            checksum=compute_checksum(path),
            up=_load_up(package, path),
        )
        for version, (name, path) in sorted(parsed.items())
    ]
    logger.debug("Discovered migrations: %s", [script.version for script in scripts])
    return scripts
