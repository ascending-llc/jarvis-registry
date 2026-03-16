import asyncio
import logging
from typing import Any

import boto3

from registry.constants import REGISTRY_CONSTANTS

logger = logging.getLogger(__name__)


class AgentCoreClientProvider:
    """
    Centralized factory/cache for AgentCore AWS clients.

    Keeps boto3 Session usage internal so callers only deal with clients and
    a credentials provider callback for SigV4 HTTP fallback.
    """

    def __init__(self, default_region: str):
        self.default_region = default_region
        self._control_clients: dict[str, Any] = {}
        self._runtime_clients: dict[str, Any] = {}
        self._credential_providers: dict[str, Any] = {}
        self._sessions: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_control_client(self, region: str | None = None) -> Any:
        region = region or self.default_region
        cached = self._control_clients.get(region)
        if cached:
            return cached

        await self._initialize_region(region)
        return self._control_clients[region]

    async def get_runtime_client(self, region: str | None = None) -> Any:
        region = region or self.default_region
        cached = self._runtime_clients.get(region)
        if cached:
            return cached

        await self._initialize_region(region)
        return self._runtime_clients[region]

    async def get_runtime_credentials_provider(self, region: str | None = None):
        region = region or self.default_region
        provider = self._credential_providers.get(region)
        if provider:
            return provider

        await self._initialize_region(region)
        return self._credential_providers[region]

    async def _initialize_region(self, region: str) -> None:
        lock = self._locks.setdefault(region, asyncio.Lock())
        async with lock:
            if region in self._control_clients and region in self._runtime_clients:
                return
            await asyncio.to_thread(self._create_clients_for_region, region)

    def _create_clients_for_region(self, region: str) -> None:
        if region in self._control_clients and region in self._runtime_clients:
            return

        access_key = REGISTRY_CONSTANTS.AWS_ACCESS_KEY_ID
        secret_key = REGISTRY_CONSTANTS.AWS_SECRET_ACCESS_KEY
        session_token = REGISTRY_CONSTANTS.AWS_SESSION_TOKEN
        assume_role_arn = REGISTRY_CONSTANTS.AGENTCORE_ASSUME_ROLE_ARN

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

        self._sessions[region] = session
        self._control_clients[region] = session.client("bedrock-agentcore-control", region_name=region)
        self._runtime_clients[region] = session.client("bedrock-agentcore", region_name=region)
        self._credential_providers[region] = session.get_credentials
