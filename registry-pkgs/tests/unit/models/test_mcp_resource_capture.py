from copy import deepcopy

import pytest

from registry_pkgs.models.mcp_resource_capture import (
    RESOURCE_CAPTURE_KEY,
    create_resource_capture,
    resource_capture_revision,
)


def _captured() -> dict:
    config = {"url": "https://example.test/mcp", "apiKey": {"key": "shared"}, "resources": [{"uri": "files://public"}]}
    config[RESOURCE_CAPTURE_KEY] = create_resource_capture(config, config["resources"])
    return config


def test_same_successful_capture_reuses_revision_and_title_changes_do_not_invalidate() -> None:
    config = _captured()
    revision = resource_capture_revision(config)
    assert revision
    assert create_resource_capture(config, config["resources"])["revision"] == revision
    config["title"] = "New title"
    assert resource_capture_revision(config) == revision


@pytest.mark.parametrize(
    "change",
    [
        {"url": "https://other.test/mcp"},
        {"type": "sse"},
        {"apiKey": {"key": "new"}},
        {"headers": {"Authorization": "different"}},
        {"requiresOAuth": True},
        {"oauth": {}},
        {"resources": [{"uri": "private://another-user"}]},
    ],
)
def test_changed_endpoint_auth_or_contents_cannot_reuse_proof(change: dict) -> None:
    config = _captured()
    config.update(change)
    assert resource_capture_revision(config) is None


def test_legacy_and_oauth_resources_never_get_shared_proof() -> None:
    assert resource_capture_revision({"resources": [{"uri": "private://old"}]}) is None
    assert create_resource_capture({"oauth": None}, []) is None
    assert create_resource_capture({"requiresOAuth": True}, []) is None


def test_new_capture_gets_new_revision_without_changing_previous_proof() -> None:
    config = _captured()
    before = deepcopy(config)
    proof = create_resource_capture(config, [{"uri": "files://new"}])
    assert proof["revision"] != resource_capture_revision(config)
    assert config == before
