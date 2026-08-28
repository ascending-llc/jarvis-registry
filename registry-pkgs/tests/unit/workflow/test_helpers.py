"""Unit tests for registry_pkgs.workflows.helpers."""

from __future__ import annotations

import json

from registry_pkgs.core.config import WorkflowPromptSettings
from registry_pkgs.workflows import helpers
from registry_pkgs.workflows.helpers import extract_user_text


def test_workflow_prompt_settings_default_matches_900k_token_budget():
    """Default sizes to ~900K tokens (Sonnet 5's 1M-token window minus headroom) at ~4 chars/token."""
    assert WorkflowPromptSettings().workflow_prompt_max_chars == 3_600_000


def test_truncate_returns_value_unchanged_when_within_limit(monkeypatch):
    monkeypatch.setattr(helpers, "_MAX_PROMPT_CHARS", 100)
    assert helpers._truncate("short") == "short"


def test_truncate_caps_and_annotates_oversized_value(monkeypatch):
    monkeypatch.setattr(helpers, "_MAX_PROMPT_CHARS", 10)
    assert helpers._truncate("x" * 15) == f"{'x' * 10}\n[truncated: 5 chars omitted]"


def test_extract_user_text_prefers_user_text_key():
    assert extract_user_text({"user_text": "hello"}) == "hello"


def test_extract_user_text_coerces_non_string_user_text():
    assert extract_user_text({"user_text": 123}) == "123"


def test_extract_user_text_none_returns_empty():
    assert extract_user_text(None) == ""


def test_extract_user_text_empty_dict_returns_empty():
    assert extract_user_text({}) == ""


def test_extract_user_text_falls_back_to_json_for_other_shapes():
    payload = {"foo": "bar", "count": 2}
    result = extract_user_text(payload)
    # No user_text key → whole payload serialized so input is not dropped.
    assert json.loads(result) == payload


def test_extract_user_text_blank_user_text_falls_back_to_json():
    payload = {"user_text": "", "foo": "bar"}
    result = extract_user_text(payload)
    assert json.loads(result) == payload
