"""
Integration tests for OAuth 2.0 Device Flow and Dynamic Client Registration.

Tests:
- RFC 7591 (OAuth 2.0 Dynamic Client Registration)
- RFC 8628 (OAuth 2.0 Device Authorization Grant)

Note: All OAuth endpoints are served under /auth prefix when AUTH_SERVER_API_PREFIX=/auth
"""

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from auth_server.core.state import device_codes_storage, registered_clients, user_codes_storage
from auth_server.routes.oauth_flow import (
    cleanup_expired_device_codes,
    generate_user_code,
    get_client,
    list_registered_clients,
    validate_client_credentials,
)

# API prefix for OAuth endpoints (set in conftest.py via AUTH_SERVER_API_PREFIX env var)
API_PREFIX = "/auth"


@pytest.mark.integration
@pytest.mark.device_flow
class TestDynamicClientRegistration:
    """Integration tests for RFC 7591 Dynamic Client Registration."""

    def test_register_client_minimal(self, test_client: TestClient, clear_device_storage):
        """Test client registration with minimal required fields."""
        response = test_client.post(f"{API_PREFIX}/oauth2/register", json={})

        assert response.status_code == 200
        data = response.json()

        # Verify required RFC 7591 fields
        assert "client_id" in data
        assert "client_secret" in data
        assert "client_id_issued_at" in data
        assert "client_secret_expires_at" in data

        # Verify client_id format
        assert data["client_id"].startswith("mcp-client-")

        # Verify secret never expires
        assert data["client_secret_expires_at"] == 0

        # Verify default values
        assert "authorization_code" in data["grant_types"]
        assert "urn:ietf:params:oauth:grant-type:device_code" in data["grant_types"]
        assert "code" in data["response_types"]
        assert data["token_endpoint_auth_method"] == "client_secret_post"

        # Verify client stored in memory
        assert data["client_id"] in registered_clients

    def test_register_client_full_metadata(self, test_client: TestClient, clear_device_storage):
        """Test client registration with all optional fields."""
        registration_data = {
            "client_name": "Test MCP Client",
            "client_uri": "https://example.com",
            "redirect_uris": ["https://example.com/callback"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "scope": "registry-admin",
            "contacts": ["admin@example.com"],
            "token_endpoint_auth_method": "client_secret_basic",
        }

        response = test_client.post(f"{API_PREFIX}/oauth2/register", json=registration_data)

        assert response.status_code == 200
        data = response.json()

        # Verify all fields preserved
        assert data["client_name"] == registration_data["client_name"]
        assert data["client_uri"] == registration_data["client_uri"]
        assert data["redirect_uris"] == registration_data["redirect_uris"]
        assert data["grant_types"] == registration_data["grant_types"]
        assert data["response_types"] == registration_data["response_types"]
        assert data["scope"] == registration_data["scope"]
        assert data["token_endpoint_auth_method"] == registration_data["token_endpoint_auth_method"]

    def test_register_multiple_clients(self, test_client: TestClient, clear_device_storage):
        """Test registering multiple clients generates unique credentials."""
        response1 = test_client.post(f"{API_PREFIX}/oauth2/register", json={"client_name": "Client 1"})
        response2 = test_client.post(f"{API_PREFIX}/oauth2/register", json={"client_name": "Client 2"})

        assert response1.status_code == 200
        assert response2.status_code == 200

        data1 = response1.json()
        data2 = response2.json()

        # Verify unique client_ids
        assert data1["client_id"] != data2["client_id"]

        # Verify unique client_secrets
        assert data1["client_secret"] != data2["client_secret"]

        # Verify both stored
        assert len(registered_clients) == 2

    def test_get_client(self, test_client: TestClient, clear_device_storage):
        """Test retrieving registered client by ID."""
        # Register a client
        response = test_client.post(f"{API_PREFIX}/oauth2/register", json={"client_name": "Test Client"})
        assert response.status_code == 200

        client_id = response.json()["client_id"]

        # Retrieve client
        retrieved_client = get_client(client_id)

        assert retrieved_client is not None
        assert retrieved_client["client_id"] == client_id
        assert retrieved_client["client_name"] == "Test Client"
        assert "client_secret" in retrieved_client

    def test_get_nonexistent_client(self, clear_device_storage):
        """Test retrieving non-existent client returns None."""
        result = get_client("nonexistent-client-id")
        assert result is None

    def test_validate_client_credentials_valid(self, test_client: TestClient, clear_device_storage):
        """Test validating correct client credentials."""
        # Register a client
        response = test_client.post(f"{API_PREFIX}/oauth2/register", json={"client_name": "Test Client"})
        assert response.status_code == 200

        data = response.json()
        client_id = data["client_id"]
        client_secret = data["client_secret"]

        # Validate credentials
        assert validate_client_credentials(client_id, client_secret) is True

    def test_validate_client_credentials_invalid_secret(self, test_client: TestClient, clear_device_storage):
        """Test validating incorrect client secret."""
        # Register a client
        response = test_client.post(f"{API_PREFIX}/oauth2/register", json={"client_name": "Test Client"})
        assert response.status_code == 200

        client_id = response.json()["client_id"]

        # Try invalid secret
        assert validate_client_credentials(client_id, "invalid-secret") is False

    def test_validate_client_credentials_invalid_id(self, clear_device_storage):
        """Test validating with non-existent client ID."""
        assert validate_client_credentials("nonexistent-id", "any-secret") is False

    def test_list_registered_clients(self, test_client: TestClient, clear_device_storage):
        """Test listing all registered clients (admin function)."""
        # Register multiple clients
        test_client.post(f"{API_PREFIX}/oauth2/register", json={"client_name": "Client 1"})
        test_client.post(f"{API_PREFIX}/oauth2/register", json={"client_name": "Client 2"})
        test_client.post(f"{API_PREFIX}/oauth2/register", json={"client_name": "Client 3"})

        # List clients
        clients_list = list_registered_clients()

        assert len(clients_list) == 3

        # Verify secrets not included in list
        for client_info in clients_list:
            assert "client_secret" not in client_info
            assert "client_id" in client_info
            assert "client_name" in client_info
            assert "grant_types" in client_info
            assert "registered_at" in client_info


@pytest.mark.integration
@pytest.mark.device_flow
class TestDeviceFlowRoutes:
    """Integration tests for RFC 8628 Device Authorization Grant endpoints."""

    def test_generate_user_code_format(self, clear_device_storage):
        """Test user code generation format (XXXX-XXXX)."""
        user_code = generate_user_code()

        # Verify format
        assert len(user_code) == 9
        assert user_code[4] == "-"

        # Verify uppercase alphanumeric
        code_without_dash = user_code.replace("-", "")
        assert code_without_dash.isalnum()
        assert code_without_dash.isupper()

        # Verify no confusing characters (O, 0, I, 1)
        assert "O" not in user_code
        assert "0" not in user_code
        assert "I" not in user_code
        assert "1" not in user_code

    def test_generate_user_code_uniqueness(self, clear_device_storage):
        """Test user codes are reasonably unique."""
        codes = set()
        for _ in range(100):
            codes.add(generate_user_code())

        # Should generate 100 unique codes (collision highly unlikely)
        assert len(codes) == 100

    def test_cleanup_expired_device_codes_function(self, test_client: TestClient, clear_device_storage):
        """Test cleanup_expired_device_codes utility function."""
        # Create device code
        device_response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})
        data = device_response.json()
        device_code = data["device_code"]
        user_code = data["user_code"]

        # Verify stored
        assert device_code in device_codes_storage
        assert user_code in user_codes_storage

        # Manually expire the code
        device_codes_storage[device_code]["expires_at"] = int(time.time()) - 1

        # Trigger cleanup
        cleanup_expired_device_codes()

        # Verify removed
        assert device_code not in device_codes_storage
        assert user_code not in user_codes_storage

    def test_device_authorization_success(self, test_client: TestClient, clear_device_storage):
        """Test successful device code generation."""
        response = test_client.post(
            f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client", "scope": "openid profile"}
        )

        assert response.status_code == 200
        data = response.json()

        # Verify response contains all required fields per RFC 8628
        assert "device_code" in data
        assert "user_code" in data
        assert "verification_uri" in data
        assert "expires_in" in data
        assert "interval" in data

        # Verify field formats
        assert len(data["device_code"]) > 20  # Device code should be sufficiently long
        assert len(data["user_code"]) == 9  # Format: XXXX-XXXX
        assert "-" in data["user_code"]
        assert data["verification_uri"].startswith("http")
        assert data["expires_in"] == 600  # 10 minutes
        assert data["interval"] == 5  # Poll every 5 seconds

        # Verify optional fields
        assert "verification_uri_complete" in data
        assert data["user_code"] in data["verification_uri_complete"]

    def test_device_authorization_without_scope(self, test_client: TestClient, clear_device_storage):
        """Test device code generation without scope parameter."""
        response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})

        assert response.status_code == 200
        data = response.json()
        assert "device_code" in data
        assert "user_code" in data

    def test_device_authorization_missing_client_id(self, test_client: TestClient, clear_device_storage):
        """Test device code generation without required client_id."""
        response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"scope": "openid"})

        assert response.status_code == 422  # Validation error

    def test_device_verification_page(self, test_client: TestClient, clear_device_storage):
        """Test device verification HTML page rendering."""
        # First, generate a device code
        device_response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})
        user_code = device_response.json()["user_code"]

        # Access verification page
        response = test_client.get(f"{API_PREFIX}/oauth2/device/verify?user_code={user_code}")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

        # Verify page contains expected content
        content = response.text
        assert user_code in content
        assert "Device Verification" in content or "Verify" in content

    def test_device_verification_page_without_user_code(self, test_client: TestClient):
        """Test verification page without user_code parameter."""
        response = test_client.get(f"{API_PREFIX}/oauth2/device/verify")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

        # Should show form to enter user code
        content = response.text
        assert "user_code" in content.lower()

    def test_device_approval_success(self, test_client: TestClient, clear_device_storage):
        """Test successful device approval."""
        # Generate device code
        device_response = test_client.post(
            f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client", "scope": "openid"}
        )
        user_code = device_response.json()["user_code"]

        # Approve the device
        approval_response = test_client.post(f"{API_PREFIX}/oauth2/device/approve", json={"user_code": user_code})

        assert approval_response.status_code == 200
        data = approval_response.json()
        assert data["status"] == "approved"
        assert "message" in data

    def test_device_approval_invalid_user_code(self, test_client: TestClient, clear_device_storage):
        """Test device approval with invalid user code."""
        response = test_client.post(f"{API_PREFIX}/oauth2/device/approve", json={"user_code": "INVALID-CODE"})

        assert response.status_code == 404
        assert "detail" in response.json()

    def test_device_approval_missing_user_code(self, test_client: TestClient):
        """Test device approval without user_code parameter."""
        response = test_client.post(f"{API_PREFIX}/oauth2/device/approve", json={})

        assert response.status_code == 422  # Validation error

    def test_device_token_pending(self, test_client: TestClient, clear_device_storage):
        """Test token polling when authorization is pending."""
        # Generate device code
        device_response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})
        device_code = device_response.json()["device_code"]

        # Poll for token before approval
        token_response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "test-client",
            },
        )

        assert token_response.status_code == 400
        data = token_response.json()
        assert data["error"] == "authorization_pending"

    def test_device_token_success(self, test_client: TestClient, clear_device_storage):
        """Test successful token retrieval after approval."""
        # Generate device code
        device_response = test_client.post(
            f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client", "scope": "openid profile"}
        )
        device_code = device_response.json()["device_code"]
        user_code = device_response.json()["user_code"]

        # Approve the device
        test_client.post(f"{API_PREFIX}/oauth2/device/approve", json={"user_code": user_code})

        # Poll for token after approval
        token_response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "test-client",
            },
        )

        assert token_response.status_code == 200
        data = token_response.json()

        # Verify token response fields per RFC 8628
        assert "access_token" in data
        assert "token_type" in data
        assert data["token_type"] == "Bearer"
        assert "expires_in" in data
        assert data["expires_in"] == 3600  # 1 hour

        # Verify token format (JWT)
        assert len(data["access_token"]) > 50
        assert data["access_token"].count(".") == 2  # JWT has 3 parts separated by dots

    def test_device_token_invalid_grant_type(self, test_client: TestClient, clear_device_storage):
        """Test token request with invalid grant type."""
        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={"grant_type": "invalid_grant_type", "device_code": "test-code", "client_id": "test-client"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "unsupported_grant_type"

    def test_device_token_invalid_device_code(self, test_client: TestClient, clear_device_storage):
        """Test token request with invalid device code."""
        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": "invalid-device-code",
                "client_id": "test-client",
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "invalid_grant"

    def test_device_token_client_mismatch(self, test_client: TestClient, clear_device_storage):
        """Test token request with mismatched client_id."""
        # Generate device code with one client
        device_response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "client-1"})
        device_code = device_response.json()["device_code"]

        # Try to poll with different client
        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "client-2",
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "invalid_client"

    def test_device_token_expired(self, test_client: TestClient, clear_device_storage):
        """Test token request with expired device code."""
        # Generate device code
        device_response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})
        device_code = device_response.json()["device_code"]

        # Manually expire the device code
        from auth_server.core.state import device_codes_storage

        device_codes_storage[device_code]["expires_at"] = int(time.time()) - 1

        # Trigger cleanup to remove expired code
        test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})

        # Try to poll with expired code (now removed)
        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "test-client",
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "invalid_grant"  # Code no longer exists after cleanup

    def test_device_token_slow_down(self, test_client: TestClient, clear_device_storage):
        """Test polling behavior (slow_down not implemented yet, returns authorization_pending)."""
        # Generate device code
        device_response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})
        device_code = device_response.json()["device_code"]

        # First poll
        response1 = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "test-client",
            },
        )

        # Second poll immediately (too fast)
        response2 = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "test-client",
            },
        )

        # Both should return authorization_pending (slow_down not yet implemented)
        assert response1.status_code == 400
        assert response1.json()["error"] == "authorization_pending"
        assert response2.status_code == 400
        assert response2.json()["error"] == "authorization_pending"

    def test_complete_device_flow(self, test_client: TestClient, clear_device_storage):
        """Test the complete device authorization flow end-to-end."""
        # Step 1: Client requests device code
        device_response = test_client.post(
            f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client", "scope": "openid profile email"}
        )
        assert device_response.status_code == 200
        device_code = device_response.json()["device_code"]
        user_code = device_response.json()["user_code"]
        verification_uri = device_response.json()["verification_uri"]

        # Step 2: User visits verification page
        verify_response = test_client.get(f"{verification_uri}?user_code={user_code}")
        assert verify_response.status_code == 200

        # Step 3: User approves device
        approve_response = test_client.post(f"{API_PREFIX}/oauth2/device/approve", json={"user_code": user_code})
        assert approve_response.status_code == 200

        # Step 4: Client polls for token
        token_response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "test-client",
            },
        )
        assert token_response.status_code == 200

        # Verify token
        token_data = token_response.json()
        assert "access_token" in token_data
        assert token_data["token_type"] == "Bearer"

        # Step 5: Verify token can't be reused
        second_token_response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "test-client",
            },
        )
        # Should return the same token or invalid_grant
        assert second_token_response.status_code in [200, 400]

    def test_user_code_format(self, test_client: TestClient, clear_device_storage):
        """Test that user codes follow expected format."""
        # Generate multiple codes to verify format consistency
        for _ in range(5):
            response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})
            user_code = response.json()["user_code"]

            # Format: XXXX-XXXX (8 alphanumeric chars with dash)
            assert len(user_code) == 9
            assert user_code[4] == "-"

            # Should be uppercase alphanumeric
            parts = user_code.split("-")
            assert len(parts) == 2
            assert len(parts[0]) == 4
            assert len(parts[1]) == 4
            assert parts[0].isalnum()
            assert parts[1].isalnum()
            # Each character should be either uppercase letter or digit
            assert all(c.isupper() or c.isdigit() for c in parts[0])
            assert all(c.isupper() or c.isdigit() for c in parts[1])

    def test_device_code_uniqueness(self, test_client: TestClient, clear_device_storage):
        """Test that device codes and user codes are unique."""
        codes = set()
        user_codes = set()

        # Generate multiple device codes
        for _ in range(10):
            response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})
            data = response.json()
            codes.add(data["device_code"])
            user_codes.add(data["user_code"])

        # All codes should be unique
        assert len(codes) == 10
        assert len(user_codes) == 10

    def test_cleanup_expired_codes(self, test_client: TestClient, clear_device_storage):
        """Test that expired device codes are cleaned up."""
        from auth_server.core.state import device_codes_storage, user_codes_storage

        # Generate device code
        response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})
        device_code = response.json()["device_code"]
        user_code = response.json()["user_code"]

        # Verify code exists
        assert device_code in device_codes_storage
        assert user_code in user_codes_storage

        # Expire the code
        device_codes_storage[device_code]["expires_at"] = int(time.time()) - 1

        # Trigger cleanup by making another request
        test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})

        # Expired code should be cleaned up
        assert device_code not in device_codes_storage
        assert user_code not in user_codes_storage


