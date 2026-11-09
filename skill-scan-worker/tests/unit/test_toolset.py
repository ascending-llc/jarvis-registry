import pytest
from skill_scan_worker import toolset
from skill_scan_worker.adapters.base import static_fingerprint_for


class _FakeAdapter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.tool_version = "1.0.0"
        self.llm_input_unbounded = False

    def static_fingerprint(self) -> str:
        return static_fingerprint_for(self.name, self.tool_version, {})

    def build_invocation(self, skill_dir, output_file, llm):  # pragma: no cover - not used here
        raise NotImplementedError

    def parse(self, process, *, llm_requested):  # pragma: no cover - not used here
        raise NotImplementedError


def test_empty_adapters_rejected(monkeypatch) -> None:
    monkeypatch.setattr(toolset, "ENABLED_ADAPTERS", [])
    with pytest.raises(RuntimeError, match="empty"):
        toolset.validate_adapters()


def test_duplicate_names_rejected(monkeypatch) -> None:
    monkeypatch.setattr(toolset, "ENABLED_ADAPTERS", [_FakeAdapter("dup"), _FakeAdapter("dup")])
    with pytest.raises(RuntimeError, match="duplicate"):
        toolset.validate_adapters()


def test_valid_adapters_pass(monkeypatch) -> None:
    monkeypatch.setattr(toolset, "ENABLED_ADAPTERS", [_FakeAdapter("a"), _FakeAdapter("b")])
    toolset.validate_adapters()  # no raise
