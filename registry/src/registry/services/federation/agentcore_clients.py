import asyncio
import logging
from typing import Any

import boto3

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

    @staticmethod
    def _cache_key(region: str, assume_role_arn: str | None) -> tuple[str, str]:
        return region, assume_role_arn or ""

    async def get_control_client(self, region: str, assume_role_arn: str | None = None) -> Any:
        cache_key = self._cache_key(region, assume_role_arn)
        cached = self._control_clients.get(cache_key)
        if cached:
            return cached

        await self._initialize_context(region, assume_role_arn)
        return self._control_clients[cache_key]

    async def get_runtime_client(self, region: str, assume_role_arn: str | None = None) -> Any:
        cache_key = self._cache_key(region, assume_role_arn)
        cached = self._runtime_clients.get(cache_key)
        if cached:
            return cached

        await self._initialize_context(region, assume_role_arn)
        return self._runtime_clients[cache_key]

    async def get_runtime_credentials_provider(self, region: str, assume_role_arn: str | None = None):
        cache_key = self._cache_key(region, assume_role_arn)
        provider = self._credential_providers.get(cache_key)
        if provider:
            return provider

        await self._initialize_context(region, assume_role_arn)
        return self._credential_providers[cache_key]

    async def _initialize_context(self, region: str, assume_role_arn: str | None = None) -> None:
        cache_key = self._cache_key(region, assume_role_arn)
        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            if cache_key in self._control_clients and cache_key in self._runtime_clients:
                return
            await asyncio.to_thread(self._create_clients_for_context, region, assume_role_arn)

    def _create_clients_for_context(self, region: str, assume_role_arn: str | None = None) -> None:
        cache_key = self._cache_key(region, assume_role_arn)
        if cache_key in self._control_clients and cache_key in self._runtime_clients:
            return

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

        session = base_session
        if assume_role_arn:
            sts_client = base_session.client("sts")
            assumed_role = sts_client.assume_role(
                RoleArn=assume_role_arn,
                RoleSessionName=f"agentcore-federation-{region}",
            )
            credentials = assumed_role["Credentials"]
            session = boto3.Session(
                region_name=region,
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
            )
            logger.info("Initialized AgentCore AWS session via assume role")

        self._sessions[cache_key] = session
        self._control_clients[cache_key] = session.client("bedrock-agentcore-control", region_name=region)
        self._runtime_clients[cache_key] = session.client("bedrock-agentcore", region_name=region)
        self._credential_providers[cache_key] = session.get_credentials
