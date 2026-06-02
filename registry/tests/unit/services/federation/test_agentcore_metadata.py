from registry.services.federation.agentcore_metadata import (
    detect_runtime_version_change,
    extract_runtime_arn,
    extract_runtime_version,
)
from registry_pkgs.models.enums import FederationProviderType


class TestDetectRuntimeVersionChange:
    def test_detect_changes_only_uses_runtime_version(self):
        existing = {"runtimeVersion": "1"}
        discovered_same = {"runtimeVersion": "1"}
        discovered_new = {"runtimeVersion": "2"}

        assert detect_runtime_version_change(existing, discovered_same) == []
        assert detect_runtime_version_change(existing, discovered_new) == ["runtimeVersion: 1 -> 2"]

    def test_detect_a2a_changes_only_uses_runtime_version(self):
        existing = {"providerType": FederationProviderType.AWS_AGENTCORE, "runtimeVersion": "3"}
        discovered_same = {"providerType": FederationProviderType.AWS_AGENTCORE, "runtimeVersion": "3"}
        discovered_new = {"providerType": FederationProviderType.AWS_AGENTCORE, "runtimeVersion": "4"}

        assert detect_runtime_version_change(existing, discovered_same) == []
        assert detect_runtime_version_change(existing, discovered_new) == ["runtimeVersion: 3 -> 4"]

    def test_detect_changes_ignores_non_version_payload_drift(self):
        existing = {"runtimeVersion": "1", "title": "old"}
        discovered_same_version = {
            "runtimeVersion": "1",
            "title": "new",
            "description": "changed-desc",
            "tags": ["changed-tag"],
            "status": "inactive",
            "extra": {"changed": True},
        }

        assert detect_runtime_version_change(existing, discovered_same_version) == []

    def test_detect_runtime_version_change_supports_int(self):
        existing = {"runtimeVersion": 2}
        new_data = {"runtimeVersion": 3}
        assert detect_runtime_version_change(existing, new_data) == ["runtimeVersion: 2 -> 3"]

    def test_detect_runtime_version_change_handles_missing_version(self):
        assert (
            detect_runtime_version_change(
                {
                    "providerType": FederationProviderType.AWS_AGENTCORE,
                },
                {
                    "providerType": FederationProviderType.AWS_AGENTCORE,
                },
            )
            == []
        )
        assert detect_runtime_version_change(
            {
                "providerType": FederationProviderType.AWS_AGENTCORE,
            },
            {"runtimeVersion": "1"},
        ) == ["runtimeVersion: None -> 1"]

    # --- New cases requested by change request [M2] (uncovered before) ---

    def test_missing_version_on_new_side(self):
        existing = {"runtimeVersion": "1"}
        new = {"providerType": FederationProviderType.AWS_AGENTCORE}
        assert detect_runtime_version_change(existing, new) == ["runtimeVersion: 1 -> None"]

    def test_none_metadata_on_both_sides_is_no_change(self):
        assert detect_runtime_version_change(None, None) == []

    def test_agent_version_label_when_new_side_uses_agent_version(self):
        existing = {"agentVersion": "1"}
        new = {"agentVersion": "2"}
        assert detect_runtime_version_change(existing, new) == ["agentVersion: 1 -> 2"]

    def test_agent_version_label_when_only_existing_side_has_agent_version(self):
        existing = {"agentVersion": "1"}
        new = {"runtimeVersion": "2"}
        assert detect_runtime_version_change(existing, new) == ["agentVersion: 1 -> 2"]


class TestExtractRuntimeArn:
    def test_returns_none_for_empty_metadata(self):
        assert extract_runtime_arn(None) is None
        assert extract_runtime_arn({}) is None

    def test_prefers_runtime_arn_then_falls_back_to_agent_name(self):
        assert extract_runtime_arn({"runtimeArn": "arn:r1", "agentName": "agent-1"}) == "arn:r1"
        assert extract_runtime_arn({"agentName": "agent-1"}) == "agent-1"

    def test_returns_none_when_neither_present(self):
        assert extract_runtime_arn({"providerType": "aws"}) is None


class TestExtractRuntimeVersion:
    def test_returns_none_for_empty_metadata(self):
        assert extract_runtime_version(None) is None
        assert extract_runtime_version({}) is None

    def test_prefers_runtime_version_then_falls_back_to_agent_version(self):
        assert extract_runtime_version({"runtimeVersion": "2", "agentVersion": "9"}) == "2"
        assert extract_runtime_version({"agentVersion": "7"}) == "7"

    def test_returns_none_when_no_version_key(self):
        assert extract_runtime_version({"providerType": "aws"}) is None

    def test_coerces_int_version_to_string(self):
        assert extract_runtime_version({"runtimeVersion": 3}) == "3"
