import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from time import time

import httpx
from fastapi import WebSocket

from ..constants import HealthStatus
from ..core.config import settings
from ..utils.utils import normalize_headers

logger = logging.getLogger(__name__)


class HighPerformanceWebSocketManager:
    """High-performance WebSocket manager for 400-1000+ concurrent connections."""

    def __init__(self):
        self.connections: set[WebSocket] = set()
        self.connection_metadata: dict[WebSocket, dict] = {}

        # Rate limiting and batching
        self.pending_updates: dict[str, dict] = {}  # service_path -> latest_data
        self.last_broadcast_time = 0
        self.min_broadcast_interval = settings.websocket_broadcast_interval_ms / 1000.0
        self.max_batch_size = settings.websocket_max_batch_size

        # Connection health tracking
        self.failed_connections: set[WebSocket] = set()
        self.cleanup_task: asyncio.Task | None = None

        # Performance metrics
        self.broadcast_count = 0
        self.failed_send_count = 0

    async def add_connection(self, websocket: WebSocket) -> bool:
        """Add a new WebSocket connection with connection limits."""
        try:
            # Connection limit for memory management
            if len(self.connections) >= settings.max_websocket_connections:
                logger.warning(f"Connection limit reached: {len(self.connections)}")
                await websocket.close(code=1008, reason="Server at capacity")
                return False

            await websocket.accept()
            self.connections.add(websocket)
            self.connection_metadata[websocket] = {
                "connected_at": time(),
                "last_ping": time(),
                "client_ip": getattr(websocket.client, "host", "unknown") if websocket.client else "unknown",
            }

            logger.debug(f"WebSocket connected: {len(self.connections)} total connections")

            # Send initial status efficiently
            await self._send_initial_status_optimized(websocket)
            return True

        except Exception as e:
            logger.error(f"Error adding WebSocket connection: {e}")
            return False

    async def remove_connection(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        self.connections.discard(websocket)
        self.connection_metadata.pop(websocket, None)
        self.failed_connections.discard(websocket)

        logger.debug(f"WebSocket disconnected: {len(self.connections)} total connections")

    async def _send_initial_status_optimized(self, websocket: WebSocket):
        """Send initial status using cached data to avoid blocking."""
        try:
            # Use cached health data to avoid blocking on service calls
            cached_data = health_service._get_cached_health_data()
            if cached_data:
                await websocket.send_text(json.dumps(cached_data))
        except Exception as e:
            logger.warning(f"Failed to send initial status: {e}")
            await self.remove_connection(websocket)

    async def broadcast_update(self, service_path: str | None = None, health_data: dict | None = None):
        """High-performance broadcasting with batching and rate limiting."""
        if not self.connections:
            return

        current_time = time()

        # Rate limiting: prevent too frequent broadcasts
        if current_time - self.last_broadcast_time < self.min_broadcast_interval:
            # Queue the update for later batch processing
            if service_path and health_data:
                self.pending_updates[service_path] = health_data
            return

        # Prepare broadcast data
        if service_path and health_data:
            # Single service update
            broadcast_data = {service_path: health_data}
        else:
            # Batch updates or full status
            if self.pending_updates:
                # Send pending updates in batches
                batch_data = dict(list(self.pending_updates.items())[: self.max_batch_size])
                broadcast_data = batch_data
                # Remove sent items from pending
                for key in batch_data:
                    self.pending_updates.pop(key, None)
            else:
                # Full status update (avoid this when possible)
                broadcast_data = health_service._get_cached_health_data()

        if broadcast_data:
            await self._send_to_connections_optimized(broadcast_data)
            self.last_broadcast_time = current_time

    async def _send_to_connections_optimized(self, data: dict):
        """Optimized concurrent sending with automatic cleanup."""
        if not self.connections:
            return

        message = json.dumps(data)
        connections_list = list(self.connections)  # Snapshot for safe iteration

        # Split into chunks for better memory management with many connections
        chunk_size = 100  # Process 100 connections at a time

        for i in range(0, len(connections_list), chunk_size):
            chunk = connections_list[i : i + chunk_size]

            # Send to chunk concurrently
            tasks = [self._safe_send_message(conn, message) for conn in chunk]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Track failed connections
            for conn, result in zip(chunk, results, strict=False):
                if isinstance(result, Exception):
                    self.failed_connections.add(conn)
                    self.failed_send_count += 1

        # Cleanup failed connections in batch (non-blocking)
        if self.failed_connections:
            asyncio.create_task(self._cleanup_failed_connections())

        self.broadcast_count += 1

    async def _safe_send_message(self, connection: WebSocket, message: str):
        """Send message with timeout and error handling."""
        try:
            # Use timeout to prevent hanging on slow connections
            await asyncio.wait_for(connection.send_text(message), timeout=settings.websocket_send_timeout_seconds)
            return True
        except TimeoutError:
            return TimeoutError("Send timeout")
        except Exception as e:
            return e

    async def _cleanup_failed_connections(self):
        """Cleanup failed connections without blocking main operations."""
        failed_count = len(self.failed_connections)
        if failed_count == 0:
            return

        for conn in list(self.failed_connections):
            await self.remove_connection(conn)

        logger.info(f"Cleaned up {failed_count} failed WebSocket connections")

    def get_stats(self) -> dict:
        """Get performance statistics."""
        return {
            "active_connections": len(self.connections),
            "pending_updates": len(self.pending_updates),
            "total_broadcasts": self.broadcast_count,
            "failed_sends": self.failed_send_count,
            "failed_connections": len(self.failed_connections),
        }


class HealthMonitoringService:
    """Optimized health monitoring service for high-scale WebSocket operations."""

    def __init__(self):
        self.server_health_status: dict[str, str] = {}
        self.server_last_check_time: dict[str, datetime] = {}

        # High-performance WebSocket manager
        self.websocket_manager = HighPerformanceWebSocketManager()

        # Background task management
        self.health_check_task: asyncio.Task | None = None

        # Performance optimizations
        self._cached_health_data: dict = {}
        self._cache_timestamp = 0
        self._cache_ttl = settings.websocket_cache_ttl_seconds

    async def initialize(self):
        """Initialize the health monitoring service."""
        logger.info("Initializing health monitoring service...")

        # DEPRECATED: Background health checks disabled in favor of MongoDB-based monitoring
        # TODO: Refactor to support WebSocket updates from MongoDB change streams
        # Start background health checks
        # self.health_check_task = asyncio.create_task(self._run_health_checks())
        logger.info("Background health checks DISABLED - using MongoDB for health monitoring")

        logger.info("Health monitoring service initialized!")

    async def shutdown(self):
        """Shutdown the health monitoring service."""
        # Cancel background tasks
        if self.health_check_task:
            self.health_check_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.health_check_task

        # Close all WebSocket connections
        connections = list(self.websocket_manager.connections)
        close_tasks = []
        for conn in connections:
            with contextlib.suppress(Exception):
                close_tasks.append(conn.close())

        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

        logger.info("Health monitoring service shutdown complete")

    async def add_websocket_connection(self, websocket: WebSocket):
        """Add a new WebSocket connection and send initial health status."""
        success = await self.websocket_manager.add_connection(websocket)
        if success:
            logger.info(f"WebSocket client connected: {websocket.client}")
        return success

    async def remove_websocket_connection(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        await self.websocket_manager.remove_connection(websocket)
        logger.info(f"WebSocket connection removed: {websocket.client}")

    async def _send_initial_status(self, websocket: WebSocket):
        """Send initial health status to a newly connected WebSocket client."""
        # This method is kept for compatibility but delegates to the optimized manager
        await self.websocket_manager._send_initial_status_optimized(websocket)

    async def broadcast_health_update(self, service_path: str | None = None):
        """Broadcast health status updates to all connected WebSocket clients."""
        if not self.websocket_manager.connections:
            return

        from ..services.server_service import server_service_v1 as server_service

        if service_path:
            # Single service update - get data efficiently
            server_info = server_service.get_server_info(service_path)
            if server_info:
                health_data = self._get_service_health_data_fast(service_path, server_info)
                await self.websocket_manager.broadcast_update(service_path, health_data)
        else:
            # Full update - use cached data
            await self.websocket_manager.broadcast_update()

    def _get_cached_health_data(self) -> dict:
        """Get cached health data to avoid expensive operations during WebSocket sends."""
        current_time = time()

        # Return cached data if still valid
        if (current_time - self._cache_timestamp) < self._cache_ttl and self._cached_health_data:
            return self._cached_health_data

        # Rebuild cache
        from ..services.server_service import server_service_v1 as server_service

        all_servers = server_service.get_all_servers()

        data = {}
        for path, server_info in all_servers.items():
            data[path] = self._get_service_health_data_fast(path, server_info)

        self._cached_health_data = data
        self._cache_timestamp = current_time
        return data

    def get_websocket_stats(self) -> dict:
        """Get WebSocket performance statistics."""
        return self.websocket_manager.get_stats()

    async def _run_health_checks(self):
        """Background task to run periodic health checks."""
        logger.info("Starting periodic health checks...")

        while True:
            try:
                await self._perform_health_checks()
                await asyncio.sleep(settings.health_check_interval_seconds)
            except asyncio.CancelledError:
                logger.info("Health check task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in health check loop: {e}", exc_info=True)
                await asyncio.sleep(60)  # Wait a minute before retrying

    async def _perform_health_checks(self):
        """Perform health checks on all enabled services."""
        import httpx

        from ..services.server_service import server_service_v1 as server_service

        enabled_services = server_service.get_enabled_services()
        if not enabled_services:
            return

        # Only log if there are many services to avoid spam
        if len(enabled_services) > 1:
            logger.debug(f"Performing health checks on {len(enabled_services)} enabled services")

        # Track if any status changed to minimize broadcasts
        status_changed = False

        # Perform actual health checks concurrently for better performance
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.health_check_timeout_seconds)) as client:
            # Batch process enabled services
            check_tasks = []
            for service_path in enabled_services:
                server_info = server_service.get_server_info(service_path)
                if server_info and server_info.get("proxy_pass_url"):
                    check_tasks.append(self._check_single_service(client, service_path, server_info))

            # Execute all health checks concurrently
            if check_tasks:
                results = await asyncio.gather(*check_tasks, return_exceptions=True)

                # Check if any status changed
                for result in results:
                    if isinstance(result, bool) and result:  # True indicates status changed
                        status_changed = True
                        break

        # Only broadcast if something actually changed
        if status_changed:
            await self.broadcast_health_update()

    async def _check_single_service(self, client: httpx.AsyncClient, service_path: str, server_info: dict) -> bool:
        """Check a single service and return True if status changed."""

        proxy_pass_url = server_info.get("proxy_pass_url")
        previous_status = self.server_health_status.get(service_path, HealthStatus.UNKNOWN)
        new_status = previous_status

        try:
            # Try to reach the service endpoint using transport-aware checking
            is_healthy, status_detail = await self._check_server_endpoint_transport_aware(
                client, proxy_pass_url, server_info
            )

            if is_healthy:
                new_status = status_detail  # Could be "healthy" or "healthy-auth-expired"

                # Fetch tools in these cases:
                # 1. First health check (previous_status == UNKNOWN)
                # 2. Service transitioned to healthy from unhealthy
                # 3. Service is healthy but has no tools yet (tool_list is empty)
                # Only do this for fully healthy status, not auth-expired
                should_fetch_tools = False
                if status_detail == HealthStatus.HEALTHY:
                    if previous_status == HealthStatus.UNKNOWN:
                        # First health check - always fetch tools
                        should_fetch_tools = True
                        logger.info(f"First health check for {service_path} - will fetch tools")
                    elif previous_status != HealthStatus.HEALTHY:
                        # Transitioned to healthy - fetch tools
                        should_fetch_tools = True
                        logger.info(f"Service {service_path} transitioned to healthy - will fetch tools")
                    else:
                        # Already healthy - only fetch if we don't have tools
                        current_tool_list = server_info.get("tool_list", [])
                        if not current_tool_list:
                            should_fetch_tools = True
                            logger.info(f"Service {service_path} is healthy but has no tools - will fetch tools")

                if should_fetch_tools:
                    asyncio.create_task(self._update_tools_background(service_path, proxy_pass_url))
            else:
                new_status = status_detail  # Detailed error message from transport check

        except httpx.TimeoutException:
            new_status = HealthStatus.UNHEALTHY_TIMEOUT
        except httpx.ConnectError:
            new_status = HealthStatus.UNHEALTHY_CONNECTION_ERROR
        except Exception as e:
            new_status = f"error: {type(e).__name__}"

        # Update status and timestamp
        self.server_health_status[service_path] = new_status
        self.server_last_check_time[service_path] = datetime.now(UTC)

        # Return True if status changed
        return previous_status != new_status

    def _build_headers_for_server(self, server_info: dict, include_session_id: bool = False) -> dict[str, str]:
        """
        Build HTTP headers for server requests by merging default headers with server-specific headers.

        Args:
            server_info: Server configuration dictionary
            include_session_id: Whether to generate and include Mcp-Session-Id header

        Returns:
            Merged headers dictionary
        """
        import uuid

        # Start with default headers for MCP endpoints
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

        # Add session ID if requested (required by some MCP servers like Cloudflare)
        if include_session_id:
            session_id = str(uuid.uuid4())
            headers["Mcp-Session-Id"] = session_id
            logger.debug(f"Generated Mcp-Session-Id: {session_id}")

        # Merge server-specific headers if present
        server_headers = normalize_headers(server_info.get("headers"))
        if server_headers:
            headers.update(server_headers)
            logger.debug(f"Added server headers: {server_headers}")
        return headers

    async def _initialize_mcp_session(
        self, client: httpx.AsyncClient, endpoint: str, headers: dict[str, str]
    ) -> str | None:
        """
        Initialize an MCP session and retrieve the session ID from the server.

        Args:
            client: httpx AsyncClient instance
            endpoint: The MCP endpoint URL
            headers: Headers to send with the request

        Returns:
            Session ID string if successful, None otherwise
        """
        import uuid

        try:
            # Send initialize request without session ID
            # The server will generate and return a session ID in the response header
            init_headers = headers.copy()

            initialize_payload = {
                "jsonrpc": "2.0",
                "id": "0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-gateway-registry", "version": "1.0.0"},
                },
            }

            response = await client.post(
                endpoint,
                headers=init_headers,
                json=initialize_payload,
                timeout=httpx.Timeout(5.0),
                follow_redirects=True,
            )

            # Check if initialize succeeded
            if response.status_code not in [200, 201]:
                logger.warning(
                    f"MCP initialize failed for {endpoint}: "
                    f"Status {response.status_code}, Response: {response.text[:200]}"
                )
                return None

            # Get session ID from response headers (server-generated)
            server_session_id = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
            if server_session_id:
                logger.debug(f"Server returned session ID: {server_session_id}")
                return server_session_id
            else:
                # If server doesn't return a session ID, generate one for stateless servers
                client_session_id = str(uuid.uuid4())
                logger.debug(f"Server did not return session ID, using client-generated: {client_session_id}")
                return client_session_id

        except Exception as e:
            logger.warning(f"MCP initialize failed for {endpoint}: {e}")
            return None

    async def _try_ping_without_auth(self, client: httpx.AsyncClient, endpoint: str) -> bool:
        """
        Try a simple ping without authentication headers.
        Used as fallback when auth fails to determine if server is reachable.

        Args:
            client: httpx AsyncClient instance
            endpoint: The MCP endpoint URL to ping

        Returns:
            bool: True if server responds (indicating it's reachable but auth expired)
        """
        import uuid

        try:
            # Minimal headers without auth but with session ID (required by some servers)
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Mcp-Session-Id": str(uuid.uuid4()),
            }
            ping_payload = '{ "jsonrpc": "2.0", "id": "0", "method": "ping" }'

            response = await client.post(
                endpoint, headers=headers, content=ping_payload, timeout=httpx.Timeout(5.0), follow_redirects=True
            )

            # Check if we get any valid response (even auth errors indicate server is up)
            if response.status_code in [200, 400, 401, 403]:
                logger.info(
                    f"Ping without auth succeeded for {endpoint} - server is reachable but auth may have expired"
                )
                return True
            else:
                logger.warning(f"Ping without auth failed for {endpoint}: Status {response.status_code}")
                return False

        except Exception as e:
            logger.warning(f"Ping without auth failed for {endpoint}: {type(e).__name__} - {e}")
            return False

    async def _check_server_endpoint_transport_aware(
        self, client: httpx.AsyncClient, proxy_pass_url: str, server_info: dict
    ) -> tuple[bool, str]:
        """Check server endpoint using transport-aware logic.

        Returns:
            tuple[bool, str]: (is_healthy, status_detail)
        """
        if not proxy_pass_url:
            return False, HealthStatus.UNHEALTHY_MISSING_PROXY_URL

        # Get transport information from server_info
        supported_transports = server_info.get("supported_transports", ["streamable-http"])

        # If URL already has transport endpoint, use it directly
        # BUT skip this shortcut for streamable-http to ensure proper POST ping is used
        has_transport_in_url = (
            proxy_pass_url.endswith("/mcp")
            or proxy_pass_url.endswith("/sse")
            or "/mcp/" in proxy_pass_url
            or "/sse/" in proxy_pass_url
        )

        if has_transport_in_url and "streamable-http" not in supported_transports:
            logger.info(f"[TRACE] Found transport endpoint in URL: {proxy_pass_url}")
            logger.info(
                f"[TRACE] URL contains /mcp: {'/mcp' in proxy_pass_url}, URL contains /sse: {'/sse' in proxy_pass_url}"
            )
            try:
                # Build headers including server-specific headers
                headers = self._build_headers_for_server(server_info)
                # For SSE endpoints, use a shorter timeout since they start streaming immediately
                if proxy_pass_url.endswith("/sse") or "/sse/" in proxy_pass_url:
                    logger.info("[TRACE] Detected SSE endpoint in URL, using SSE-specific handling")
                    timeout = httpx.Timeout(connect=5.0, read=2.0, write=5.0, pool=5.0)
                    try:
                        response = await client.get(
                            proxy_pass_url, headers=headers, follow_redirects=True, timeout=timeout
                        )
                        return self._is_mcp_endpoint_healthy(response)
                    except (TimeoutError, httpx.TimeoutException) as e:
                        # For SSE endpoints, timeout while reading streaming response is normal after getting 200 OK
                        logger.debug(f"SSE endpoint {proxy_pass_url} timed out while streaming (expected): {e}")
                        # If we can extract status code from response, check if it was 200
                        if hasattr(e, "response") and e.response and e.response.status_code == 200:
                            logger.debug(
                                f"SSE endpoint {proxy_pass_url} returned 200 OK before timeout - considering healthy"
                            )
                            return True, HealthStatus.HEALTHY
                        # For SSE, timeout after initial connection usually means server is responding
                        return True, HealthStatus.HEALTHY
                    except Exception as e:
                        logger.warning(f"SSE endpoint {proxy_pass_url} failed with exception: {type(e).__name__} - {e}")
                        return False, f"unhealthy: {type(e).__name__}"
                else:
                    logger.info("[TRACE] Detected MCP endpoint in URL, using standard HTTP handling")
                    response = await client.get(proxy_pass_url, headers=headers, follow_redirects=True)

                    # Check for auth failures first
                    if response.status_code in [401, 403]:
                        logger.info(
                            f"[TRACE] Auth failure detected ({response.status_code}) for {proxy_pass_url}, trying ping without auth"
                        )
                        if await self._try_ping_without_auth(client, proxy_pass_url):
                            return True, HealthStatus.HEALTHY
                        else:
                            return False, "unhealthy: auth failed and ping without auth failed"

                    if self._is_mcp_endpoint_healthy(response):
                        return True, HealthStatus.HEALTHY
                    else:
                        return False, f"unhealthy: status {response.status_code}"
            except Exception as e:
                logger.warning(f"Health check failed for {proxy_pass_url}: {type(e).__name__} - {e}")
                return False, f"unhealthy: {type(e).__name__}"

        # Skip health checks for stdio transport (as requested)
        if supported_transports == ["stdio"]:
            logger.info(f"[TRACE] Skipping health check for stdio transport: {proxy_pass_url}")
            return True, HealthStatus.UNKNOWN

        # Try endpoints based on supported transports, prioritizing streamable-http
        logger.info(f"[TRACE] No transport endpoint in URL: {proxy_pass_url}")
        logger.info(f"[TRACE] Supported transports: {supported_transports}")
        base_url = proxy_pass_url.rstrip("/")

        # Try streamable-http first (default preference)
        if "streamable-http" in supported_transports:
            logger.info("[TRACE] Trying streamable-http transport")
            # Build base headers without session ID
            headers = self._build_headers_for_server(server_info, include_session_id=False)

            # Only try /mcp endpoint for streamable-http transport
            # Don't append /mcp if URL already has a transport endpoint (/mcp or /sse)
            if base_url.endswith("/mcp") or base_url.endswith("/sse") or "/mcp/" in base_url or "/sse/" in base_url:
                endpoint = f"{base_url}"
            else:
                endpoint = f"{base_url}/mcp"

            try:
                # Step 1: Initialize session to get session ID
                logger.info(f"[TRACE] Initializing MCP session for endpoint: {endpoint}")
                session_id = await self._initialize_mcp_session(client, endpoint, headers)

                # If initialize failed, check if it was due to auth (401/403)
                # Try ping without auth before giving up
                if not session_id:
                    logger.warning(f"Failed to initialize MCP session for {endpoint}, trying ping without auth")
                    if await self._try_ping_without_auth(client, endpoint):
                        return True, HealthStatus.HEALTHY
                    else:
                        return False, "unhealthy: session initialization failed and ping without auth failed"

                # Step 2: Add session ID to headers for ping
                headers["Mcp-Session-Id"] = session_id
                ping_payload = '{ "jsonrpc": "2.0", "id": "0", "method": "ping" }'

                logger.info(f"[TRACE] Sending ping to endpoint: {endpoint}")
                logger.info(f"[TRACE] Headers being sent: {headers}")
                response = await client.post(endpoint, headers=headers, content=ping_payload, follow_redirects=True)
                logger.info(f"[TRACE] Response status: {response.status_code}")

                # Check for auth failures first
                if response.status_code in [401, 403]:
                    logger.info(
                        f"[TRACE] Auth failure detected ({response.status_code}) for {endpoint}, trying ping without auth"
                    )
                    if await self._try_ping_without_auth(client, endpoint):
                        # ============================================================================
                        # TEMPORARY WORKAROUND - TODO: REVERT AFTER CREDENTIALS MANAGER IS IMPLEMENTED
                        # ============================================================================
                        # Issue: https://github.com/agentic-community/mcp-gateway-registry/issues/167
                        #
                        # Temporarily marking servers with auth failures as "healthy" instead of
                        # "healthy-auth-expired" to avoid confusing users when servers are registered
                        # with auth requirements but no credentials manager is in place yet.
                        #
                        # This allows servers like customer-support-assistant (Bedrock AgentCore) to
                        # show as healthy when they respond to ping, even though live tool fetching
                        # requires authentication.
                        #
                        # BEFORE CREDENTIALS MANAGER: Return healthy (current behavior)
                        # AFTER CREDENTIALS MANAGER:  Return healthy-auth-expired (proper behavior)
                        #
                        # When the credentials manager container is implemented (see design doc at
                        # .scratchpad/credentials-manager-design.md), this should be changed back to:
                        #   return True, HealthStatus.HEALTHY_AUTH_EXPIRED
                        # ============================================================================
                        return True, HealthStatus.HEALTHY  # TODO: Change back to HEALTHY_AUTH_EXPIRED
                    else:
                        return False, "unhealthy: auth failed and ping without auth failed"

                # Check normal health status
                if self._is_mcp_endpoint_healthy_streamable(response):
                    logger.info(f"Health check succeeded at {endpoint}")
                    return True, HealthStatus.HEALTHY
                else:
                    logger.warning(
                        f"Health check failed for {endpoint}: Status {response.status_code}, Response: {response.text}"
                    )
                    return False, f"unhealthy: status {response.status_code}"

            except Exception as e:
                logger.warning(f"Health check failed for {endpoint}: {type(e).__name__} - {e}")
                return False, f"unhealthy: {type(e).__name__}"

        # Fallback to SSE
        if "sse" in supported_transports:
            logger.info("[TRACE] Trying SSE transport")
            try:
                if base_url.endswith("/sse"):
                    sse_endpoint = f"{base_url}"
                else:
                    sse_endpoint = f"{base_url}/sse"
                # Build headers including server-specific headers
                headers = self._build_headers_for_server(server_info)
                # Use shorter timeout for SSE since it starts streaming immediately
                timeout = httpx.Timeout(connect=5.0, read=2.0, write=5.0, pool=5.0)
                response = await client.get(sse_endpoint, headers=headers, follow_redirects=True, timeout=timeout)
                if self._is_mcp_endpoint_healthy(response):
                    return True, HealthStatus.HEALTHY
            except (TimeoutError, httpx.TimeoutException) as e:
                # For SSE endpoints, timeout while reading streaming response is normal after getting 200 OK
                logger.info(f"SSE endpoint {sse_endpoint} timed out while streaming (expected): {e}")
                # If we can extract status code from response, check if it was 200
                if hasattr(e, "response") and e.response and e.response.status_code == 200:
                    logger.info(f"SSE endpoint {sse_endpoint} returned 200 OK before timeout - considering healthy")
                    return True, HealthStatus.HEALTHY
                # For SSE, timeout after initial connection usually means server is responding
                return True, "healthy"
            except Exception as e:
                logger.error(f"SSE endpoint {sse_endpoint} failed with exception: {type(e).__name__} - {e}")
                pass

        # If no specific transports, try default streamable-http then sse
        if not supported_transports or supported_transports == []:
            logger.info("[TRACE] No specific transports defined, trying defaults")
            headers = self._build_headers_for_server(server_info)

            # Only try /mcp endpoint for default streamable-http transport
            endpoint = f"{base_url}/mcp"
            ping_payload = '{ "jsonrpc": "2.0", "id": "0", "method": "ping" }'

            try:
                logger.info(f"[TRACE] Trying default endpoint: {endpoint}")
                logger.info(f"[TRACE] Headers being sent: {headers}")
                response = await client.post(endpoint, headers=headers, content=ping_payload, follow_redirects=True)
                logger.info(f"[TRACE] Response status: {response.status_code}")
                if self._is_mcp_endpoint_healthy_streamable(response):
                    logger.info(f"Health check succeeded at {endpoint}")
                    return True, HealthStatus.HEALTHY
                else:
                    logger.warning(
                        f"Health check failed for {endpoint}: Status {response.status_code}, Response: {response.text}"
                    )
                    return False, f"unhealthy: status {response.status_code}"
            except Exception as e:
                logger.warning(f"Health check failed for {endpoint}: {type(e).__name__} - {e}")

            try:
                sse_endpoint = f"{base_url}/sse"
                # Build headers including server-specific headers
                headers = self._build_headers_for_server(server_info)
                # Use shorter timeout for SSE since it starts streaming immediately
                timeout = httpx.Timeout(connect=5.0, read=2.0, write=5.0, pool=5.0)
                response = await client.get(sse_endpoint, headers=headers, follow_redirects=True, timeout=timeout)
                if self._is_mcp_endpoint_healthy(response):
                    return True, HealthStatus.HEALTHY
            except (TimeoutError, httpx.TimeoutException) as e:
                # For SSE endpoints, timeout while reading streaming response is normal after getting 200 OK
                logger.info(f"SSE endpoint {sse_endpoint} timed out while streaming (expected): {e}")
                # If we can extract status code from response, check if it was 200
                if hasattr(e, "response") and e.response and e.response.status_code == 200:
                    logger.info(f"SSE endpoint {sse_endpoint} returned 200 OK before timeout - considering healthy")
                    return True, HealthStatus.HEALTHY
                # For SSE, timeout after initial connection usually means server is responding
                return True, "healthy"
            except Exception as e:
                logger.error(f"SSE endpoint {sse_endpoint} failed with exception: {type(e).__name__} - {e}")
                pass

        return False, "unhealthy: all transport checks failed"

    def _is_mcp_endpoint_healthy_streamable(self, response) -> bool:
        """
        Determine if a streamable-http MCP endpoint is healthy based on HTTP response.

        For streamable-http MCP endpoints, we consider them healthy if:
        1. HTTP 200 OK - Normal successful response
        2. HTTP 400 Bad Request with JSON-RPC error code -32600

        Args:
            response: httpx.Response object from the health check request

        Returns:
            bool: True if the endpoint is considered healthy, False otherwise
        """
        # HTTP 200 is always healthy
        if response.status_code == 200:
            return True

        # HTTP 400 is healthy only if it has JSON-RPC error code -32600
        if response.status_code == 400:
            try:
                # Parse the JSON response
                response_data = response.json()

                # Check for error dictionary with code -32600 (standard MCP error)
                if isinstance(response_data.get("error"), dict):
                    error = response_data["error"]
                    if isinstance(error.get("code"), int) and error.get("code") == -32600:
                        return True

                # Check for streamable-http no auth specific query parameter error
                if isinstance(response_data.get("error"), str):
                    error_msg = response_data["error"]
                    if "Missing required query parameter: strata_id or instance_id" in error_msg:
                        return True

            except (ValueError, KeyError, TypeError):
                # If we can't parse JSON or the structure is wrong, treat as unhealthy
                pass

        # All other status codes are considered unhealthy
        return False

    def _is_mcp_endpoint_healthy(self, response) -> bool:
        """
        Determine if an MCP endpoint is healthy based on HTTP response.

        For MCP endpoints, we consider them healthy if:
        1. HTTP 200 OK - Normal successful response
        2. HTTP 400 Bad Request with specific JSON-RPC error indicating missing session ID

        The 400 status with "Missing session ID" error is considered healthy because:
        - It proves the MCP endpoint is reachable and functioning
        - The server is properly validating requests according to MCP protocol
        - It's rejecting our basic GET request because we're not providing a session ID
        - This is expected behavior for a working MCP server when accessed without proper session

        Args:
            response: httpx.Response object from the health check request

        Returns:
            bool: True if the endpoint is considered healthy, False otherwise
        """
        # HTTP 200 is always healthy
        if response.status_code == 200:
            return True

        # HTTP 400 is healthy only if it's the expected MCP session error
        if response.status_code == 400:
            try:
                # Parse the JSON response
                response_data = response.json()

                # Check for the specific JSON-RPC error indicating missing session ID
                # This is the expected response from a healthy MCP endpoint when accessed without session
                if (
                    response_data.get("jsonrpc") == "2.0"
                    and response_data.get("id") == "server-error"
                    and isinstance(response_data.get("error"), dict)
                ):
                    error = response_data["error"]
                    if error.get("code") == -32600 and "Missing session ID" in error.get("message", ""):
                        return True

            except (ValueError, KeyError, TypeError):
                # If we can't parse JSON or the structure is wrong, treat as unhealthy
                pass

        # All other status codes (404, 500, etc.) are considered unhealthy
        return False

    async def _update_tools_background(self, service_path: str, proxy_pass_url: str):
        """Update tool list in the background without blocking health checks."""
        try:
            logger.info(f"Starting background tool update for {service_path}")
            from ..core.mcp_client import mcp_client_service
            from ..services.server_service import server_service_v1 as server_service

            # Wait a moment to ensure health check session is fully closed
            # This prevents connection conflicts with servers like currenttime and realserverfaketools
            # that don't allow multiple concurrent sessions on the same endpoint
            await asyncio.sleep(0.5)

            # Get server info to pass transport configuration
            server_info = server_service.get_server_info(service_path)
            logger.info(f"Fetching tools from {proxy_pass_url} for {service_path}")
            tool_list = await mcp_client_service.get_tools_from_server_with_server_info(proxy_pass_url, server_info)
            logger.info(f"Tool fetch result for {service_path}: {len(tool_list) if tool_list else 'None'} tools")

            if tool_list is not None:
                new_tool_count = len(tool_list)
                current_server_info = server_service.get_server_info(service_path)
                if current_server_info:
                    current_tool_count = current_server_info.get("num_tools", 0)

                    # Update if count changed OR if we have no tool details yet
                    current_tool_list = current_server_info.get("tool_list", [])
                    if current_tool_count != new_tool_count or not current_tool_list:
                        updated_server_info = current_server_info.copy()
                        updated_server_info["tool_list"] = tool_list
                        updated_server_info["num_tools"] = new_tool_count

                        server_service.update_server(service_path, updated_server_info)

                        # Broadcast only this specific service update
                        await self.broadcast_health_update(service_path)

        except Exception as e:
            logger.warning(f"Failed to fetch tools for {service_path}: {e}")

    def get_all_health_status(self) -> dict:
        """Get health status for all services."""
        from ..services.server_service import server_service_v1 as server_service

        all_servers = server_service.get_all_servers()

        data = {}
        for path, server_info in all_servers.items():
            data[path] = self._get_service_health_data_fast(path, server_info)

        return data

    async def perform_immediate_health_check(self, service_path: str) -> tuple[str, datetime | None]:
        """Perform an immediate health check for a single service."""
        import httpx

        from ..services.server_service import server_service_v1 as server_service

        server_info = server_service.get_server_info(service_path)
        if not server_info:
            return "error: server not registered", None

        proxy_pass_url = server_info.get("proxy_pass_url")

        # Record check time
        last_checked_time = datetime.now(UTC)
        self.server_last_check_time[service_path] = last_checked_time

        if not proxy_pass_url:
            current_status = "error: missing proxy URL"
            self.server_health_status[service_path] = current_status
            logger.info(f"Health check skipped for {service_path}: Missing URL.")
            return current_status, last_checked_time

        # Set status to 'checking' before performing the check
        logger.info(f"Setting status to '{HealthStatus.CHECKING}' for {service_path} ({proxy_pass_url})...")
        self.server_health_status.get(service_path, HealthStatus.UNKNOWN)
        self.server_health_status[service_path] = HealthStatus.CHECKING

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(settings.health_check_timeout_seconds)) as client:
                # Use transport-aware endpoint checking
                is_healthy, status_detail = await self._check_server_endpoint_transport_aware(
                    client, proxy_pass_url, server_info
                )

                if is_healthy:
                    current_status = status_detail  # Could be "healthy" or "healthy-auth-expired"
                    logger.info(f"Health check successful for {service_path} ({proxy_pass_url}): {status_detail}")

                    # Schedule tool list fetch in background only for fully healthy status
                    logger.info(
                        f"DEBUG: Health check status for {service_path}: status_detail='{status_detail}' (type: {type(status_detail)}) vs HealthStatus.HEALTHY='{HealthStatus.HEALTHY}' (type: {type(HealthStatus.HEALTHY)})"
                    )
                    if status_detail == HealthStatus.HEALTHY:
                        logger.info(
                            f"DEBUG: Status detail matches HealthStatus.HEALTHY, triggering background tool update for {service_path}"
                        )
                        asyncio.create_task(self._update_tools_background(service_path, proxy_pass_url))
                    elif status_detail == HealthStatus.HEALTHY_AUTH_EXPIRED:
                        logger.warning(f"Auth token expired for {service_path} but server is reachable")
                    else:
                        logger.info(
                            f"DEBUG: Status detail '{status_detail}' does not match HealthStatus.HEALTHY, NOT triggering background tool update"
                        )

                else:
                    current_status = status_detail  # Detailed error from transport check
                    logger.info(f"Health check failed for {service_path} ({proxy_pass_url}): {status_detail}")

        except httpx.TimeoutException:
            current_status = "unhealthy: timeout"
            logger.info(f"Health check timeout for {service_path}")
        except httpx.ConnectError:
            current_status = "error: connection failed"
            logger.info(f"Health check connection failed for {service_path}")
        except Exception as e:
            current_status = f"error: {type(e).__name__}"
            logger.error(f"ERROR: Unexpected error during health check for {service_path}: {e}")

        # Update the status
        self.server_health_status[service_path] = current_status
        logger.info(f"Final health status for {service_path}: {current_status}")

        return current_status, last_checked_time

    def _get_service_health_data(self, service_path: str) -> dict:
        """Get health data for a specific service - legacy method, use _get_service_health_data_fast for better performance."""
        from ..services.server_service import server_service_v1 as server_service

        server_info = server_service.get_server_info(service_path)
        return self._get_service_health_data_fast(service_path, server_info or {})

    def _get_service_health_data_fast(self, service_path: str, server_info: dict) -> dict:
        """Get health data for a specific service - optimized version."""
        from ..services.server_service import server_service_v1 as server_service

        # Quick enabled check using cached server_info if possible
        is_enabled = server_service.is_service_enabled(service_path)

        if not is_enabled:
            status = "disabled"
            self.server_health_status[service_path] = "disabled"
        else:
            # Use cached status, only update if transitioning from disabled
            cached_status = self.server_health_status.get(service_path, "unknown")
            if cached_status == "disabled":
                status = HealthStatus.CHECKING
                self.server_health_status[service_path] = HealthStatus.CHECKING
            else:
                status = cached_status

        # Use pre-fetched server_info instead of calling get_server_info again
        last_checked_dt = self.server_last_check_time.get(service_path)
        last_checked_iso = last_checked_dt.isoformat() if last_checked_dt else None
        num_tools = server_info.get("num_tools", 0) if server_info else 0

        return {"status": status, "last_checked_iso": last_checked_iso, "num_tools": num_tools}


# Global health monitoring service instance
health_service = HealthMonitoringService()
