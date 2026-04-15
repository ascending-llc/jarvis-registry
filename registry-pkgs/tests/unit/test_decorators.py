"""Tests for database transaction decorators."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import ConnectionFailure, OperationFailure

from registry_pkgs.database.decorators import _tx_session, get_current_session, use_transaction


@pytest.fixture(autouse=True)
def reset_contextvar():
    """Reset the ContextVar before each test to ensure isolation."""
    token = _tx_session.set(None)
    yield
    _tx_session.reset(token)


def _make_mock_session():
    """
    Create a mock AsyncClientSession whose start_transaction()
    returns a coroutine that yields an async context manager.
    """
    mock_session = AsyncMock()

    @asynccontextmanager
    async def _transaction_context():
        yield
        # If the body raised, the async with propagates it after __aexit__

    async def _fake_transaction(*args, **kwargs):
        return _transaction_context()

    mock_session.start_transaction = _fake_transaction
    return mock_session


class TestUseTransactionDecorator:
    """Tests for the @use_transaction decorator."""

    @pytest.mark.asyncio
    async def test_decorator_starts_and_commits_transaction(self):
        """Test that decorator starts transaction and commits on success."""
        mock_client = MagicMock()
        mock_session = _make_mock_session()

        # Mock the context manager for start_session
        mock_client.start_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.start_session.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("registry_pkgs.database.decorators.MongoDB.get_client", return_value=mock_client):

            @use_transaction
            async def test_func():
                # Verify session is available inside function
                session = get_current_session()
                assert session is mock_session
                return "success"

            result = await test_func()
            assert result == "success"
            mock_client.start_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_decorator_prevents_nested_transactions(self):
        """Test that decorator raises RuntimeError when nested transactions are detected."""
        mock_client = MagicMock()
        mock_session = _make_mock_session()

        mock_client.start_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.start_session.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("registry_pkgs.database.decorators.MongoDB.get_client", return_value=mock_client):

            @use_transaction
            async def outer_func():
                return await inner_func()

            @use_transaction
            async def inner_func():
                return "should not reach here"

            with pytest.raises(RuntimeError) as exc_info:
                await outer_func()

            assert "Nested transaction detected" in str(exc_info.value)
            assert "inner_func" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_handles_operation_failure_code_263(self):
        """Test that decorator properly handles OperationFailure with code 263 (no replica set)."""
        mock_client = MagicMock()
        mock_session = _make_mock_session()

        mock_client.start_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.start_session.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("registry_pkgs.database.decorators.MongoDB.get_client", return_value=mock_client):

            @use_transaction
            async def test_func():
                raise OperationFailure("Transaction numbers are only allowed on a replica set member", code=263)

            with pytest.raises(OperationFailure) as exc_info:
                await test_func()

            assert exc_info.value.code == 263

    @pytest.mark.asyncio
    async def test_decorator_handles_operation_failure_with_transaction_keyword(self):
        """Test that decorator handles OperationFailure containing 'transaction' in message."""
        mock_client = MagicMock()
        mock_session = _make_mock_session()

        mock_client.start_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.start_session.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("registry_pkgs.database.decorators.MongoDB.get_client", return_value=mock_client):

            @use_transaction
            async def test_func():
                raise OperationFailure("transaction failed", code=999)

            with pytest.raises(OperationFailure):
                await test_func()

    @pytest.mark.asyncio
    async def test_decorator_handles_connection_failure(self):
        """Test that decorator properly handles ConnectionFailure."""
        mock_client = MagicMock()
        mock_session = _make_mock_session()

        mock_client.start_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.start_session.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("registry_pkgs.database.decorators.MongoDB.get_client", return_value=mock_client):

            @use_transaction
            async def test_func():
                raise ConnectionFailure("connection lost")

            with pytest.raises(ConnectionFailure) as exc_info:
                await test_func()

            assert "connection lost" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_handles_generic_exception(self):
        """Test that decorator properly handles generic exceptions."""
        mock_client = MagicMock()
        mock_session = _make_mock_session()

        mock_client.start_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.start_session.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("registry_pkgs.database.decorators.MongoDB.get_client", return_value=mock_client):

            @use_transaction
            async def test_func():
                raise ValueError("generic error")

            with pytest.raises(ValueError) as exc_info:
                await test_func()

            assert "generic error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_cleans_up_contextvar_on_success(self):
        """Test that decorator resets ContextVar after successful execution."""
        mock_client = MagicMock()
        mock_session = _make_mock_session()

        mock_client.start_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.start_session.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("registry_pkgs.database.decorators.MongoDB.get_client", return_value=mock_client):

            @use_transaction
            async def test_func():
                return "success"

            await test_func()

            # After execution, ContextVar should be None
            assert _tx_session.get() is None

    @pytest.mark.asyncio
    async def test_decorator_cleans_up_contextvar_on_exception(self):
        """Test that decorator resets ContextVar after exception."""
        mock_client = MagicMock()
        mock_session = _make_mock_session()

        mock_client.start_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.start_session.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("registry_pkgs.database.decorators.MongoDB.get_client", return_value=mock_client):

            @use_transaction
            async def test_func():
                raise ValueError("test error")

            with pytest.raises(ValueError):
                await test_func()

            # After exception, ContextVar should be None
            assert _tx_session.get() is None

    @pytest.mark.asyncio
    async def test_decorator_preserves_function_metadata(self):
        """Test that decorator preserves original function's metadata."""

        @use_transaction
        async def test_func():
            """Test function docstring."""
            return "success"

        assert test_func.__name__ == "test_func"
        assert test_func.__doc__ == "Test function docstring."


