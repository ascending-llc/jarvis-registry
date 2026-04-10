"""
Unit tests for user_service module.

Tests user_id resolution from MongoDB using username and email lookups.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId

from auth_server.services.user_service import UserService


@pytest.mark.unit
@pytest.mark.auth
class TestUserService:
    """Test UserService class methods."""

    @pytest.mark.asyncio
    async def test_resolve_user_id_by_username(self):
        """Test resolving user_id by username from MongoDB."""
        # Mock user object
        mock_user = MagicMock()
        mock_user.id = ObjectId("507f1f77bcf86cd799439011")

        # Patch User.find_one to return mock user
        with patch("auth_server.services.user_service.User.find_one", new_callable=AsyncMock) as mock_find:
            mock_find.return_value = mock_user

            user_info = {"username": "testuser", "email": "test@example.com"}

            service = UserService()
            result = await service.resolve_user_id(user_info)

            # Should return user_id as string
            assert result == "507f1f77bcf86cd799439011"

            # Should have called find_one with username
            mock_find.assert_called_once_with({"username": "testuser"})

    @pytest.mark.asyncio
    async def test_resolve_user_id_by_email_fallback(self):
        """Test resolving user_id by email when username lookup fails."""
        mock_user = MagicMock()
        mock_user.id = ObjectId("507f1f77bcf86cd799439012")

        with patch("auth_server.services.user_service.User.find_one", new_callable=AsyncMock) as mock_find:
            # First call (username) returns None, second call (email) returns user
            mock_find.side_effect = [None, mock_user]

            user_info = {"username": "nonexistent", "email": "test@example.com"}

            service = UserService()
            result = await service.resolve_user_id(user_info)

            # Should return user_id from email lookup
            assert result == "507f1f77bcf86cd799439012"

            # Should have tried username first, then email
            assert mock_find.call_count == 2
            mock_find.assert_any_call({"username": "nonexistent"})
            mock_find.assert_any_call({"email": "test@example.com"})

    @pytest.mark.asyncio
    async def test_resolve_user_id_user_not_found(self):
        """Test resolving user_id when user doesn't exist."""
        with patch("auth_server.services.user_service.User.find_one", new_callable=AsyncMock) as mock_find:
            # Both username and email lookups return None
            mock_find.return_value = None

            user_info = {"username": "nonexistent", "email": "nonexistent@example.com"}

            service = UserService()
            result = await service.resolve_user_id(user_info)

            # Should return None when user not found
            assert result is None

            # Should have tried both username and email
            assert mock_find.call_count == 2

    @pytest.mark.asyncio
    async def test_resolve_user_id_no_username_only_email(self):
        """Test resolving user_id when only email is provided."""
        mock_user = MagicMock()
        mock_user.id = ObjectId("507f1f77bcf86cd799439013")

        with patch("auth_server.services.user_service.User.find_one", new_callable=AsyncMock) as mock_find:
            mock_find.return_value = mock_user

            user_info = {"email": "test@example.com"}

            service = UserService()
            result = await service.resolve_user_id(user_info)

            # Should return user_id from email lookup
            assert result == "507f1f77bcf86cd799439013"

            # Should only call find_one with email (no username to try)
            mock_find.assert_called_once_with({"email": "test@example.com"})

    @pytest.mark.asyncio
    async def test_resolve_user_id_no_username_no_email(self):
        """Test resolving user_id with no username or email."""
        user_info = {}

        service = UserService()
        result = await service.resolve_user_id(user_info)

        # Should return None when no identifiers provided
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_user_id_empty_strings(self):
        """Test resolving user_id with empty string values."""
        user_info = {"username": "", "email": ""}

        service = UserService()
        result = await service.resolve_user_id(user_info)

        # Should return None for empty strings
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_user_id_database_error(self):
        """Test resolving user_id when database raises an error."""
        with patch("auth_server.services.user_service.User.find_one", new_callable=AsyncMock) as mock_find:
            # Simulate database error
            mock_find.side_effect = Exception("Database connection error")

            user_info = {"username": "testuser", "email": "test@example.com"}

            service = UserService()
            result = await service.resolve_user_id(user_info)

            # Should return None on error (graceful degradation)
            assert result is None

    @pytest.mark.asyncio
    async def test_service_instance_resolves_user(self):
        """Test that a constructed service instance resolves users."""
        mock_user = MagicMock()
        mock_user.id = ObjectId("507f1f77bcf86cd799439014")

        with patch("auth_server.services.user_service.User.find_one", new_callable=AsyncMock) as mock_find:
            mock_find.return_value = mock_user

            user_info = {"username": "globaltest"}
            result = await UserService().resolve_user_id(user_info)

            assert result == "507f1f77bcf86cd799439014"

    @pytest.mark.asyncio
    async def test_resolve_user_id_prefers_username_over_email(self):
        """Test that username is preferred when both username and email match different users."""
        mock_user_by_username = MagicMock()
        mock_user_by_username.id = ObjectId("507f1f77bcf86cd799439015")

        mock_user_by_email = MagicMock()
        mock_user_by_email.id = ObjectId("507f1f77bcf86cd799439016")

        with patch("auth_server.services.user_service.User.find_one", new_callable=AsyncMock) as mock_find:
            # Username lookup succeeds, email lookup should not be called
            mock_find.return_value = mock_user_by_username

            user_info = {"username": "testuser", "email": "different@example.com"}

            service = UserService()
            result = await service.resolve_user_id(user_info)

            # Should return user_id from username (first lookup)
            assert result == "507f1f77bcf86cd799439015"

            # Should only call find_one once (username lookup succeeded)
            mock_find.assert_called_once_with({"username": "testuser"})
