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


class _ExecutionMetricsContext:
    """
    Base async context manager for MCP execution metrics (tool/resource/prompt).

    Handles the shared lifecycle: start the timer on enter; on exit, default success
    to False when an exception propagated, auto-capture error_type from the exception
    class (when not set explicitly), and delegate the actual recording to _record().

    Subclasses set the metric-specific name label and override _record().

    The success/error_type contract:
    - Clean success: caller calls set_success(True); error_type stays "none".
    - Handled failure (e.g. an isError result with no exception): caller calls
      set_success(False) and may set_error_type("server_not_found") for the failure mode.
    - Raised exception: __aexit__ forces success=False and sets error_type to the
      exception class name unless the caller already set one.
    """

    def __init__(self, server_name: str = "unknown") -> None:
        self._start_time: float = 0
        self._server_name: str = server_name
        self._success: bool = False
        self._error_type: str = "none"
        self._error_type_set: bool = False

    def set_server_name(self, server_name: str) -> None:
        """Set the MCP server name."""
        self._server_name = server_name

    def set_success(self, success: bool) -> None:
        """Set the success status."""
        self._success = success

    def set_error_type(self, error_type: str) -> None:
        """Set a bounded, low-cardinality failure-mode label."""
        self._error_type = error_type
        self._error_type_set = True

    async def __aenter__(self):
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
            if not self._error_type_set:
                self._error_type = exc_type.__name__

        duration = time.perf_counter() - self._start_time

        try:
            self._record(duration)
        except Exception as e:
            logger.warning(f"Failed to record execution metric: {e}")

    def _record(self, duration: float) -> None:
        """Record the metric. Overridden by subclasses."""
        raise NotImplementedError


class ToolExecutionMetricsContext(_ExecutionMetricsContext):
    """
    Context manager for tracking tool execution with dynamic info.

    Useful for proxy functions where tool/server info is resolved at various points.

    Example:
        async with ToolExecutionMetricsContext(tool_name=tool_name, method="POST") as ctx:
            server = await get_server_by_id(server_id)
            ctx.set_server_name(server.serverName)
            result = await proxy_tool_call(...)
            ctx.set_success(not result.isError)
            return result
    """

    def __init__(
        self,
        tool_name: str = "unknown",
        server_name: str = "unknown",
        method: str = "UNKNOWN",
    ) -> None:
        super().__init__(server_name=server_name)
        self._tool_name: str = tool_name
        self._method: str = method

    def set_tool_name(self, tool_name: str) -> None:
        """Set the tool name."""
        self._tool_name = tool_name

    def set_method(self, method: str) -> None:
        """Set the HTTP method."""
        self._method = method

    def _record(self, duration: float) -> None:
        _record_tool_execution(
            tool_name=self._tool_name,
            server_name=self._server_name,
            success=self._success,
            duration_seconds=duration,
            method=self._method,
            error_type=self._error_type,
        )


class ResourceAccessMetricsContext(_ExecutionMetricsContext):
    """
    Context manager for tracking resource access with dynamic info.

    Note: resource_uri is intentionally not recorded (unbounded cardinality).

    Example:
        async with ResourceAccessMetricsContext() as ctx:
            server = await get_server_by_id(server_id)
            ctx.set_server_name(server.serverName)
            result = await read_resource(...)
            ctx.set_success(not result.isError)
            return result
    """

    def _record(self, duration: float) -> None:
        _record_resource_access(
            server_name=self._server_name,
            success=self._success,
            duration_seconds=duration,
            error_type=self._error_type,
        )


class PromptExecutionMetricsContext(_ExecutionMetricsContext):
    """
    Context manager for tracking prompt execution with dynamic info.

    Example:
        async with PromptExecutionMetricsContext(prompt_name=prompt_name) as ctx:
            server = await get_server_by_id(server_id)
            ctx.set_server_name(server.serverName)
            result = await execute_prompt(...)
            ctx.set_success(not result.isError)
            return result
    """

    def __init__(
        self,
        prompt_name: str = "unknown",
        server_name: str = "unknown",
    ) -> None:
        super().__init__(server_name=server_name)
        self._prompt_name: str = prompt_name

    def set_prompt_name(self, prompt_name: str) -> None:
        """Set the prompt name."""
        self._prompt_name = prompt_name

    def _record(self, duration: float) -> None:
        _record_prompt_execution(
            prompt_name=self._prompt_name,
            server_name=self._server_name,
            success=self._success,
            duration_seconds=duration,
            error_type=self._error_type,
        )
