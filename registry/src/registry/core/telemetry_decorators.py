"""
Centralized telemetry decorators for the Registry service.

This module provides specialized decorators that use the registry-specific
domain functions to track operations with minimal code changes.

All decorators use time.perf_counter() for accurate timing and handle
exceptions gracefully without affecting business logic.
"""

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import (
    Any,
    TypeVar,
)

from ..utils.otel_metrics import (
    record_auth_request as _record_auth_request,
)
from ..utils.otel_metrics import (
    record_prompt_execution as _record_prompt_execution,
)
from ..utils.otel_metrics import (
    record_registry_operation as _record_registry_operation,
)
from ..utils.otel_metrics import (
    record_resource_access as _record_resource_access,
)
from ..utils.otel_metrics import (
    record_tool_discovery as _record_tool_discovery,
)
from ..utils.otel_metrics import (
    record_tool_execution as _record_tool_execution,
)

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def track_registry_operation(
    operation: str,
    resource_type: str | None = None,
    extract_resource: Callable[..., str] | None = None,
) -> Callable[[F], F]:
    """
    Universal decorator for tracking registry API operations.

    Automatically tracks operation duration, success/failure, and records
    metrics using the registry metrics client.

    Args:
        operation: Type of operation (e.g., "search", "create", "update", "delete", "read", "list")
        resource_type: Static resource type (e.g., "server", "tool", "agent")
        extract_resource: Optional function to dynamically extract resource type from args/kwargs

    Returns:
        Decorated function that tracks the operation

    Example:
        @router.get("/servers")
        @track_registry_operation("list", resource_type="server")
        async def list_servers():
            ...

        @router.post("/search")
        @track_registry_operation("search", extract_resource=lambda q, **kw: q.type)
        async def search(query: SearchQuery):
            ...
    """

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.perf_counter()
            success = False

            # Extract resource name dynamically if function provided
            resource = resource_type
            if extract_resource:
                try:
                    resource = extract_resource(*args, **kwargs)
                except Exception:
                    resource = "unknown"

            try:
                result = await func(*args, **kwargs)
                success = True
                return result
            except Exception:
                raise
            finally:
                duration = time.perf_counter() - start_time
                try:
                    _record_registry_operation(
                        operation=operation,
                        resource_type=resource or func.__name__,
                        success=success,
                        duration_seconds=duration,
                    )
                except Exception as e:
                    logger.warning(f"Failed to record operation metric: {e}")

        return wrapper  # type: ignore

    return decorator


def track_tool_execution[F: Callable[..., Any]](func: F) -> F:
    """
    Decorator to automatically track tool execution metrics.

    Extracts tool_name from request body before execution,
    and server_path from result after execution.

    Example:
        @router.post("/tools/call")
        @track_tool_execution
        async def execute_tool(body: ToolExecutionRequest, user_context: CurrentUser):
            ...
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = time.perf_counter()
        success = False
        tool_name = "unknown"
        server_name = "unknown"

        try:
            # Extract tool name from request body
            body = kwargs.get("body")
            if body and hasattr(body, "tool_name"):
                tool_name = body.tool_name

            # Execute business logic
            result = await func(*args, **kwargs)
            success = True

            # HTTP Response
            if hasattr(result, "server_path"):
                path_segments = result.server_path.strip("/").split("/")
                server_name = path_segments[0] if path_segments else "unknown"
            # SSE
            elif body and hasattr(body, "server_path"):
                path_segments = body.server_path.strip("/").split("/")
                server_name = path_segments[0] if path_segments else "unknown"

            return result

        except Exception:
            success = False
            raise

        finally:
            duration = time.perf_counter() - start_time
            try:
                _record_tool_execution(
                    tool_name=tool_name,
                    server_name=server_name,
                    success=success,
                    duration_seconds=duration,
                    method="POST",
                )
            except Exception as e:
                logger.warning(f"Failed to record tool execution metric: {e}")

    return wrapper  # type: ignore


def track_resource_access[F: Callable[..., Any]](func: F) -> F:
    """
    Decorator to automatically track resource access metrics.

    Extracts resource_uri from request body before execution,
    and server_path from result after execution.

    Example:
        @router.post("/resources/read")
        @track_resource_access
        async def read_resource(body: ResourceReadRequest, user_context: CurrentUser):
            ...
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = time.perf_counter()
        success = False
        resource_uri = "unknown"
        server_name = "unknown"

        try:
            # Extract resource URI from request body
            body = kwargs.get("body")
            if body and hasattr(body, "resource_uri"):
                resource_uri = body.resource_uri

            # Execute business logic
            result = await func(*args, **kwargs)
            success = True

            # Extract server name from result if available
            if hasattr(result, "server_path"):
                # Extract first path segment (e.g., "/github/api" -> "github")
                path_segments = result.server_path.strip("/").split("/")
                server_name = path_segments[0] if path_segments else "unknown"
            elif body and hasattr(body, "server_path"):
                # Fallback to request body
                path_segments = body.server_path.strip("/").split("/")
                server_name = path_segments[0] if path_segments else "unknown"

            return result

        except Exception:
            success = False
            raise

        finally:
            duration = time.perf_counter() - start_time
            try:
                _record_resource_access(
                    resource_uri=resource_uri,
                    server_name=server_name,
                    success=success,
                    duration_seconds=duration,
                )
            except Exception as e:
                logger.warning(f"Failed to record resource access metric: {e}")

    return wrapper  # type: ignore


