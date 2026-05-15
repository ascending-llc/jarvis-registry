from datetime import UTC, datetime, timedelta

from httpx import AsyncClient, Auth, Limits, Timeout

from ..utils.crypto_utils import generate_service_jwt


class AuthServerJwtAuth(Auth):
    def __init__(self, expires_in_seconds: int):
        super().__init__()

        self._ttl_seconds = expires_in_seconds
        self._leeway: int = 300

        self._sign_jwt()

    def _sign_jwt(self) -> None:
        self._jwt_expires_at = datetime.now(UTC) + timedelta(seconds=(self._ttl_seconds - self._leeway))

        self._jwt = generate_service_jwt(for_agentcore_runtime=True, expires_in_seconds=self._ttl_seconds)

    def _jwt_expired(self) -> bool:
        return datetime.now(UTC) > self._jwt_expires_at

    def auth_flow(self, request):
        if self._jwt_expired():
            self._sign_jwt()

        request.headers["Authorization"] = f"Bearer {self._jwt}"

        yield request


class A2AProxyClientRegistry:
    def __init__(self, jwt_expires_in_seconds: int = 3600):
        self._expires_in_seconds = jwt_expires_in_seconds

        self._dict: dict[str, AsyncClient] = {}

    def get(self, agent_slug: str, *, agentcore_jwt: bool = True) -> AsyncClient:
        if agent_slug in self._dict:
            return self._dict[agent_slug]

        auth: AuthServerJwtAuth | None = None
        if agentcore_jwt:
            auth = AuthServerJwtAuth(self._expires_in_seconds)

        client = AsyncClient(
            timeout=Timeout(30.0, read=60.0),
            follow_redirects=True,
            limits=Limits(max_connections=100, max_keepalive_connections=20),
            auth=auth,
        )

        self._dict[agent_slug] = client

        return client

    async def close(self) -> None:
        clients = list(self._dict.values())

        self._dict.clear()

        for client in clients:
            await client.aclose()
