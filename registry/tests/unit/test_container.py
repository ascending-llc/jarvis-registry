"""Unit tests for RegistryContainer — model selection logic."""

from unittest.mock import MagicMock, patch

import pytest

from registry.container import RegistryContainer

_AIP_ARN = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/bedrock-governance-sonnet"
_FALLBACK_MODEL = "amazon.nova-2-lite-v1:0"


def _make_settings(*, aip_arn: str | None = None) -> MagicMock:
    settings = MagicMock()
    settings.aws_bedrock_sonnet_aip_arn = aip_arn
    settings.aws_workflow_llm_model = _FALLBACK_MODEL
    settings.workflow_llm_model_id = aip_arn or _FALLBACK_MODEL
    settings.aws_region = "us-east-1"
    settings.aws_access_key_id = None
    settings.aws_secret_access_key = None
    settings.aws_session_token = None
    settings.registry_internal_url = "http://localhost:7860"
    settings.jwt_signing_config = MagicMock()
    return settings


def _make_container(settings: MagicMock) -> RegistryContainer:
    container = RegistryContainer(
        settings=settings,
        db_client=MagicMock(),
        redis_client=MagicMock(),
    )
    container.__dict__["a2a_client_registry"] = MagicMock()
    return container


@pytest.mark.unit
class TestWorkflowRunnerModelSelection:
    @patch("registry.container.WorkflowRunner")
    @patch("registry.container.MongoDB")
    @patch("registry.container.AwsBedrock")
    def test_uses_aip_arn_when_set(self, mock_bedrock, mock_mongodb, mock_runner):
        container = _make_container(_make_settings(aip_arn=_AIP_ARN))

        _ = container.workflow_runner

        mock_bedrock.assert_called_once()
        assert mock_bedrock.call_args.kwargs["id"] == _AIP_ARN
        assert mock_runner.call_args.kwargs["client_provider"] is container.a2a_client_registry.get_client

    @patch("registry.container.WorkflowRunner")
    @patch("registry.container.MongoDB")
    @patch("registry.container.AwsBedrock")
    def test_falls_back_to_workflow_llm_model_when_arn_not_set(self, mock_bedrock, mock_mongodb, mock_runner):
        container = _make_container(_make_settings(aip_arn=None))

        _ = container.workflow_runner

        mock_bedrock.assert_called_once()
        assert mock_bedrock.call_args.kwargs["id"] == _FALLBACK_MODEL

    @patch("registry.container.WorkflowRunner")
    @patch("registry.container.MongoDB")
    @patch("registry.container.AwsBedrock")
    def test_falls_back_to_workflow_llm_model_when_arn_empty_string(self, mock_bedrock, mock_mongodb, mock_runner):
        container = _make_container(_make_settings(aip_arn=""))

        _ = container.workflow_runner

        mock_bedrock.assert_called_once()
        assert mock_bedrock.call_args.kwargs["id"] == _FALLBACK_MODEL
