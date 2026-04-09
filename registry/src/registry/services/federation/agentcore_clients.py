import asyncio
import inspect
import logging
import threading
from collections.abc import Callable
from typing import Any

import boto3
from botocore.exceptions import ClientError

from registry.core.config import settings

logger = logging.getLogger(__name__)


class AgentCoreClientProvider:
    """
    Centralized factory/cache for AgentCore AWS clients.

    Keeps boto3 Session usage internal so callers only deal with clients and
    a credentials provider callback for SigV4 HTTP fallback.
    """

    def __init__(self):
        self._control_clients: dict[tuple[str, str], Any] = {}
        self._runtime_clients: dict[tuple[str, str], Any] = {}
        self._credential_providers: dict[tuple[str, str], Any] = {}
        self._sessions: dict[tuple[str, str], Any] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._sync_locks: dict[tuple[str, str], threading.Lock] = {}

    @staticmethod
    def _cache_key(region: str, assume_role_arn: str | None) -> tuple[str, str]:
        return region, assume_role_arn or ""

    @staticmethod
    def is_expired_token_error(exc: Exception) -> bool:
        if not isinstance(exc, ClientError):
            return False
        error_code = exc.response.get("Error", {}).get("Code")
        return error_code in {"ExpiredToken", "ExpiredTokenException", "RequestExpired"}

    @staticmethod
    def _should_cache(assume_role_arn: str | None) -> bool:
        # Role sessions are both temporary and request-specific. Reusing them across
        # calls risks mixing credentials from different assumeRoleArn values and
        # resurrecting expired STS tokens in long-lived API workers.
        return not assume_role_arn

    async def get_control_client(self, region: str, assume_role_arn: str | None = None) -> Any:
        cache_key = self._cache_key(region, assume_role_arn)
        cached = self._control_clients.get(cache_key)
        if cached and self._should_cache(assume_role_arn):
            return cached

        if self._should_cache(assume_role_arn):
            await self._initialize_context(region, assume_role_arn)
            return self._control_clients[cache_key]
        return await asyncio.to_thread(self._build_control_client, region, assume_role_arn)

    async def get_runtime_client(self, region: str, assume_role_arn: str | None = None) -> Any:
        cache_key = self._cache_key(region, assume_role_arn)
        cached = self._runtime_clients.get(cache_key)
        if cached and self._should_cache(assume_role_arn):
            return cached

        if self._should_cache(assume_role_arn):
            await self._initialize_context(region, assume_role_arn)
            return self._runtime_clients[cache_key]
        return await asyncio.to_thread(self._build_runtime_client, region, assume_role_arn)

    async def get_runtime_credentials_provider(self, region: str, assume_role_arn: str | None = None):
        cache_key = self._cache_key(region, assume_role_arn)
        provider = self._credential_providers.get(cache_key)
        if provider and self._should_cache(assume_role_arn):
            return provider

        if self._should_cache(assume_role_arn):
            await self._initialize_context(region, assume_role_arn)
            return self._credential_providers[cache_key]
        return lambda: self._create_session(region, assume_role_arn).get_credentials()

    async def _initialize_context(
        self,
        region: str,
        assume_role_arn: str | None = None,
    ) -> None:
        if not self._should_cache(assume_role_arn):
            return
        cache_key = self._cache_key(region, assume_role_arn)
        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            if cache_key in self._control_clients and cache_key in self._runtime_clients:
                return
            await asyncio.to_thread(self._create_cached_context, region, assume_role_arn)

    async def invalidate_context(self, region: str, assume_role_arn: str | None = None) -> None:
        cache_key = self._cache_key(region, assume_role_arn)
        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            self._control_clients.pop(cache_key, None)
            self._runtime_clients.pop(cache_key, None)
            self._credential_providers.pop(cache_key, None)
            self._sessions.pop(cache_key, None)
        logger.info(
            "Invalidated cached AgentCore AWS context for region=%s assume_role=%s", region, bool(assume_role_arn)
        )

    def _create_cached_context(self, region: str, assume_role_arn: str | None = None) -> None:
        cache_key = self._cache_key(region, assume_role_arn)
        sync_lock = self._sync_locks.setdefault(cache_key, threading.Lock())
        with sync_lock:
            if cache_key in self._control_clients and cache_key in self._runtime_clients:
                return

            session = self._create_session(region, assume_role_arn)
            self._sessions[cache_key] = session
            self._control_clients[cache_key] = session.client("bedrock-agentcore-control", region_name=region)
            self._runtime_clients[cache_key] = session.client("bedrock-agentcore", region_name=region)
            self._credential_providers[cache_key] = lambda cache_key=cache_key: self._sessions[
                cache_key
            ].get_credentials()

    def _create_session(self, region: str, assume_role_arn: str | None) -> Any:
        access_key = settings.aws_access_key_id
        secret_key = settings.aws_secret_access_key
        session_token = settings.aws_session_token

        if access_key and secret_key:
            base_session = boto3.Session(
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                aws_session_token=session_token,
            )
            logger.info("Initialized AgentCore AWS session with explicit access keys")
        else:
            base_session = boto3.Session(region_name=region)
            logger.info("Initialized AgentCore AWS session with default credential chain")

        if not assume_role_arn:
            return base_session

        # Always build a fresh assume-role session so each request uses the
        # specific role it asked for and never reuses stale STS credentials.
        sts_client = base_session.client("sts")
        assumed_role = sts_client.assume_role(
            RoleArn=assume_role_arn,
            RoleSessionName=f"agentcore-federation-{region}",
        )
        credentials = assumed_role["Credentials"]
        logger.info("Initialized AgentCore AWS session via assume role")
        return boto3.Session(
            region_name=region,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )

    def _build_control_client(self, region: str, assume_role_arn: str | None = None) -> Any:
        session = self._create_session(region, assume_role_arn)
        return session.client("bedrock-agentcore-control", region_name=region)

    def _build_runtime_client(self, region: str, assume_role_arn: str | None = None) -> Any:
        session = self._create_session(region, assume_role_arn)
        return session.client("bedrock-agentcore", region_name=region)

    async def execute_with_control_client(
        self,
        region: str,
        operation: Callable[[Any], Any],
        assume_role_arn: str | None = None,
    ) -> Any:
        return await self._execute_with_client("control", region, operation, assume_role_arn)

    async def execute_with_runtime_client(
        self,
        region: str,
        operation: Callable[[Any], Any],
        assume_role_arn: str | None = None,
    ) -> Any:
        return await self._execute_with_client("runtime", region, operation, assume_role_arn)

    async def _execute_with_client(
        self,
        client_kind: str,
        region: str,
        operation: Callable[[Any], Any],
        assume_role_arn: str | None = None,
    ) -> Any:
        client_getter = self.get_control_client if client_kind == "control" else self.get_runtime_client
        client = await client_getter(region, assume_role_arn)
        try:
            result = operation(client)
            if inspect.isawaitable(result):
                return await result
            return result
        except Exception as exc:
            if not self.is_expired_token_error(exc):
                raise
            logger.warning(
                "AgentCore %s client credentials expired for region %s; refreshing cached client and retrying once",
                client_kind,
                region,
            )
            await self.invalidate_context(region, assume_role_arn)
            refreshed_client = await client_getter(region, assume_role_arn)
            result = operation(refreshed_client)
            if inspect.isawaitable(result):
                return await result
            return result