def track_prompt_execution[F: Callable[..., Any]](func: F) -> F:
    """
    Decorator to automatically track prompt execution metrics.

    Extracts prompt_name from request body before execution,
    and server_path from result after execution.

    Example:
        @router.post("/prompts/execute")
        @track_prompt_execution
        async def execute_prompt(body: PromptExecutionRequest, user_context: CurrentUser):
            ...
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = time.perf_counter()
        success = False
        prompt_name = "unknown"
        server_name = "unknown"

        try:
            # Extract prompt name from request body
            body = kwargs.get("body")
            if body and hasattr(body, "prompt_name"):
                prompt_name = body.prompt_name

            # Execute business logic
            result = await func(*args, **kwargs)
            success = True

            # Extract server name from result if available
            if hasattr(result, "server_path"):
                # Extract first path segment (e.g., "/github/api" -> "github")
                path_segments = result.server_path.strip("/").split("/")
                server_name = path_segments[0] if path_segments else "unknown"
            elif body and hasattr(body, "server_path"):
                # Fallback to request body
                path_segments = body.server_path.strip("/").split("/")
                server_name = path_segments[0] if path_segments else "unknown"

            return result

        except Exception:
            success = False
            raise

        finally:
            duration = time.perf_counter() - start_time
            try:
                _record_prompt_execution(
                    prompt_name=prompt_name,
                    server_name=server_name,
                    success=success,
                    duration_seconds=duration,
                )
            except Exception as e:
                logger.warning(f"Failed to record prompt execution metric: {e}")

    return wrapper  # type: ignore


def track_tool_discovery[F: Callable[..., Any]](func: F) -> F:
    """
    Decorator to automatically track tool discovery metrics.

    Extracts server_name and transport_type from server argument,
    and tools_count/success from result tuple.

    Expected function signature:
        async def retrieve_from_server(self, server, ...) -> Tuple[tools, resources, prompts, caps, error]

    Success is determined by: error (last element) is None
    Tools count is determined by: len(tools) if tools is not None

    Example:
        @track_tool_discovery
        async def retrieve_from_server(self, server: ExtendedMCPServer, ...) -> Tuple[...]:
            ...
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = time.perf_counter()
        success = False
        server_name = "unknown"
        transport_type = "unknown"
        tools_count = 0

        try:
            # Extract server from args (first arg after self for methods)
            server = kwargs.get("server")
            if server is None and len(args) > 1:
                server = args[1]  # args[0] is self for methods

            if server:
                server_name = getattr(server, "serverName", "unknown")
                config = getattr(server, "config", {}) or {}
                transport_type = config.get("type", "streamable-http")

            # Execute business logic
            result = await func(*args, **kwargs)

            # Result is expected to be a tuple: (tools, resources, prompts, capabilities, error)
            if isinstance(result, tuple) and len(result) >= 5:
                tools, _resources, _prompts, _capabilities, error = result
                success = error is None
                if tools is not None:
                    tools_count = len(tools)

            return result

        except Exception:
            success = False
            raise

        finally:
            duration = time.perf_counter() - start_time
            try:
                _record_tool_discovery(
                    server_name=server_name,
                    success=success,
                    duration_seconds=duration,
                    transport_type=transport_type,
                    tools_count=tools_count,
                )
            except Exception as e:
                logger.warning(f"Failed to record tool discovery metric: {e}")

    return wrapper  # type: ignore