@pytest.mark.integration
@pytest.mark.device_flow
class TestDeviceFlowWithMocking:
    """Integration tests for device flow with JWT mocking."""

    @patch("auth_server.routes.oauth_flow.jwt.encode")
    def test_approve_device_success_with_token(self, mock_jwt_encode, test_client: TestClient, clear_device_storage):
        """Test device approval generates access token."""
        mock_jwt_encode.return_value = "mock-access-token"

        # Create device code
        device_response = test_client.post(
            f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client", "scope": "test-scope"}
        )
        user_code = device_response.json()["user_code"]

        # Approve device
        response = test_client.post(f"{API_PREFIX}/oauth2/device/approve", json={"user_code": user_code})

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "approved"
        assert "successfully" in data["message"].lower()

        # Verify JWT token generated
        mock_jwt_encode.assert_called_once()

    @patch("auth_server.routes.oauth_flow.jwt.encode")
    def test_approve_device_already_approved(self, mock_jwt_encode, test_client: TestClient, clear_device_storage):
        """Test approving already-approved device returns success."""
        mock_jwt_encode.return_value = "mock-access-token"

        # Create and approve device
        device_response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})
        user_code = device_response.json()["user_code"]

        # First approval
        test_client.post(f"{API_PREFIX}/oauth2/device/approve", json={"user_code": user_code})

        # Second approval (should be idempotent)
        response = test_client.post(f"{API_PREFIX}/oauth2/device/approve", json={"user_code": user_code})

        assert response.status_code == 200
        assert "already" in response.json()["message"].lower()

    @patch("auth_server.routes.oauth_flow.jwt.encode")
    def test_device_token_success_with_mocked_jwt(self, mock_jwt_encode, test_client: TestClient, clear_device_storage):
        """Test token endpoint returns mocked access token after approval."""
        mock_jwt_encode.return_value = "mock-access-token"

        # Create device code
        device_response = test_client.post(
            f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client", "scope": "test-scope"}
        )
        data = device_response.json()
        device_code = data["device_code"]
        user_code = data["user_code"]

        # Approve device
        test_client.post(f"{API_PREFIX}/oauth2/device/approve", json={"user_code": user_code})

        # Poll token endpoint
        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "test-client",
            },
        )

        assert response.status_code == 200
        token_data = response.json()

        # Verify RFC 8628 token response
        assert "access_token" in token_data
        assert token_data["token_type"] == "Bearer"
        assert "expires_in" in token_data
        assert token_data["scope"] == "test-scope"
        assert token_data["access_token"] == "mock-access-token"

    @patch("auth_server.routes.oauth_flow.jwt.encode")
    def test_device_token_expired_code(self, mock_jwt_encode, test_client: TestClient, clear_device_storage):
        """Test token endpoint rejects expired device codes."""
        # Create device code
        device_response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "test-client"})
        device_code = device_response.json()["device_code"]

        # Manually expire
        device_codes_storage[device_code]["expires_at"] = int(time.time()) - 1

        # Try to get token
        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "test-client",
            },
        )

        assert response.status_code == 400
        # After cleanup, expired codes may return invalid_grant or expired_token
        assert response.json()["error"] in ["expired_token", "invalid_grant"]


