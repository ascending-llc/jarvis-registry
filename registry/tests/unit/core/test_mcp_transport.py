"""MethodRoutedTransport keeps GET streams and request/cleanup traffic on separate pools."""

import httpx2
import pytest

from registry.core.mcp_transport import MethodRoutedTransport, SharedTransport


class _Pool(httpx2.AsyncBaseTransport):
    def __init__(self, name: str) -> None:
        self.name = name
        self.methods: list[str] = []
        self.closed = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.methods.append(request.method)
        return httpx2.Response(200, text=self.name)

    async def aclose(self) -> None:
        self.closed += 1


@pytest.mark.parametrize(("method", "pool"), [("GET", "stream"), ("POST", "request"), ("DELETE", "request")])
async def test_routes_by_method(method: str, pool: str) -> None:
    request_pool, stream_pool = _Pool("request"), _Pool("stream")
    router = MethodRoutedTransport(request_pool=request_pool, stream_pool=stream_pool)

    async with httpx2.AsyncClient(transport=SharedTransport(router)) as client:
        response = await client.request(method, "http://downstream/mcp")

    assert response.text == pool
    assert request_pool.closed == stream_pool.closed == 0


async def test_aclose_closes_both_pools_even_if_one_fails() -> None:
    class _Failing(_Pool):
        async def aclose(self) -> None:
            await super().aclose()
            raise RuntimeError("close failed")

    request_pool, stream_pool = _Failing("request"), _Pool("stream")
    router = MethodRoutedTransport(request_pool=request_pool, stream_pool=stream_pool)

    with pytest.raises(RuntimeError):
        await router.aclose()

    assert request_pool.closed == stream_pool.closed == 1
