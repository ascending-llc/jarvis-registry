# Telemetry Package

A generic OpenTelemetry metrics package that provides configuration-driven metric collection. This package is intentionally decoupled from business logic - services define their own domain-specific metrics and recording functions.

## Architecture Overview

```
packages/telemetry/              # Generic, reusable across all services
  metrics_client.py              # OTelMetricsClient with generic methods
  decorators.py                  # Generic timing decorators
  setup.py                       # OpenTelemetry SDK configuration

your_service/utils/
  otel_metrics.py                # Service-specific domain functions

config/metrics/
  your_service.yml               # YAML config defining metrics to capture
```

## Quick Start

### 1. Create a YAML Configuration File

Create a config file at `config/metrics/<service_name>.yml`:

```yaml
service_name: my_service

counters:
  - name: requests_total
    description: Total number of requests
    unit: "1"
    capture: true

  - name: errors_total
    description: Total number of errors
    unit: "1"
    capture: true

histograms:
  - name: request_duration_seconds
    description: Request duration in seconds
    unit: "s"
    capture: true
```

The `capture` flag controls whether the metric is registered:
- `capture: true` (default) - metric is registered and will record data
- `capture: false` - metric is skipped, calls to record will be silent no-ops

### 2. Create a Service-Specific Metrics Module

Create `your_service/utils/otel_metrics.py`:

```python
"""
Metrics client and domain functions for My Service.
"""

import logging
from typing import Optional

from registry_pkgs.telemetry.metrics_client import create_metrics_client, load_metrics_config

logger = logging.getLogger(__name__)


# Load configuration and create service-specific metrics client
_config = load_metrics_config("my_service")
metrics = create_metrics_client("my_service", config=_config)


# =============================================================================
# Domain-Specific Recording Functions
# =============================================================================


def record_request(
    endpoint: str,
    method: str,
    success: bool,
    duration_seconds: Optional[float] = None,
) -> None:
    """
    Record an API request.

    Args:
        endpoint: The API endpoint
        method: HTTP method (GET, POST, etc.)
        success: Whether the request was successful
        duration_seconds: Request duration in seconds
    """
    attributes = {
        "endpoint": endpoint,
        "method": method,
        "status": "success" if success else "failure",
    }

    metrics.record_counter("requests_total", 1, attributes)

    if duration_seconds is not None:
        metrics.record_histogram("request_duration_seconds", duration_seconds, attributes)


def record_error(error_type: str, endpoint: str) -> None:
    """Record an error occurrence."""
    attributes = {"error_type": error_type, "endpoint": endpoint}
    metrics.record_counter("errors_total", 1, attributes)
```

### 3. Use Domain Functions in Your Code

```python
from your_service.utils.otel_metrics import record_request, record_error

async def handle_request(request):
    start_time = time.perf_counter()
    try:
        result = await process_request(request)
        duration = time.perf_counter() - start_time
        record_request("/api/data", "GET", success=True, duration_seconds=duration)
        return result
    except Exception as e:
        duration = time.perf_counter() - start_time
        record_request("/api/data", "GET", success=False, duration_seconds=duration)
        record_error(type(e).__name__, "/api/data")
        raise
```

## Using Decorators

The package provides generic decorators for automatic timing. You can also create service-specific decorators.

### Generic Decorators

```python
from registry_pkgs.telemetry.decorators import track_duration, create_timed_context

# Using track_duration with a custom record function
def record_func(duration: float, labels: dict) -> None:
    metrics.record_histogram("operation_duration", duration, labels)

@track_duration(record_func, extract_labels=lambda x: {"type": x.type})
async def my_operation(request):
    ...

# Using create_timed_context for code blocks
async with create_timed_context(record_func, labels={"operation": "fetch"}) as ctx:
    result = await fetch_data()
    if result.error:
        ctx.set_success(False)
```

### Creating Service-Specific Decorators and Context Managers

Create helpers in your service that wrap the domain functions. Context managers are preferred when you need to resolve data (like a server name) asynchronously inside the operation.

#### 1. Context Manager Pattern (Recommended)

