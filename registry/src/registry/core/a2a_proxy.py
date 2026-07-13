from datetime import UTC, datetime, timedelta

from httpx import AsyncClient, Auth, Limits, Timeout

from registry_pkgs.core.agentcore_jwt import mint_agentcore_runtime_jwt
from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.federation import AgentCoreRuntimeJwtConfig

_ClientCacheConfig = tuple[bool, AgentCoreRuntimeJwtConfig | None]


class AuthServerJwtAuth(Auth):
    def __init__(
        self,
        *,
        jwt_signing_config: JwtSigningConfig,
        runtime_jwt_config: AgentCoreRuntimeJwtConfig | None,
        subject: str,
        expires_in_seconds: int,
    ):
        super().__init__()

        self._jwt_signing_config = jwt_signing_config
        self._runtime_jwt_config = runtime_jwt_config
        self._subject = subject
        self._ttl_seconds = expires_in_seconds
        self._leeway: int = 60  # Try to refresh JWT 1 min before expiration.

        self._sign_jwt()

    def _sign_jwt(self) -> None:
        self._jwt_expires_at = datetime.now(UTC) + timedelta(seconds=(self._ttl_seconds - self._leeway))

        self._jwt = mint_agentcore_runtime_jwt(
            self._runtime_jwt_config,
            subject=self._subject,
            signing=self._jwt_signing_config,
            expires_in_seconds=self._ttl_seconds,
        )

    def _jwt_expired(self) -> bool:
        return datetime.now(UTC) > self._jwt_expires_at

    def auth_flow(self, request):
        if self._jwt_expired():
            self._sign_jwt()

        request.headers["Authorization"] = f"Bearer {self._jwt}"

        yield request


class A2AProxyClientRegistry:
    def __init__(
        self,
        *,
        jwt_signing_config: JwtSigningConfig,
        jwt_subject: str,
        jwt_expires_in_seconds: int = 3600,
    ):
        self._jwt_signing_config = jwt_signing_config
        self._jwt_subject = jwt_subject
        self._expires_in_seconds = jwt_expires_in_seconds

        self._dict: dict[str, tuple[_ClientCacheConfig, AsyncClient]] = {}

    def _build_auth(
        self,
        *,
        agentcore_jwt: bool,
        runtime_jwt_config: AgentCoreRuntimeJwtConfig | None,
    ) -> AuthServerJwtAuth | None:
        if not agentcore_jwt:
            return None
        return AuthServerJwtAuth(
            jwt_signing_config=self._jwt_signing_config,
            runtime_jwt_config=runtime_jwt_config,
            subject=self._jwt_subject,
            expires_in_seconds=self._expires_in_seconds,
        )

    def get(
        self,
        agent_slug: str,
        *,
        agentcore_jwt: bool = False,
        runtime_jwt_config: AgentCoreRuntimeJwtConfig | None = None,
    ) -> AsyncClient:
        cache_config = (agentcore_jwt, runtime_jwt_config)
        cached = self._dict.get(agent_slug)
        if cached is not None:
            cached_config, cached_client = cached
            if cached_config == cache_config:
                return cached_client
            cached_client.auth = self._build_auth(  # type: ignore[assignment]
                agentcore_jwt=agentcore_jwt,
                runtime_jwt_config=runtime_jwt_config,
            )
            self._dict[agent_slug] = (cache_config, cached_client)
            return cached_client

        auth = self._build_auth(
            agentcore_jwt=agentcore_jwt,
            runtime_jwt_config=runtime_jwt_config,
        )

        client = AsyncClient(
            timeout=Timeout(30.0, read=60.0),
            follow_redirects=True,
            limits=Limits(max_connections=100, max_keepalive_connections=20),
            auth=auth,
        )

        self._dict[agent_slug] = (cache_config, client)

        return client

    async def close(self) -> None:
        clients = [client for _cache_config, client in self._dict.values()]

        self._dict.clear()

        for client in clients:
            await client.aclose()
