from datetime import UTC, datetime

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.extended_skill import ExtendedSkill
from registry_pkgs.models.extended_skill_file import REGISTRY_SKILL_FILE_SOURCE, ExtendedSkillFile
from registry_pkgs.skill_scan.content import (
    SKILL_MD_FILENAME,
    SkillNotScannableError,
    compute_content_digest,
    has_unreadable_files,
    is_readable_file,
    materialize_skill,
    render_skill_md,
)


def _skill(**overrides) -> ExtendedSkill:
    values = {
        "name": "demo",
        "description": "a demo skill",
        "author": PydanticObjectId(),
        "authorName": "alice",
        "createdByRegistry": True,
        "body": "do the thing",
        "frontmatter": {"name": "demo", "license": "MIT"},
    }
    values.update(overrides)
    # model_construct bypasses Beanie's __init__ collection check; these helpers only read fields.
    return ExtendedSkill.model_construct(**values)


def _file(
    path: str, body: bytes | None = b"x", *, source: str = REGISTRY_SKILL_FILE_SOURCE, **overrides
) -> ExtendedSkillFile:
    values = {
        "skillId": PydanticObjectId(),
        "relativePath": path,
        "source": source,
        "mimeType": "text/plain",
        "bytes": len(body) if body is not None else 0,
        "body": body,
    }
    values.update(overrides)
    return ExtendedSkillFile.model_construct(**values)


# --- digest ---------------------------------------------------------------------------


def test_digest_is_stable_across_frontmatter_key_order():
    a = _skill(frontmatter={"name": "demo", "a": 1, "b": 2})
    b = _skill(frontmatter={"name": "demo", "b": 2, "a": 1})
    assert compute_content_digest(a, []) == compute_content_digest(b, [])


def test_digest_is_stable_across_file_order():
    f1, f2 = _file("a.py"), _file("b.py")
    assert compute_content_digest(_skill(), [f1, f2]) == compute_content_digest(_skill(), [f2, f1])


def test_digest_changes_on_content_changes():
    base = compute_content_digest(_skill(), [_file("a.py", b"one")])
    assert compute_content_digest(_skill(body="other"), [_file("a.py", b"one")]) != base
    assert (
        compute_content_digest(_skill(frontmatter={"name": "demo", "license": "GPL"}), [_file("a.py", b"one")]) != base
    )
    assert compute_content_digest(_skill(), [_file("renamed.py", b"one")]) != base
    assert compute_content_digest(_skill(), [_file("a.py", b"two")]) != base


def test_digest_handles_non_json_frontmatter_values():
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    base = compute_content_digest(_skill(frontmatter={"name": "demo", "updated": ts}), [])
    same = compute_content_digest(_skill(frontmatter={"name": "demo", "updated": ts}), [])
    other = compute_content_digest(
        _skill(frontmatter={"name": "demo", "updated": datetime(2026, 2, 1, tzinfo=UTC)}), []
    )
    assert base == same  # stable (default=str serialization is deterministic)
    assert base != other  # sensitive to the value


def test_digest_uses_meta_entry_for_unreadable_file():
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    unreadable = _file("a.py", None, source="chat-storage", bytes=10, updatedAt=ts)
    base = compute_content_digest(_skill(), [unreadable])
    bigger = _file("a.py", None, source="chat-storage", bytes=20, updatedAt=ts)
    assert compute_content_digest(_skill(), [bigger]) != base
    later = _file("a.py", None, source="chat-storage", bytes=10, updatedAt=datetime(2026, 2, 1, tzinfo=UTC))
    assert compute_content_digest(_skill(), [later]) != base


# --- readability ----------------------------------------------------------------------


def test_is_readable_file():
    assert is_readable_file(_file("a.py", b"x"))
    assert not is_readable_file(_file("a.py", b"x", source="chat-storage"))
    assert not is_readable_file(_file("a.py", None))


def test_has_unreadable_files():
    assert has_unreadable_files(_skill(createdByRegistry=False, fileCount=1), [])
    assert has_unreadable_files(_skill(), [_file("a.py", None)])
    assert has_unreadable_files(_skill(), [_file("a.py", b"x", source="chat-storage")])
    assert not has_unreadable_files(_skill(), [_file("a.py", b"x")])
    assert not has_unreadable_files(_skill(createdByRegistry=False, fileCount=0), [])


# --- render ---------------------------------------------------------------------------


def test_render_skill_md_has_frontmatter_and_body():
    out = render_skill_md(_skill(body="hello"))
    assert out.startswith("---\n")
    assert "name: demo" in out
    assert out.rstrip().endswith("hello")


# --- materialize ----------------------------------------------------------------------


def test_materialize_writes_files_with_safe_modes(tmp_path):
    root = tmp_path / "skill"
    materialize_skill(_skill(), [_file("sub/a.py", b"print(1)")], root)
    skill_md = root / SKILL_MD_FILENAME
    target = root / "sub" / "a.py"
    assert skill_md.read_text().startswith("---\n")
    assert target.read_bytes() == b"print(1)"
    assert (skill_md.stat().st_mode & 0o777) == 0o600
    assert (target.stat().st_mode & 0o777) == 0o600
    assert not (target.stat().st_mode & 0o111)  # never executable


@pytest.mark.parametrize("reserved", ["SKILL.md", "skill.md", "Skill.md"])
def test_materialize_rejects_skill_md_named_file(tmp_path, reserved):
    root = tmp_path / "skill"
    with pytest.raises(SkillNotScannableError):
        materialize_skill(_skill(), [_file(reserved, b"overwrite")], root)


@pytest.mark.parametrize("bad", ["../x", "/etc/x", "a\\b", "a/./b", "a\x01b"])
def test_materialize_rejects_unsafe_paths(tmp_path, bad):
    root = tmp_path / "skill"
    with pytest.raises(SkillNotScannableError):
        materialize_skill(_skill(), [_file(bad, b"x")], root)
    # nothing written outside root
    assert not (tmp_path / "x").exists()
    assert not (tmp_path / "etc").exists()


def test_materialize_rejects_unreadable_file(tmp_path):
    root = tmp_path / "skill"
    with pytest.raises(SkillNotScannableError):
        materialize_skill(_skill(), [_file("a.py", None, source="chat-storage")], root)


@pytest.mark.parametrize("paths", [["a", "a/b"], ["a/b", "a"]])
def test_materialize_rejects_file_directory_collision(tmp_path, paths):
    # Each path is valid alone, but "a" can't be both a file and a directory on disk.
    root = tmp_path / "skill"
    with pytest.raises(SkillNotScannableError) as exc_info:
        materialize_skill(_skill(), [_file(p, b"x") for p in paths], root)
    assert isinstance(exc_info.value.__cause__, OSError)
