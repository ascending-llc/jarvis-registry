"""Unit tests for registry_pkgs.workflows.helpers."""

from __future__ import annotations

import json

from registry_pkgs.workflows.helpers import extract_user_text


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
