"""Tests for MongoDB connection management and Beanie initialization."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import OperationFailure

from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import (
    MongoDB,
    close_mongodb,
    create_mongo_client,
    ensure_collections,
    init_mongodb,
)

_POOL_OPTIONS = {
    "directConnection": True,
    "maxPoolSize": 50,
    "minPoolSize": 0,
    "maxIdleTimeMS": 60000,
    "waitQueueTimeoutMS": 5000,
    "connectTimeoutMS": 10000,
    "serverSelectionTimeoutMS": 10000,
    "retryWrites": True,
    "retryReads": True,
}


class TestCreateMongoClient:
    """URL building and client construction shared by connect_db and the migration runner."""

    @pytest.mark.parametrize(
        ("config", "db_name", "expected_url", "expected_db"),
        [
            pytest.param(
                MongoConfig(mongo_uri="mongodb://localhost:27017/jarvis"),
                None,
                "mongodb://localhost:27017/jarvis",
                "jarvis",
                id="db-from-uri-path",
            ),
            pytest.param(
                MongoConfig(mongo_uri="mongodb://localhost:27017/jarvis"),
                "explicit",
                "mongodb://localhost:27017/explicit",
                "explicit",
                id="explicit-db-name-wins-over-uri-path",
            ),
            pytest.param(
                MongoConfig(mongo_uri="mongodb://localhost:27017"),
                "explicit",
                "mongodb://localhost:27017/explicit",
                "explicit",
                id="explicit-db-name-without-uri-path",
            ),
            pytest.param(
                MongoConfig(mongo_uri="mongodb://localhost:27017/jarvis?authSource=admin&tls=true"),
                None,
                "mongodb://localhost:27017/jarvis?authSource=admin&tls=true",
                "jarvis",
                id="query-params-passed-through",
            ),
            pytest.param(
                MongoConfig(
                    mongo_uri="mongodb://mongo:27017/jarvis?authSource=admin",
                    mongodb_username="us@r",
                    mongodb_password="p:ss/w@rd",
                ),
                None,
                "mongodb://us%40r:p%3Ass%2Fw%40rd@mongo:27017/jarvis?authSource=admin",
                "jarvis",
                id="injected-credentials-escaped",
            ),
            pytest.param(
                MongoConfig(
                    mongo_uri="mongodb://old:secret@mongo:27017/jarvis",
                    mongodb_username="new",
                    mongodb_password="pw",
                ),
                None,
                "mongodb://new:pw@mongo:27017/jarvis",
                "jarvis",
                id="injected-credentials-replace-uri-credentials",
            ),
            pytest.param(
                MongoConfig(mongo_uri="mongodb://user:pw@mongo:27017/jarvis"),
                None,
                "mongodb://user:pw@mongo:27017/jarvis",
                "jarvis",
                id="credentials-already-in-uri",
            ),
            pytest.param(
                MongoConfig(mongo_uri="mongodb://mongo:27017/jarvis", mongodb_username="only-user"),
                None,
                "mongodb://mongo:27017/jarvis",
                "jarvis",
                id="username-without-password-not-injected",
            ),
        ],
    )
    def test_builds_url_and_resolves_db_name(
        self,
        config: MongoConfig,
        db_name: str | None,
        expected_url: str,
        expected_db: str,
    ) -> None:
        with patch("registry_pkgs.database.mongodb.AsyncMongoClient") as MockClient:
            client, resolved_db = create_mongo_client(config, db_name)

        MockClient.assert_called_once_with(expected_url, **_POOL_OPTIONS)
        assert client is MockClient.return_value
        assert resolved_db == expected_db

    def test_raises_when_no_db_name_resolvable(self) -> None:
        with (
            patch("registry_pkgs.database.mongodb.AsyncMongoClient") as MockClient,
            pytest.raises(ValueError, match="database name is required"),
        ):
            create_mongo_client(MongoConfig(mongo_uri="mongodb://localhost:27017/"))

        MockClient.assert_not_called()


class TestMongoDBConnection:
    """Test MongoDB connection manager."""

    @pytest.fixture(autouse=True)
    def reset_mongodb_state(self):
        """Reset MongoDB class state before each test."""
        MongoDB.client = None
        MongoDB.database_name = None
        yield
        MongoDB.client = None
        MongoDB.database_name = None

    @pytest.fixture(autouse=True)
    def mock_ensure_collections(self):
        """connect_db creates missing collections; the database here is a MagicMock, so stub that step."""
        with patch("registry_pkgs.database.mongodb.ensure_collections", new_callable=AsyncMock) as mock:
            yield mock

    @pytest.mark.asyncio
    async def test_connect_db_creates_client(self):
        """Test connect_db initializes MongoDB connection manager."""
        # We'll test the actual connection flow in integration tests
        # Here we test the class state management
        assert MongoDB.client is None

        # Mock the entire connect flow
        with (
            patch("registry_pkgs.database.mongodb.AsyncMongoClient") as MockClient,
            patch("registry_pkgs.database.mongodb.init_beanie", new_callable=AsyncMock),
        ):
            mock_instance = MagicMock()
            mock_instance.admin = MagicMock()
            mock_instance.admin.command = AsyncMock(return_value={"ok": 1})
            mock_instance.__getitem__ = MagicMock(return_value=MagicMock())
            MockClient.return_value = mock_instance

            await MongoDB.connect_db(MongoConfig(mongo_uri="mongodb://localhost:27017/test_db"), "test_db")

            # Verify client was created
            assert MongoDB.client is not None
            assert MongoDB.database_name == "test_db"

    @pytest.mark.asyncio
    async def test_connect_db_uses_env_variables(self):
        """Test connect_db reads configuration from explicit config."""
        with (
            patch("registry_pkgs.database.mongodb.AsyncMongoClient") as MockClient,
            patch("registry_pkgs.database.mongodb.init_beanie", new_callable=AsyncMock),
        ):
            mock_instance = MagicMock()
            mock_instance.admin = MagicMock()
            mock_instance.admin.command = AsyncMock(return_value={"ok": 1})
            mock_instance.__getitem__ = MagicMock(return_value=MagicMock())
            MockClient.return_value = mock_instance

            await MongoDB.connect_db(
                MongoConfig(
                    mongo_uri="mongodb://testhost:27017/testdb",
                    mongodb_username="testuser",
                    mongodb_password="testpass",
                )
            )

            # Verify URI construction included credentials
            call_args = MockClient.call_args[0]
            assert "testuser" in call_args[0]
            assert "testpass" in call_args[0]

    @pytest.mark.asyncio
    async def test_connect_db_extracts_dbname_from_uri(self):
        """Test connect_db extracts database name from MONGO_URI."""
        with (
            patch("registry_pkgs.database.mongodb.AsyncMongoClient") as MockClient,
            patch("registry_pkgs.database.mongodb.init_beanie", new_callable=AsyncMock),
        ):
            mock_instance = MagicMock()
            mock_instance.admin = MagicMock()
            mock_instance.admin.command = AsyncMock(return_value={"ok": 1})
            mock_instance.__getitem__ = MagicMock(return_value=MagicMock())
            MockClient.return_value = mock_instance
            await MongoDB.connect_db(MongoConfig(mongo_uri="mongodb://localhost:27017/extracted_db"))

            assert MongoDB.database_name == "extracted_db"

    @pytest.mark.asyncio
    async def test_connect_db_initializes_beanie(self, mock_ensure_collections):
        """Test connect_db initializes Beanie with all document models."""
        with (
            patch("registry_pkgs.database.mongodb.AsyncMongoClient") as MockClient,
            patch("registry_pkgs.database.mongodb.init_beanie", new_callable=AsyncMock) as mock_init_beanie,
        ):
            mock_instance = MagicMock()
            mock_instance.admin = MagicMock()
            mock_instance.admin.command = AsyncMock(return_value={"ok": 1})
            mock_db = MagicMock()
            mock_instance.__getitem__ = MagicMock(return_value=mock_db)
            MockClient.return_value = mock_instance

            await MongoDB.connect_db(MongoConfig(mongo_uri="mongodb://localhost:27017/test_db"), "test_db")

            # Verify Beanie was initialized
            assert mock_init_beanie.called
            call_kwargs = mock_init_beanie.call_args[1]

            # Verify database was passed
            assert call_kwargs["database"] == mock_db

            # Verify all document models are included
            document_models = call_kwargs["document_models"]
            model_names = [model.__name__ for model in document_models]

            assert "User" in model_names
            assert "RegistryAccessRole" in model_names
            assert "ExtendedGroup" in model_names
            assert "RegistryAclEntry" in model_names
            assert "ExtendedMCPServer" in model_names
            assert "Token" in model_names
            assert "Key" in model_names
            assert "A2AAgent" in model_names
            assert "Federation" in model_names
            assert "FederationSyncJob" in model_names
            assert "SkillSyncSource" in model_names
            assert "SkillSyncJob" in model_names

            # Every model's collection is ensured after Beanie is initialized
            mock_ensure_collections.assert_awaited_once_with(mock_db, document_models)

    @pytest.mark.asyncio
    async def test_connect_db_uses_create_mongo_client(self):
        """connect_db delegates URL building to create_mongo_client and keeps its result."""
        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(return_value={"ok": 1})
        config = MongoConfig(mongo_uri="mongodb://localhost:27017/test_db")
        with (
            patch(
                "registry_pkgs.database.mongodb.create_mongo_client", return_value=(mock_client, "resolved")
            ) as mock_create,
            patch("registry_pkgs.database.mongodb.init_beanie", new_callable=AsyncMock),
        ):
            await MongoDB.connect_db(config, "explicit")

        mock_create.assert_called_once_with(config, "explicit")
        assert MongoDB.client is mock_client
        assert MongoDB.database_name == "resolved"
        mock_client.__getitem__.assert_called_with("resolved")

    @pytest.mark.asyncio
    async def test_connect_db_raises_without_db_name(self):
        with pytest.raises(ValueError, match="database name is required"):
            await MongoDB.connect_db(MongoConfig(mongo_uri="mongodb://localhost:27017"))

        assert MongoDB.client is None

    @pytest.mark.asyncio
    async def test_connect_db_only_once(self):
        """Test connect_db doesn't reconnect if client already exists."""
        with (
            patch("registry_pkgs.database.mongodb.AsyncMongoClient") as MockClient,
            patch("registry_pkgs.database.mongodb.init_beanie", new_callable=AsyncMock),
        ):
            mock_instance = MagicMock()
            mock_instance.admin = MagicMock()
            mock_instance.admin.command = AsyncMock(return_value={"ok": 1})
            mock_instance.__getitem__ = MagicMock(return_value=MagicMock())
            MockClient.return_value = mock_instance

            await MongoDB.connect_db(MongoConfig(mongo_uri="mongodb://localhost:27017/test_db"), "test_db")
            first_call_count = MockClient.call_count

            # Call again
            await MongoDB.connect_db(MongoConfig(mongo_uri="mongodb://localhost:27017/test_db"), "test_db")

            # Verify client wasn't recreated
            assert MockClient.call_count == first_call_count

    @pytest.mark.asyncio
    async def test_close_db(self):
        """Test close_db closes the client connection."""
        # Create a mock client
        mock_client = MagicMock()
        mock_client.close = AsyncMock()

        MongoDB.client = mock_client

        await MongoDB.close_db()

        # Verify close was called and client is None
        mock_client.close.assert_called_once()
        assert MongoDB.client is None

    @pytest.mark.asyncio
    async def test_close_db_when_not_connected(self):
        """Test close_db handles case when no connection exists."""
        # Should not raise exception
        await MongoDB.close_db()
        assert MongoDB.client is None

    def test_get_client_when_connected(self):
        """Test get_client returns client when connected."""
        mock_client = MagicMock()
        MongoDB.client = mock_client

        client = MongoDB.get_client()
        assert client is mock_client

    def test_get_client_when_not_connected(self):
        """Test get_client raises RuntimeError when not connected."""
        MongoDB.client = None

        with pytest.raises(RuntimeError, match="Database connection is not initialized"):
            MongoDB.get_client()

    def test_get_database_when_connected(self):
        """Test get_database returns database when connected."""
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)

        MongoDB.client = mock_client
        MongoDB.database_name = "test_db"

        db = MongoDB.get_database()
        assert db is mock_db
        mock_client.__getitem__.assert_called_once_with("test_db")

    def test_get_database_when_not_connected(self):
        """Test get_database raises RuntimeError when not connected."""
        MongoDB.client = None

        with pytest.raises(RuntimeError, match="Database connection is not initialized"):
            MongoDB.get_database()