```python
# your_service/core/telemetry_decorators.py

import time
from typing import Any, Optional
from your_service.utils.otel_metrics import record_request

class RequestMetricsContext:
    def __init__(self, endpoint: str, method: str = "GET"):
        self._start_time = 0
        self._endpoint = endpoint
        self._method = method
        self._success = False
        self._server_name = "unknown"

    def set_server_name(self, name: str):
        self._server_name = name

    def set_success(self, success: bool):
        self._success = success

    async def __aenter__(self):
        self._start_time = time.perf_counter()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._success = False
        duration = time.perf_counter() - self._start_time
        record_request(
            self._endpoint,
            self._method,
            self._success,
            duration
        )

# Usage
async def proxy_request(request, server_id):
    async with RequestMetricsContext("/proxy", request.method) as ctx:
        server = await db.fetch_server(server_id)
        ctx.set_server_name(server.name)
        ...
        ctx.set_success(True)
```

#### 2. Decorator Pattern

```python
# your_service/core/telemetry_decorators.py

def track_request(endpoint: str, method: str = "GET") -> Callable[[F], F]:
...
```

## OpenTelemetry Setup

Call `setup_metrics` during your application's startup phase:

```python
from registry_pkgs.telemetry import setup_metrics

setup_metrics(
    service_name="your-service-name",
    otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
    enable_metrics=True,
    enable_logs=True
)
```

This must be called before creating any metrics clients.

## Generic Client Methods

The `OTelMetricsClient` provides only generic methods:

| Method | Description |
|--------|-------------|
| `create_counter(name, description, unit)` | Register a counter metric |
| `create_histogram(name, description, unit)` | Register a histogram metric |
| `record_counter(name, value, attributes)` | Record a counter value |
| `record_histogram(name, value, attributes)` | Record a histogram value |
| `record_metric(name, value, attributes)` | Auto-detect type and record |
| `get_counter(name)` | Get a registered counter |
| `get_histogram(name)` | Get a registered histogram |

All methods are safe - they swallow exceptions and log warnings to prevent application crashes.

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP collector endpoint | `http://otel-collector:4318` |
| `OTEL_PROMETHEUS_ENABLED` | Enable Prometheus exporter | `false` |

## How It Works

1. **Config Loading**: At module import time, the YAML config is loaded
2. **Metric Registration**: Metrics with `capture: true` are registered with OpenTelemetry
3. **Recording**: Domain functions call `metrics.record_counter()` / `metrics.record_histogram()`
4. **Graceful Degradation**: If a metric isn't registered (config missing, `capture: false`), the call is a silent no-op

```
YAML Config                    Service Module                 Decorators/Code
    |                               |                              |
    v                               v                              v
[counters]  ──> create_metrics_client() ──> record_request() <── @track_request
[histograms]         |                           |
                     v                           v
              _counters: {...}           metrics.record_counter()
              _histograms: {...}                 |
                     |                           v
                     └──────> counter.add() / histogram.record()
```

## Example: Adding a New Service

1. Create `config/metrics/new_service.yml` with your metrics
2. Create `new_service/utils/otel_metrics.py` with:
   - Config loading
   - `metrics = create_metrics_client("new_service", config=_config)`
   - Domain-specific recording functions
3. Optionally create `new_service/core/telemetry_decorators.py` for decorators
4. Import and use your domain functions throughout your service

This keeps the telemetry package generic while allowing each service to define its own domain-specific metrics vocabulary.

## Standard Metrics Reference

Below are the standard metrics currently defined for the core services, including the attributes (labels) used for filtering and aggregation.

