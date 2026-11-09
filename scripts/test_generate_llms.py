import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import generate_llms


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            '---\ntitle: Release\ndescription: "Release summary"\ntags:\n  - Release\n---\n\nBody text.',
            "Release summary",
        ),
        ("---\ndescription: >\n  A multiline\n  description.\n---\n\nBody text.", "A multiline description."),
        ("---\ntitle: Example\ndate: 2026-10-29\n---\n\n# Heading\n\nBody **summary**.", "Body summary."),
        ("---\ndescription: null\n---\n\nBody summary.", "Body summary."),
        ("# Heading\n\nPlain body summary.", "Plain body summary."),
    ],
)
def test_page_description(content: str, expected: str) -> None:
    assert generate_llms.page_description(content) == expected


@pytest.fixture
def local_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "index.md").write_text("# Example\n\nOriginal summary.\n", encoding="utf-8")
    (tmp_path / "mkdocs.yml").write_text(
        "site_name: Example\nsite_url: https://example.com/\nnav:\n  - Home: index.md\n", encoding="utf-8"
    )
    monkeypatch.setattr(generate_llms, "ROOT", tmp_path)
    return docs_dir


def test_generate_preserves_timestamp_after_checkout(local_docs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Mock()
    clock.now.return_value = datetime(2026, 10, 30, tzinfo=UTC)
    monkeypatch.setattr(generate_llms, "datetime", clock)
    original = generate_llms.generate()
    for path, content in original.items():
        path.write_text(content, encoding="utf-8")
    clock.now.return_value = datetime(2030, 1, 1, tzinfo=UTC)
    for path in [local_docs / "index.md", local_docs.parent / "mkdocs.yml", *original]:
        os.utime(path, (1893456000, 1893456000))
    assert generate_llms.generate() == original
    monkeypatch.setattr("sys.argv", ["generate_llms.py", "--check"])
    generate_llms.main()


def test_generate_detects_content_change(local_docs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Mock()
    clock.now.return_value = datetime(2026, 10, 30, tzinfo=UTC)
    monkeypatch.setattr(generate_llms, "datetime", clock)
    original = generate_llms.generate()
    for path, content in original.items():
        path.write_text(content, encoding="utf-8")
    clock.now.return_value = datetime(2030, 1, 1, tzinfo=UTC)
    (local_docs / "index.md").write_text("# Example\n\nChanged summary.\n", encoding="utf-8")
    updated = generate_llms.generate()
    for path, content in updated.items():
        assert content != original[path]
        assert "Last updated: 2030-01-01T00:00:00Z" in content
        assert "Changed summary." in content
    monkeypatch.setattr("sys.argv", ["generate_llms.py", "--check"])
    with pytest.raises(SystemExit, match="Out of date"):
        generate_llms.main()