class TestConvenienceFunctions:
    """Test convenience functions for FastAPI lifespan events."""

    @pytest.fixture(autouse=True)
    def reset_mongodb_state(self):
        """Reset MongoDB class state before each test."""
        MongoDB.client = None
        MongoDB.database_name = None
        yield
        MongoDB.client = None
        MongoDB.database_name = None

    @pytest.mark.asyncio
    async def test_init_mongodb(self):
        """Test init_mongodb convenience function."""
        with patch.object(MongoDB, "connect_db", new_callable=AsyncMock) as mock_connect:
            config = MongoConfig(mongo_uri="mongodb://localhost:27017/test_db")
            await init_mongodb(config, "test_db")
            mock_connect.assert_called_once_with(config, "test_db")

    @pytest.mark.asyncio
    async def test_close_mongodb(self):
        """Test close_mongodb convenience function."""
        with patch.object(MongoDB, "close_db", new_callable=AsyncMock) as mock_close:
            await close_mongodb()
            mock_close.assert_called_once()


def _model(collection_name: str) -> MagicMock:
    return MagicMock(get_collection_name=MagicMock(return_value=collection_name))


class TestEnsureCollections:
    """Test idempotent creation of Beanie model collections."""

    @pytest.mark.asyncio
    async def test_creates_only_missing_collections(self):
        database = MagicMock(
            list_collection_names=AsyncMock(return_value=["users"]),
            create_collection=AsyncMock(),
        )

        await ensure_collections(database, [_model("users"), _model("skills"), _model("skillfiles")])

        assert [call.args[0] for call in database.create_collection.await_args_list] == ["skills", "skillfiles"]
        assert all(call.kwargs == {"check_exists": False} for call in database.create_collection.await_args_list)

    @pytest.mark.asyncio
    async def test_does_nothing_when_all_collections_exist(self):
        database = MagicMock(
            list_collection_names=AsyncMock(return_value=["users", "skills"]),
            create_collection=AsyncMock(),
        )

        await ensure_collections(database, [_model("users"), _model("skills")])

        database.create_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_a_collection_shared_by_two_models_once(self):
        database = MagicMock(list_collection_names=AsyncMock(return_value=[]), create_collection=AsyncMock())

        await ensure_collections(database, [_model("skills"), _model("skills")])

        database.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tolerates_a_collection_created_concurrently(self):
        namespace_exists = OperationFailure("Collection already exists", code=48)
        database = MagicMock(
            list_collection_names=AsyncMock(return_value=[]),
            create_collection=AsyncMock(side_effect=[namespace_exists, None]),
        )

        await ensure_collections(database, [_model("skills"), _model("skillfiles")])

        assert database.create_collection.await_count == 2

    @pytest.mark.asyncio
    async def test_raises_other_create_failures(self):
        unauthorized = OperationFailure("not authorized", code=13)
        database = MagicMock(
            list_collection_names=AsyncMock(return_value=[]),
            create_collection=AsyncMock(side_effect=unauthorized),
        )

        with pytest.raises(OperationFailure, match="not authorized"):
            await ensure_collections(database, [_model("skills")])
