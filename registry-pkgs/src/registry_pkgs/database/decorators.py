"""
Decorators for managing database transactions.
"""

import functools
import logging
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import ConnectionFailure, OperationFailure

from registry_pkgs.database.mongodb import MongoDB

logger = logging.getLogger(__name__)

MONGODB_TRANSACTION_NOT_SUPPORTED_ERROR_CODE = 263

_tx_session: ContextVar[AsyncClientSession | None] = ContextVar("_tx_session", default=None)


def use_transaction(func: Callable) -> Callable:
    """Decorator to wrap a function in a MongoDB transaction.
    This decorator creates a new transaction scope and manages the session lifecycle.
    The session is stored in a ContextVar and can be accessed by nested functions
    using get_current_session().
    Usage:
        @router.post("/server/create")
        @use_transaction
        async def create_user_with_profile(user_data: dict, profile_data: dict):
            # This function runs in a transaction
            server = await server_service.create_server(server_data)
            author_acl = await acl_service.grant_permission(user_data)
            return server, author_acl
    Raises:
        RuntimeError: If a nested transaction is detected
        OperationFailure: If MongoDB transaction is not supported
        ConnectionFailure: If MongoDB connection fails
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        active_session = _tx_session.get()
        if active_session:
            raise RuntimeError(
                f"Nested transaction detected in {func.__name__}. "
                f"Remove @use_transaction decorator from this function or its callers. "
                f"Only apply @use_transaction to exactly one function/method involved in a request lifecycle."
            )

        client: AsyncMongoClient = MongoDB.get_client()
        logger.info("Starting transaction for %s", func.__name__)
        try:
            async with client.start_session() as session:
                async with await session.start_transaction():
                    context_token = _tx_session.set(session)
                    try:
                        result = await func(*args, **kwargs)
                        logger.info("Transaction committed successfully for %s", func.__name__)
                        return result
                    finally:
                        _tx_session.reset(context_token)
        except OperationFailure as exc:
            if exc.code == MONGODB_TRANSACTION_NOT_SUPPORTED_ERROR_CODE:
                logger.error(
                    "Transaction failed for %s - MongoDB must run as a replica set. "
                    "Start mongod with --replSet rs0 and run rs.initiate(). "
                    "See docker-compose.yml for a single-node replica set example. "
                    "Original error: %s",
                    func.__name__,
                    exc,
                )
            else:
                logger.error(
                    "Transaction failed for %s due to operation error: %s",
                    func.__name__,
                    exc,
                )
            raise
        except ConnectionFailure as exc:
            logger.error(
                "Transaction failed for %s due to connection error: %s",
                func.__name__,
                exc,
            )
            raise
        except Exception:
            logger.error(
                "Transaction failed for %s due to unexpected error",
                func.__name__,
                exc_info=True,
            )
            raise

    return wrapper


def get_current_session() -> AsyncClientSession | None:
    """Get the current MongoDB session from the active transaction context (if any).
    This function retrieves the session created by the @use_transaction decorator, if the current async context
    is in a MongoDB transaction. Otherwise it returns None.
    It should be called from methods that need to participate in the
    transaction by passing the session object to a CRUD method of a Beanie document class.
    Note that all CRUD methods a Beanie document class expect the session parameter to be `AsyncClientSession | None`.
    If None is passed, the operation is simply performed outside of a transaction.
    This is why this method simply returns None when there is no transaction in the current async context.
    Usage:
        async def create_user(user_data: dict):
            session = get_current_session()
            user = User(**user_data)
            await user.insert(session=session)  # <- User is a Beanie document class; .insert is a CRUD method.
            return user
    Returns:
        AsyncClientSession | None: The active MongoDB session if in transaction. Otherwise None.
    """
    return _tx_session.get()