### Registry Service

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `registry_operations_total` | Counter | `operation`, `resource_type`, `status` | Tracks CRUD operations (create, read, list, search) on registry resources (server, tool). |
| `mcp_server_requests_total` | Counter | `server_name`, `mode` (`gateway` or `proxy`) | Counts operations on an MCP server (tool calls, resource reads, prompt gets), whether the gateway ran them or the direct proxy forwarded them. |
| `mcp_tool_discovery_total` | Counter | `tool_name` (or query), `source`, `status` | Tracks tool discovery attempts via search or direct lookup. |
| `mcp_tool_execution_total` | Counter | `tool_name`, `server_name`, `method`, `status` | Counts execution of specific tools on MCP servers. |
| `mcp_resource_access_total` | Counter | `resource_uri`, `server_name`, `status` | Tracks access to specific resources (e.g., file reads, data fetch). |
| `mcp_prompt_get_total` | Counter | `prompt_name`, `server_name`, `status` | Counts retrievals of pre-defined prompts on MCP servers. |
| `mcp_proxy_requests_total` | Counter | `server_name`, `http_method` (`POST` or `GET`), `mcp_method` (an MCP spec client method, `other`, or `none` for GET), `status` (`success`, `failure`, `input_required` or `cancelled`) | Counts requests the direct MCP proxy handled for a server, by outcome. Requests that need downstream authorization first are counted as `input_required`, and outbound-auth errors as `failure`. Other refusals before forwarding (ACL, consent, transport, Accept header) are not counted. |
| `mcp_proxy_tool_calls_total` | Counter | `tool_name` (a tool registered on the server, else `other`), `server_name`, `status` (same values as `mcp_proxy_requests_total`) | Counts `tools/call` requests the direct MCP proxy sent to a server, by outcome. |
| `auth_requests_total` | Counter | `mechanism`, `status` | Tracks authentication attempts handled by the registry middleware. |
| `registry_operation_duration_seconds` | Histogram | *Same as above* | Latency distribution of registry operations. |
| `mcp_tool_execution_duration_seconds` | Histogram | *Same as above* | Latency distribution of tool executions. |
| `mcp_tool_discovery_duration_seconds` | Histogram | *Same as above* | Latency distribution of tool discovery operations. |
| `mcp_resource_access_duration_seconds` | Histogram | *Same as above* | Latency distribution of resource access operations. |
| `mcp_prompt_get_duration_seconds` | Histogram | *Same as above* | Latency distribution of prompt retrievals. |
| `mcp_proxy_request_duration_seconds` | Histogram | *Same as `mcp_proxy_requests_total`* | Latency distribution of direct proxy requests. For POST it lasts until the response is complete (for SSE, until the body has been read); for `http_method="GET"` it lasts until the server sends its response headers. |

### Workflow Worker

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `workflow_agent_invocations_total` | Counter | `agent_type` (`mcp`, `a2a`, `a2a_pool` or `builtin`), `executor_key`, `status` (`success` or `failure`), `error_type` | Counts workflow executor invocations. |
| `workflow_agent_call_seconds` | Histogram | *Same as above* | Latency distribution of workflow executor calls. |
| `workflow_runs_total` | Counter | `workflow_name`, `status` | Counts finished workflow runs. |
| `workflow_run_seconds` | Histogram | *Same as above* | Latency distribution of workflow runs. |
| `workflow_tool_calls_total` | Counter | `executor_key`, `tool_name`, `status` (`success` or `error`) | Counts the MCP tool calls an executor's agent run made. |
| `workflow_tool_call_seconds` | Histogram | *Same as above* | Latency distribution of those tool calls. |
| `workflow_llm_tokens_total` | Counter | `agent_type` (`mcp` or `a2a_pool`), `executor_key`, `model`, `direction` (`input` or `output`) | LLM tokens the model provider reported for an executor's agent run, summed over the model calls of that run. `model` is the LiteLLM model string of the node's model source, such as `openai/gpt-4o-mini` or `azure/<deployment name>`. A run that reported no tokens is not counted. |
| `workflow_llm_cost_usd_total` | Counter | `agent_type`, `executor_key`, `model` | List price in USD of the tokens counted by `workflow_llm_tokens_total`: input tokens times the input price plus output tokens times the output price, at the per-token prices LiteLLM lists for the model source's canonical model, the same prices the Models page shows. Left out for a model LiteLLM has no price for. Cache discounts are not applied. |

### Auth Server

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `auth_requests_total` | Counter | `mechanism`, `status` | Tracks authentication attempts (JWT, API Key, Basic). |
| `token_refreshes_total` | Counter | `status` | Tracks successful/failed token refresh attempts. |
| `token_generations_total` | Counter | `status` | Tracks successful/failed token generation events. |
| `token_revocations_total` | Counter | `status` | Tracks token revocation requests. |
| `oauth_callbacks_total` | Counter | `status` | Tracks OAuth callback hits (step 2 of OAuth flow). |
| `oauth_authorizations_total` | Counter | `status` | Tracks OAuth authorization requests (step 1 of OAuth flow). |
| `session_creations_total` | Counter | `status` | Tracks new user session creations. |
| `session_validations_total` | Counter | `status` | Tracks session validation checks. |
| `auth_request_duration_seconds` | Histogram | *Same as above* | Latency of authentication requests. |
| `token_generation_duration_seconds` | Histogram | *Same as above* | Latency of token generation operations. |
| `oauth_flow_duration_seconds` | Histogram | *Same as above* | Latency of the complete OAuth authentication flow. |
| `session_validation_duration_seconds` | Histogram | *Same as above* | Latency of session validation checks. |
