"""Tests for migration discovery: naming, duplicates, the `up` contract, checksums, and import guardrails."""

import ast
import importlib
import inspect
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from beanie import Document

import registry_pkgs.migrations as migrations_package
from registry_pkgs.migrations.discovery import (
    VERSIONS_DIR,
    VERSIONS_PACKAGE,
    MigrationDiscoveryError,
    compute_checksum,
    discover_migrations,
)

_VALID_UP = "async def up(db):\n    pass\n"


@pytest.fixture
def versions_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, str]]:
    """An importable, uniquely named stand-in for the `versions` package."""
    package = f"fake_versions_{uuid4().hex}"
    directory = tmp_path / package
    directory.mkdir()
    (directory / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield directory, package
    for module in [name for name in sys.modules if name.startswith(package)]:
        del sys.modules[module]


def test_discovers_valid_scripts_sorted_by_version(versions_package: tuple[Path, str]) -> None:
    directory, package = versions_package
    (directory / "m0002_second.py").write_text(_VALID_UP)
    (directory / "m0001_first_one.py").write_text(_VALID_UP)
    (directory / "notes.md").write_text("not a migration")

    scripts = discover_migrations(directory, package)

    assert [(s.version, s.name) for s in scripts] == [("0001", "first_one"), ("0002", "second")]
    assert scripts[0].path == directory / "m0001_first_one.py"
    assert scripts[0].checksum == compute_checksum(directory / "m0001_first_one.py")
    assert inspect.iscoroutinefunction(scripts[0].up)


def test_empty_directory_yields_no_scripts(versions_package: tuple[Path, str]) -> None:
    directory, package = versions_package

    assert discover_migrations(directory, package) == []


@pytest.mark.parametrize(
    "file_name",
    ["0002_missing_prefix.py", "m002_short.py", "m00002_long.py", "m0002-dash.py", "m0002_Upper.py", "m0002_.py"],
)
def test_rejects_non_matching_file_name(versions_package: tuple[Path, str], file_name: str) -> None:
    directory, package = versions_package
    (directory / file_name).write_text(_VALID_UP)

    with pytest.raises(MigrationDiscoveryError, match=file_name):
        discover_migrations(directory, package)


def test_rejects_duplicate_version(versions_package: tuple[Path, str]) -> None:
    directory, package = versions_package
    (directory / "m0003_alpha.py").write_text(_VALID_UP)
    (directory / "m0003_beta.py").write_text(_VALID_UP)

    with pytest.raises(MigrationDiscoveryError, match=r"0003.*m0003_alpha\.py.*m0003_beta\.py"):
        discover_migrations(directory, package)


def test_rejects_bad_name_before_importing_anything(versions_package: tuple[Path, str]) -> None:
    directory, package = versions_package
    (directory / "m0001_ok.py").write_text("raise RuntimeError('must not be imported')\n" + _VALID_UP)
    (directory / "bad.py").write_text(_VALID_UP)

    with pytest.raises(MigrationDiscoveryError, match="bad.py"):
        discover_migrations(directory, package)


def test_rejects_missing_up(versions_package: tuple[Path, str]) -> None:
    directory, package = versions_package
    (directory / "m0001_no_up.py").write_text("async def down(db):\n    pass\n")

    with pytest.raises(MigrationDiscoveryError, match=r"m0001_no_up\.py.*`up"):
        discover_migrations(directory, package)


@pytest.mark.parametrize(
    "source",
    ["def up(db):\n    pass\n", "up = 42\n", "class up:\n    pass\n"],
    ids=["sync-function", "not-callable", "class"],
)
def test_rejects_non_async_up(versions_package: tuple[Path, str], source: str) -> None:
    directory, package = versions_package
    (directory / "m0001_sync_up.py").write_text(source)

    with pytest.raises(MigrationDiscoveryError, match=r"m0001_sync_up\.py.*async def up"):
        discover_migrations(directory, package)


def test_checksum_ignores_crlf(tmp_path: Path) -> None:
    lf = tmp_path / "lf.py"
    crlf = tmp_path / "crlf.py"
    lf.write_bytes(b"async def up(db):\n    pass\n")
    crlf.write_bytes(b"async def up(db):\r\n    pass\r\n")

    assert compute_checksum(lf) == compute_checksum(crlf)


def test_checksum_changes_with_content(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_bytes(b"x = 1\n")
    b.write_bytes(b"x = 2\n")

    assert compute_checksum(a) != compute_checksum(b)
    assert len(compute_checksum(a)) == 64


def test_real_versions_directory_is_valid() -> None:
    """Fails CI on a misnamed, duplicated or non-conforming migration in the shipped package."""
    scripts = discover_migrations()

    assert Path(importlib.import_module(VERSIONS_PACKAGE).__file__).parent == VERSIONS_DIR
    assert scripts, "expected at least migration 0001"
    assert scripts[0].version == "0001"
    assert scripts[0].name == "seed_access_roles"
    assert [s.version for s in scripts] == sorted(s.version for s in scripts)


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_no_migrations_module_imports_beanie() -> None:
    package_dir = Path(migrations_package.__file__).parent
    offenders = {
        str(path.relative_to(package_dir)): sorted(m for m in _imported_modules(path) if m.split(".")[0] == "beanie")
        for path in package_dir.rglob("*.py")
    }

    assert {path: mods for path, mods in offenders.items() if mods} == {}


def test_no_versions_module_binds_a_beanie_document() -> None:
    """Catches Documents imported indirectly, e.g. through `registry_pkgs.models`."""
    for path in sorted(VERSIONS_DIR.glob("m*.py")):
        module = importlib.import_module(f"{VERSIONS_PACKAGE}.{path.stem}")
        documents = [
            name for name, value in vars(module).items() if inspect.isclass(value) and issubclass(value, Document)
        ]
        assert documents == [], f"{path.name} binds Beanie Document classes {documents}"