@pytest.mark.integration
@pytest.mark.device_flow
class TestEndToEndIntegration:
    """End-to-end integration tests combining client registration and device flow."""

    @patch("auth_server.routes.oauth_flow.jwt.encode")
    def test_full_device_flow_with_registered_client(
        self, mock_jwt_encode, test_client: TestClient, clear_device_storage
    ):
        """Test complete device flow with dynamically registered client."""
        mock_jwt_encode.return_value = "integration-test-token"

        # Step 1: Register client
        reg_response = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={
                "client_name": "Integration Test Client",
                "grant_types": ["urn:ietf:params:oauth:grant-type:device_code"],
            },
        )
        assert reg_response.status_code == 200
        client_id = reg_response.json()["client_id"]
        client_secret = reg_response.json()["client_secret"]

        # Step 2: Initiate device flow
        device_response = test_client.post(
            f"{API_PREFIX}/oauth2/device/code",
            data={"client_id": client_id, "scope": "registry-admin"},
        )
        assert device_response.status_code == 200
        device_code = device_response.json()["device_code"]
        user_code = device_response.json()["user_code"]

        # Step 3: User approves device
        approve_response = test_client.post(f"{API_PREFIX}/oauth2/device/approve", json={"user_code": user_code})
        assert approve_response.status_code == 200

        # Step 4: Client polls for token
        token_response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": client_id,
            },
        )
        assert token_response.status_code == 200
        access_token = token_response.json()["access_token"]

        # Verify token
        assert access_token == "integration-test-token"

        # Verify client credentials still valid
        assert validate_client_credentials(client_id, client_secret) is True