class AuthMetricsContext:
    """
    Context manager for tracking authentication with dynamic mechanism detection.

    Useful when the auth mechanism is determined during the authentication process
    rather than being known upfront.

    Example:
        async with AuthMetricsContext() as ctx:
            user_context = await try_jwt_auth(request)
            if user_context:
                ctx.set_mechanism("jwt")
                ctx.set_success(True)
                return user_context

            user_context = await try_session_auth(request)
            if user_context:
                ctx.set_mechanism("session")
                ctx.set_success(True)
                return user_context

            ctx.set_success(False)
            raise AuthenticationError("No valid auth")
    """

    def __init__(self, default_mechanism: str = "unknown"):
        self._start_time: float = 0
        self._mechanism: str = default_mechanism
        self._success: bool = False

    def set_mechanism(self, mechanism: str) -> None:
        """Set the authentication mechanism."""
        self._mechanism = mechanism

    def set_success(self, success: bool) -> None:
        """Set the success status."""
        self._success = success

    async def __aenter__(self) -> "AuthMetricsContext":
        self._start_time = time.perf_counter()
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        if exc_type is not None:
            self._success = False

        duration = time.perf_counter() - self._start_time

        try:
            _record_auth_request(
                mechanism=self._mechanism,
                success=self._success,
                duration_seconds=duration,
            )
        except Exception as e:
            logger.warning(f"Failed to record auth metric: {e}")


class ToolExecutionMetricsContext:
    """
    Context manager for tracking tool execution with dynamic info.

    Useful for proxy functions where tool info may be extracted at various points.

    Example:
        async with ToolExecutionMetricsContext(method=request.method) as ctx:
            ctx.set_server_name(server.serverName)
            tool_name = extract_tool_from_body(body)
            ctx.set_tool_name(tool_name)

            response = await proxy_request(target_url)

            ctx.set_success(response.status_code < 400)
            return response
    """

    def __init__(
        self,
        tool_name: str = "unknown",
        server_name: str = "unknown",
        method: str = "UNKNOWN",
    ):
        self._start_time: float = 0
        self._tool_name: str = tool_name
        self._server_name: str = server_name
        self._method: str = method
        self._success: bool = False

    def set_tool_name(self, tool_name: str) -> None:
        """Set the tool name."""
        self._tool_name = tool_name

    def set_server_name(self, server_name: str) -> None:
        """Set the server name."""
        self._server_name = server_name

    def set_method(self, method: str) -> None:
        """Set the HTTP method."""
        self._method = method

    def set_success(self, success: bool) -> None:
        """Set the success status."""
        self._success = success

    async def __aenter__(self) -> "ToolExecutionMetricsContext":
        self._start_time = time.perf_counter()
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        if exc_type is not None:
            self._success = False

        duration = time.perf_counter() - self._start_time

        try:
            _record_tool_execution(
                tool_name=self._tool_name,
                server_name=self._server_name,
                success=self._success,
                duration_seconds=duration,
                method=self._method,
            )
        except Exception as e:
            logger.warning(f"Failed to record tool execution metric: {e}")
