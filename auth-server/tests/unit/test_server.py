"""Unit tests for auth_server.server: telemetry initialization and lifespan wiring."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI

from auth_server.server import _initialize_telemetry, lifespan


@pytest.mark.unit
class TestInitializeTelemetry:
    """Verify _initialize_telemetry() wires up metrics and structured logging, best-effort."""

    def test_calls_setup_metrics_and_structured_logging(self):
        with (
            patch("auth_server.server.setup_metrics") as mock_setup_metrics,
            patch("auth_server.server.configure_structured_logging") as mock_configure_logging,
        ):
            _initialize_telemetry()

        mock_setup_metrics.assert_called_once()
        mock_configure_logging.assert_called_once()

    def test_setup_metrics_failure_does_not_raise(self):
        with (
            patch("auth_server.server.setup_metrics", side_effect=RuntimeError("boom")),
            patch("auth_server.server.configure_structured_logging"),
        ):
            _initialize_telemetry()

    def test_structured_logging_failure_does_not_raise(self):
        with (
            patch("auth_server.server.setup_metrics"),
            patch("auth_server.server.configure_structured_logging", side_effect=RuntimeError("boom")),
        ):
            _initialize_telemetry()


@pytest.mark.unit
class TestLifespanTelemetryShutdown:
    """Verify _shutdown_telemetry_safe() is called on all lifespan exit paths."""

    @pytest.fixture(autouse=True)
    def mock_auth_container(self):
        with patch("auth_server.server.AuthContainer", return_value=Mock()) as mock_cls:
            yield mock_cls

    @pytest.mark.asyncio
    async def test_normal_exit_calls_shutdown_telemetry(self):
        test_app = FastAPI()

        with patch("auth_server.server._shutdown_telemetry_safe") as mock_shutdown:
            async with lifespan(test_app):
                pass

            mock_shutdown.assert_called()

    @pytest.mark.asyncio
    async def test_startup_failure_calls_shutdown_telemetry(self):
        test_app = FastAPI()

        with (
            patch("auth_server.server.init_mongodb", new=AsyncMock(side_effect=RuntimeError("db down"))),
            patch("auth_server.server._shutdown_telemetry_safe") as mock_shutdown,
        ):
            with pytest.raises(RuntimeError, match="db down"):
                async with lifespan(test_app):
                    pass

            mock_shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_error_does_not_mask_telemetry_shutdown(self):
        test_app = FastAPI()

        with (
            patch("auth_server.server.close_redis_client", side_effect=RuntimeError("shutdown boom")),
            patch("auth_server.server._shutdown_telemetry_safe") as mock_shutdown,
        ):
            async with lifespan(test_app):
                pass

            mock_shutdown.assert_called()
