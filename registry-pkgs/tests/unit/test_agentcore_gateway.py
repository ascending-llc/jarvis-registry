"""Unit tests for AgentCoreGateway model."""

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from registry_pkgs.models.agentcore_gateway import AgentCoreGateway
from registry_pkgs.models.oauth_provider_config import CognitoConfig, OAuthProviderConfig


@pytest.mark.unit
class TestAgentCoreGatewayModel:
    @staticmethod
    def _oauth_provider_adapter() -> TypeAdapter:
        return TypeAdapter(OAuthProviderConfig)

    def test_basic_construction_and_defaults(self):
        gateway = AgentCoreGateway.model_construct(
            name="sre-gateway",
            arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/sre",
            region="us-east-1",
            status="active",
            tags=[],
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
            oauthProvider=None,
        )

        assert gateway.name == "sre-gateway"
        assert gateway.status == "active"
        assert gateway.tags == []
        assert isinstance(gateway.createdAt, datetime)
        assert gateway.createdAt.tzinfo == UTC
        assert isinstance(gateway.updatedAt, datetime)
        assert gateway.updatedAt.tzinfo == UTC

    def test_oauth_provider_discriminator_parsing(self):
        oauth_provider = self._oauth_provider_adapter().validate_python(
            {
                "providerType": "cognito",
                "clientId": "client-123",
                "userPoolId": "us-east-1_ABC123",
                "region": "us-east-1",
            }
        )

        gateway = AgentCoreGateway.model_construct(
            name="sre-gateway",
            arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/sre",
            region="us-east-1",
            oauthProvider=oauth_provider,
            status="active",
            tags=[],
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
        )

        assert gateway.oauthProvider is not None
        assert isinstance(gateway.oauthProvider, CognitoConfig)
        assert (
            gateway.oauthProvider.token_url == "https://us-east-1ABC123.auth.us-east-1.amazoncognito.com/oauth2/token"
        )

    def test_helper_outputs(self):
        oauth_provider = self._oauth_provider_adapter().validate_python(
            {
                "providerType": "okta",
                "clientId": "client-123",
                "domain": "dev-123456.okta.com",
            }
        )

        gateway = AgentCoreGateway.model_construct(
            name="sre-gateway",
            arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/sre",
            region="us-east-1",
            oauthProvider=oauth_provider,
            status="active",
            tags=[],
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
        )

        assert gateway.get_oauth_service_name() == "agentcore-gateway-sre-gateway"
        assert gateway.get_provider_display_name() == "Okta"

    def test_helper_unknown_provider_when_missing_oauth_provider(self):
        gateway = AgentCoreGateway.model_construct(
            name="no-auth-gateway",
            arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/no-auth",
            region="us-east-1",
            oauthProvider=None,
            status="active",
            tags=[],
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
        )

        assert gateway.get_provider_display_name() == "Unknown"

    def test_invalid_oauth_provider_raises_validation_error(self):
        with pytest.raises(ValidationError):
            self._oauth_provider_adapter().validate_python(
                {
                    "providerType": "not-supported",
                    "clientId": "client-x",
                }
            )

    def test_settings_indexes(self):
        indexes = AgentCoreGateway.Settings.indexes
        index_documents = [idx.document for idx in indexes]

        assert {"arn": 1} in [doc["key"] for doc in index_documents]
        assert {"name": 1} in [doc["key"] for doc in index_documents]
        assert {"status": 1} in [doc["key"] for doc in index_documents]
        assert {"oauthProvider.providerType": 1} in [doc["key"] for doc in index_documents]

        arn_index = next(doc for doc in index_documents if doc["key"] == {"arn": 1})
        name_index = next(doc for doc in index_documents if doc["key"] == {"name": 1})
        assert arn_index.get("unique") is True
        assert name_index.get("unique") is True

    def test_oauth_provider_type_annotation_is_union(self):
        # Basic smoke-check to ensure the field remains typed as discriminator union.
        annotation = AgentCoreGateway.model_fields["oauthProvider"].annotation
        assert annotation == OAuthProviderConfig | None
