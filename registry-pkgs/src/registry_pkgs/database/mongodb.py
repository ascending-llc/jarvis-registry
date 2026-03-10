"""
MongoDB Connection Pool and Beanie ODM Initialization

This module provides MongoDB connection management with connection pooling
and Beanie ODM initialization for the MCP Gateway Registry.
"""

from urllib.parse import quote_plus

from beanie import init_beanie
from pymongo import AsyncMongoClient

from ..core.config import settings
from ..models._generated import (
    IAccessRole,
    IAction,
    IGroup,
    IUser,
    Key,
    Token,
)
from ..models.a2a_agent import A2AAgent
from ..models.extended_acl_entry import ExtendedAclEntry as IAclEntry
from ..models.extended_mcp_server import (
    ExtendedMCPServer as MCPServerDocument,
)


class MongoDB:
    """MongoDB connection manager with connection pooling."""

    client: AsyncMongoClient | None = None

    @classmethod
    async def connect_db(cls, db_name: str | None = None):
        """
        Initialize MongoDB connection with connection pooling.

        Args:
            db_name: Database name. If not provided, uses default or MONGODB_DB_NAME env var.
        """
        if cls.client is not None:
            return
        # Get MongoDB configuration from environment variables
        # URI format: mongodb://username:password@host:port/dbname?queryParams
        mongo_uri = settings.MONGO_URI
        mongo_username = settings.MONGODB_USERNAME
        mongo_password = settings.MONGODB_PASSWORD

        # Parse MONGO_URI to extract db_name and query params if present
        uri_parts = mongo_uri.rsplit("/", 1)
        base_uri = uri_parts[0]
        db_and_params = uri_parts[1] if len(uri_parts) > 1 else None

        # Split database name from query parameters
        query_params = ""
        extracted_db = None
        if db_and_params:
            if "?" in db_and_params:
                extracted_db, query_params = db_and_params.split("?", 1)
                query_params = "?" + query_params
            else:
                extracted_db = db_and_params

        if extracted_db and not db_name:
            db_name = extracted_db

        # Construct the final MongoDB URL
        if mongo_username and mongo_password:
            # Credentials provided via env vars - insert them into the URI
            escaped_username = quote_plus(mongo_username)
            escaped_password = quote_plus(mongo_password)
            protocol, rest = base_uri.split("://", 1)
            # Strip any existing credentials from rest (everything before @)
            if "@" in rest:
                rest = rest.split("@", 1)[1]
            mongodb_url = f"{protocol}://{escaped_username}:{escaped_password}@{rest}/{db_name}{query_params}"
        else:
            # Credentials already in URI or not needed
            mongodb_url = f"{base_uri}/{db_name}{query_params}" if db_name else base_uri

        cls.database_name = db_name
        try:
            # Create PyMongo async client with connection pool settings
            cls.client = AsyncMongoClient(
                mongodb_url,
                directConnection=True,
                maxPoolSize=50,  # Maximum number of connections in the pool
                minPoolSize=10,  # Minimum number of connections in the pool
                maxIdleTimeMS=30000,  # Close connections after 30 seconds of inactivity
                waitQueueTimeoutMS=5000,  # Wait up to 5 seconds for a connection from pool
                connectTimeoutMS=10000,  # Connection timeout
                serverSelectionTimeoutMS=10000,  # Server selection timeout
                retryWrites=True,  # Retry write operations
                retryReads=True,  # Retry read operations
            )
            # Verify connection
            await cls.client.admin.command("ping")
            # Get database
            db = cls.client[db_name]
            # Pass the namespace containing all model classes so forward references can be resolved
            rebuild_namespace = {
                "IUser": IUser,
                "IAccessRole": IAccessRole,
                "IGroup": IGroup,
                "IAclEntry": IAclEntry,
                "MCPServerDocument": MCPServerDocument,
                "Token": Token,
                "IAction": IAction,
                "Key": Key,
                "A2AAgent": A2AAgent,
            }
            MCPServerDocument.model_rebuild(_types_namespace=rebuild_namespace)
            Token.model_rebuild(_types_namespace=rebuild_namespace)
            IAclEntry.model_rebuild(_types_namespace=rebuild_namespace)
            IAction.model_rebuild(_types_namespace=rebuild_namespace)
            Key.model_rebuild(_types_namespace=rebuild_namespace)
            A2AAgent.model_rebuild(_types_namespace=rebuild_namespace)

            # Initialize Beanie with all document models
            await init_beanie(
                database=db,
                document_models=[
                    IUser,
                    MCPServerDocument,
                    IAccessRole,
                    IAclEntry,
                    IGroup,
                    Token,
                    IAction,
                    Key,
                    A2AAgent,
                ],
            )
        except Exception:
            raise

    @classmethod
    async def close_db(cls):
        """Close MongoDB connection and cleanup resources."""
        if cls.client is None:
            return

        try:
            await cls.client.close()
            cls.client = None
        except Exception:
            raise

    @classmethod
    def get_client(cls) -> AsyncMongoClient:
        """
        Get the MongoDB client instance.

        Returns:
            AsyncMongoClient: The Mongo client instance.
        Raises:
            RuntimeError: If the database connection is not initialized.
        """
        if cls.client is None:
            raise RuntimeError("Database connection is not initialized. Call MongoDB.connect_db() first.")
        return cls.client

    @classmethod
    def get_database(cls):
        """
        Get the MongoDB database instance.

        Returns:
            Database: The PyMongo async database instance.
        Raises:
            RuntimeError: If the database connection is not initialized.
        """
        if cls.client is None:
            raise RuntimeError("Database connection is not initialized. Call MongoDB.connect_db() first.")
        return cls.client[cls.database_name]


# Convenience functions for FastAPI lifespan events
async def init_mongodb(db_name: str | None = None):
    """
    Initialize MongoDB connection. To be called during FastAPI startup.

    Args:
        mongodb_url: MongoDB connection URL
        db_name: Database name
    """
    await MongoDB.connect_db(db_name)


async def close_mongodb():
    """
    Close MongoDB connection. To be called during FastAPI shutdown.
    """
    await MongoDB.close_db()
