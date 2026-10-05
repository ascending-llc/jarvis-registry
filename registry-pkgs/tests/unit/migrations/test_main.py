"""Tests for the `python -m registry_pkgs.migrations` CLI."""

import logging
import os
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from registry_pkgs.core.config import DISABLE_DOTENV_ENV_VAR
from registry_pkgs.migrations import __main__ as cli
from registry_pkgs.migrations.discovery import MigrationDiscoveryError

_SECRET = "s3cr3t-pw"
_ENV = {
    DISABLE_DOTENV_ENV_VAR: "1",
    "MONGO_URI": "mongodb://mongo:27017/jarvis",
    "MONGODB_USERNAME": "admin",
    "MONGODB_PASSWORD": _SECRET,
    "BUILD_VERSION": "build-7",
}


@pytest.fixture
def client() -> MagicMock:
    mongo_client = MagicMock(close=AsyncMock())
    mongo_client.admin.command = AsyncMock(return_value={"ok": 1})
    return mongo_client


@pytest.fixture
def create_client(client: MagicMock) -> Iterator[MagicMock]:
    with (
        patch.dict(os.environ, _ENV, clear=True),
        patch.object(cli, "create_mongo_client", return_value=(client, "jarvis")) as create,
    ):
        yield create


@pytest.mark.parametrize("argv", [[], ["down"], ["repair"]])
def test_rejects_missing_or_unknown_command(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(argv)

    assert exc_info.value.code == 2


@pytest.mark.parametrize("code", [0, 1])
def test_up_passes_build_version_and_returns_exit_code(create_client: MagicMock, client: MagicMock, code: int) -> None:
    with patch.object(cli.runner, "up", new_callable=AsyncMock, return_value=code) as up:
        assert cli.main(["up"]) == code

    config = create_client.call_args.args[0]
    assert (config.mongo_uri, config.mongodb_username, config.mongodb_password) == (
        "mongodb://mongo:27017/jarvis",
        "admin",
        _SECRET,
    )
    client.admin.command.assert_awaited_once_with("ping")
    client.__getitem__.assert_called_once_with("jarvis")
    up.assert_awaited_once_with(client.__getitem__.return_value, "build-7")
    client.close.assert_awaited_once()


@pytest.mark.parametrize("command", ["wait", "status"])
def test_wait_and_status_dispatch(create_client: MagicMock, client: MagicMock, command: str) -> None:
    with patch.object(cli.runner, command, new_callable=AsyncMock, return_value=1) as handler:
        assert cli.main([command]) == 1

    handler.assert_awaited_once_with(client.__getitem__.return_value)
    client.close.assert_awaited_once()


def test_connection_failure_exits_1_and_closes_client(
    create_client: MagicMock, client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    client.admin.command.side_effect = ServerSelectionTimeoutError("no servers")

    with patch.object(cli.runner, "up", new_callable=AsyncMock) as up:
        assert cli.main(["up"]) == 1

    up.assert_not_awaited()
    client.close.assert_awaited_once()
    assert "no servers" in caplog.text


@pytest.mark.parametrize(
    "error",
    [MigrationDiscoveryError("bad name"), RuntimeError("unexpected")],
    ids=["discovery-error", "unexpected-error"],
)
def test_command_errors_exit_1(create_client: MagicMock, client: MagicMock, error: Exception) -> None:
    with patch.object(cli.runner, "up", new_callable=AsyncMock, side_effect=error):
        assert cli.main(["up"]) == 1

    client.close.assert_awaited_once()


def test_unresolvable_db_name_exits_1() -> None:
    with patch.dict(os.environ, {DISABLE_DOTENV_ENV_VAR: "1", "MONGO_URI": "mongodb://mongo:27017"}, clear=True):
        assert cli.main(["status"]) == 1


def test_invalid_settings_exit_1_without_logging_values(caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch.dict(os.environ, {DISABLE_DOTENV_ENV_VAR: "1"}, clear=True),
        patch.object(cli, "MongoSettings", side_effect=_validation_error(_SECRET)),
    ):
        assert cli.main(["up"]) == 1

    assert "Invalid MongoDB settings" in caplog.text
    assert _SECRET not in caplog.text


def test_never_logs_credentials(create_client: MagicMock, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with patch.object(cli.runner, "status", new_callable=AsyncMock, return_value=0):
        cli.main(["status"])

    assert "Connected to MongoDB database jarvis" in caplog.text
    assert _SECRET not in caplog.text
    assert "mongodb://" not in caplog.text


def _validation_error(secret: str) -> Exception:
    from pydantic import BaseModel, ValidationError

    class _Model(BaseModel):
        mongo_uri: int

    try:
        _Model(mongo_uri=secret)
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")