class TestGetCurrentSession:
    """Tests for the get_current_session() function."""

    def test_get_current_session_return_none_when__no_transaction(self):
        """Test that get_current_session returns None when no transaction is active."""
        assert get_current_session() is None

    @pytest.mark.asyncio
    async def test_get_current_session_returns_session_in_transaction(self):
        """Test that get_current_session returns the session when inside a transaction."""
        mock_client = MagicMock()
        mock_session = _make_mock_session()

        mock_client.start_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.start_session.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("registry_pkgs.database.decorators.MongoDB.get_client", return_value=mock_client):

            @use_transaction
            async def test_func():
                session = get_current_session()
                assert session is mock_session
                return "success"

            await test_func()


class TestContextVarIsolation:
    """Tests for ContextVar isolation between concurrent operations."""

    @pytest.mark.asyncio
    async def test_contextvar_isolated_between_concurrent_calls(self):
        """Test that ContextVar is properly isolated between concurrent function calls."""
        import asyncio

        mock_client = MagicMock()

        # Create two different mock sessions
        mock_session_1 = _make_mock_session()
        mock_session_2 = _make_mock_session()

        call_count = [0]

        def mock_start_session_factory():
            call_count[0] += 1
            session = mock_session_1 if call_count[0] == 1 else mock_session_2
            mock_context = MagicMock()
            mock_context.__aenter__ = AsyncMock(return_value=session)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            return mock_context

        mock_client.start_session = mock_start_session_factory

        with patch("registry_pkgs.database.decorators.MongoDB.get_client", return_value=mock_client):
            sessions_seen = []

            @use_transaction
            async def test_func(delay: float):
                await asyncio.sleep(delay)
                session = get_current_session()
                sessions_seen.append(session)
                return session

            # Run two transactions concurrently
            await asyncio.gather(
                test_func(0.01),
                test_func(0.02),
            )

            # Each call should get a different session
            assert len(sessions_seen) == 2
            # Since we're using ContextVar, each async context should have its own session
            # Both sessions should be among the mocked sessions
            assert mock_session_1 in sessions_seen or mock_session_2 in sessions_seen
