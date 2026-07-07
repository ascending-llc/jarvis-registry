"""Unit tests for DownstreamTokenCheckService (single $or token lookup)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from auth_server.services.downstream_token_service import DownstreamTokenCheckService

USER_ID = "507f1f77bcf86cd799439011"


@pytest.fixture
def service() -> DownstreamTokenCheckService:
    return DownstreamTokenCheckService()


def _server() -> Mock:
    s = Mock()
    s.serverName = "github"
    return s


async def test_returns_false_when_server_unknown(service):
    with patch(
        "auth_server.services.downstream_token_service.ExtendedMCPServer.find_one",
        new_callable=AsyncMock,
        return_value=None,
    ):
        assert await service.has_valid_downstream_token(USER_ID, "github") is False


async def test_returns_true_when_token_exists(service):
    token = Mock()
    token.expiresAt = datetime.now(UTC) + timedelta(hours=1)
    with (
        patch(
            "auth_server.services.downstream_token_service.ExtendedMCPServer.find_one",
            new_callable=AsyncMock,
            return_value=_server(),
        ),
        patch(
            "auth_server.services.downstream_token_service.Token.find_one",
            new_callable=AsyncMock,
            return_value=token,
        ) as mock_find,
    ):
        assert await service.has_valid_downstream_token(USER_ID, "github") is True
        # Single round-trip with an $or over access/refresh shapes.
        mock_find.assert_awaited_once()
        query = mock_find.await_args.args[0]
        assert "$or" in query
        assert len(query["$or"]) == 2


async def test_returns_false_when_no_token(service):
    with (
        patch(
            "auth_server.services.downstream_token_service.ExtendedMCPServer.find_one",
            new_callable=AsyncMock,
            return_value=_server(),
        ),
        patch(
            "auth_server.services.downstream_token_service.Token.find_one",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        assert await service.has_valid_downstream_token(USER_ID, "github") is False
