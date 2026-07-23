"""AS-1741 E2E test: MCP direct-connect workflow execution.

Validates that MCP workflow executors bypass the proxy route and connect
directly to downstream servers.  Covers four topologies:

  Scenario 1  Single AgentCore MCP node (Case 1 — self-signed JWT)
  Scenario 2  Serial chain: MCP → A2A → MCP
  Scenario 3  Parallel MCP nodes (concurrent execution)
  Scenario 4  Mixed topology: parallel MCP → serial A2A

Usage:
    # Run all scenarios against the demo environment:
    uv run python scripts/test_mcp_direct.py \\
        --registry-url https://jarvis-demo.ascendingdc.com/gateway \\
        --token "$REGISTRY_TOKEN"

    # Run a single scenario:
    uv run python scripts/test_mcp_direct.py --scenario 1

    # Use a different MCP server / A2A agent:
    uv run python scripts/test_mcp_direct.py \\
        --mcp-key my-mcp-server --a2a-key my-a2a-agent

Environment variables (overridden by CLI flags):
    REGISTRY_URL       Base URL (default http://localhost:8000)
    REGISTRY_TOKEN     Bearer token for API auth
    MCP_EXECUTOR_KEY   MCP server name (default: Mcp1ForFederationTesting)
    A2A_EXECUTOR_KEY   A2A agent name (default: echo-a2a)
    WORKFLOW_TIMEOUT   Per-scenario poll timeout in seconds (default: 120)
    KEEP_WORKFLOWS     Set to any value to keep test workflows after run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("as1741")

TERMINAL_STATES = {"completed", "failed", "cancelled", "awaiting_approval"}


@dataclass
class NodeResult:
    name: str
    status: str
    error: str | None = None
    output_snippet: str = ""


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    run_status: str = ""
    run_id: str = ""
    workflow_id: str = ""
    nodes: list[NodeResult] = field(default_factory=list)
    error: str = ""
    elapsed: float = 0.0


def _single_mcp_nodes(mcp_key: str) -> list[dict]:
    return [
        {
            "name": "single-mcp",
            "nodeType": "step",
            "executorKey": mcp_key,
            "stepObjective": "List all available tools and return their names.",
        },
    ]


def _serial_chain_nodes(mcp_key: str, a2a_key: str) -> list[dict]:
    return [
        {
            "name": "step1-mcp",
            "nodeType": "step",
            "executorKey": mcp_key,
            "stepObjective": "List all available tools and return their names.",
        },
        {
            "name": "step2-a2a",
            "nodeType": "step",
            "executorKey": a2a_key,
            "stepObjective": "Echo back the input you received.",
            "referencedNodeNames": ["step1-mcp"],
        },
        {
            "name": "step3-mcp",
            "nodeType": "step",
            "executorKey": mcp_key,
            "stepObjective": "Summarise the tools you found.",
            "referencedNodeNames": ["step2-a2a"],
        },
    ]


def _parallel_mcp_nodes(mcp_key: str) -> list[dict]:
    return [
        {
            "name": "parallel-mcp-group",
            "nodeType": "parallel",
            "children": [
                {
                    "name": "mcp-branch-a",
                    "nodeType": "step",
                    "executorKey": mcp_key,
                    "stepObjective": "List available tools.",
                },
                {
                    "name": "mcp-branch-b",
                    "nodeType": "step",
                    "executorKey": mcp_key,
                    "stepObjective": "Return the server name.",
                },
            ],
        },
    ]


def _mixed_topology_nodes(mcp_key: str, a2a_key: str) -> list[dict]:
    return [
        {
            "name": "parallel-mcp",
            "nodeType": "parallel",
            "children": [
                {
                    "name": "mcp-left",
                    "nodeType": "step",
                    "executorKey": mcp_key,
                    "stepObjective": "List available tools.",
                },
                {
                    "name": "mcp-right",
                    "nodeType": "step",
                    "executorKey": mcp_key,
                    "stepObjective": "Return the server name.",
                },
            ],
        },
        {
            "name": "a2a-summarise",
            "nodeType": "step",
            "executorKey": a2a_key,
            "stepObjective": "Echo back the combined results from the previous step.",
            "referencedNodeNames": ["parallel-mcp"],
        },
    ]


SCENARIOS: dict[int, tuple[str, callable]] = {
    1: ("Single AgentCore MCP node", lambda m, a: _single_mcp_nodes(m)),
    2: ("Serial chain: MCP → A2A → MCP", lambda m, a: _serial_chain_nodes(m, a)),
    3: ("Parallel MCP nodes", lambda m, a: _parallel_mcp_nodes(m)),
    4: ("Mixed: parallel MCP → serial A2A", lambda m, a: _mixed_topology_nodes(m, a)),
}


def _api(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/api/v1{path}"


async def _create_workflow(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    name: str,
    nodes: list[dict],
) -> str:
    payload = {
        "name": name,
        "description": f"AS-1741 E2E test — {name}",
        "canvas": {"viewport": {"x": 0, "y": 0, "zoom": 1}},
        "nodes": nodes,
    }
    resp = await client.post(_api(base_url, "/workflows"), headers=headers, json=payload)
    resp.raise_for_status()
    workflow_id = resp.json()["id"]
    log.info("  created workflow %s", workflow_id)
    return workflow_id


async def _enable_workflow(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    workflow_id: str,
) -> None:
    resp = await client.put(
        _api(base_url, f"/workflows/{workflow_id}"),
        headers=headers,
        json={"enabled": True},
    )
    resp.raise_for_status()
    log.info("  enabled workflow")


async def _trigger_run(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    workflow_id: str,
    prompt: str,
) -> str:
    resp = await client.post(
        _api(base_url, f"/workflows/{workflow_id}/runs"),
        headers=headers,
        json={"initialInput": {"user_text": prompt}, "triggerSource": "as1741-test"},
    )
    resp.raise_for_status()
    run_id = resp.json()["runId"]
    log.info("  triggered run %s", run_id)
    return run_id


async def _poll_run(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    workflow_id: str,
    run_id: str,
    timeout: float,
) -> dict:
    deadline = time.monotonic() + timeout
    last_status = ""
    while time.monotonic() < deadline:
        resp = await client.get(
            _api(base_url, f"/workflows/{workflow_id}/runs/{run_id}"),
            headers=headers,
        )
        resp.raise_for_status()
        run = resp.json()
        status = run.get("status", "")
        if status != last_status:
            log.info("  run status: %s", status)
            last_status = status
        if status in TERMINAL_STATES:
            return run
        await asyncio.sleep(1.0)
    raise TimeoutError(f"run {run_id} did not finish within {timeout:.0f}s")


async def _delete_workflow(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    workflow_id: str,
) -> None:
    resp = await client.delete(_api(base_url, f"/workflows/{workflow_id}"), headers=headers)
    resp.raise_for_status()
    log.info("  deleted workflow %s", workflow_id)


async def _run_scenario(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    scenario_id: int,
    mcp_key: str,
    a2a_key: str,
    timeout: float,
    keep: bool,
) -> ScenarioResult:
    label, node_builder = SCENARIOS[scenario_id]
    result = ScenarioResult(name=f"Scenario {scenario_id}: {label}", passed=False)
    log.info("━━ %s ━━", result.name)

    nodes = node_builder(mcp_key, a2a_key)
    t0 = time.monotonic()

    try:
        workflow_id = await _create_workflow(
            client,
            base_url,
            headers,
            name=f"as1741-s{scenario_id}-{label[:30].replace(' ', '-').lower()}",
            nodes=nodes,
        )
        result.workflow_id = workflow_id

        await _enable_workflow(client, base_url, headers, workflow_id)

        run_id = await _trigger_run(
            client,
            base_url,
            headers,
            workflow_id,
            prompt="AS-1741 test: list available tools or echo input.",
        )
        result.run_id = run_id

        run = await _poll_run(client, base_url, headers, workflow_id, run_id, timeout)
        result.run_status = run.get("status", "unknown")
        result.elapsed = time.monotonic() - t0

        for nr in run.get("nodeRuns", []):
            output = nr.get("outputSnapshot", {})
            snippet = str(output.get("content", ""))[:200] if output else ""
            result.nodes.append(
                NodeResult(
                    name=nr.get("nodeName", "?"),
                    status=nr.get("status", "?"),
                    error=nr.get("error"),
                    output_snippet=snippet,
                )
            )

        if result.run_status == "awaiting_approval":
            reqs = run.get("pendingRequirements", [])
            consent_urls = [r.get("consentUrl") for r in reqs if r.get("consentUrl")]
            if consent_urls:
                result.error = f"consent required: {consent_urls[0]}"
                log.warning("  ⚠ run awaiting consent — visit: %s", consent_urls[0])
            else:
                result.error = "awaiting approval (non-consent HITL)"
                log.warning("  ⚠ run awaiting non-consent approval")
            result.passed = False
        elif result.run_status == "completed":
            all_nodes_ok = all(n.status == "completed" for n in result.nodes)
            if all_nodes_ok:
                result.passed = True
                log.info("  ✓ PASSED (%.1fs)", result.elapsed)
            else:
                failed_nodes = [n.name for n in result.nodes if n.status != "completed"]
                result.error = f"nodes not completed: {failed_nodes}"
                log.error("  ✗ FAILED — some nodes did not complete: %s", failed_nodes)
        else:
            result.error = run.get("errorSummary", result.run_status)
            log.error("  ✗ FAILED — status=%s  error=%s", result.run_status, result.error)

    except httpx.HTTPStatusError as exc:
        result.elapsed = time.monotonic() - t0
        body = exc.response.text[:300]
        result.error = f"HTTP {exc.response.status_code}: {body}"
        log.error("  ✗ HTTP error: %s %s", exc.response.status_code, body)
    except TimeoutError as exc:
        result.elapsed = time.monotonic() - t0
        result.error = str(exc)
        log.error("  ✗ %s", exc)
    except Exception as exc:
        result.elapsed = time.monotonic() - t0
        result.error = f"{type(exc).__name__}: {exc}"
        log.exception("  ✗ unexpected error")
    finally:
        if result.workflow_id and not keep:
            try:
                await _delete_workflow(client, base_url, headers, result.workflow_id)
            except Exception:
                log.warning("  cleanup failed for workflow %s", result.workflow_id)

    return result


def _print_report(results: list[ScenarioResult]) -> None:
    print("\n" + "═" * 70)
    print("  AS-1741 MCP Direct-Connect — Test Report")
    print("═" * 70)

    for r in results:
        icon = "✓" if r.passed else "✗"
        print(f"\n  {icon} {r.name}  ({r.elapsed:.1f}s)")
        if r.run_id:
            print(f"    run_id={r.run_id}  workflow_id={r.workflow_id}")
        if r.run_status:
            print(f"    status={r.run_status}")
        for n in r.nodes:
            n_icon = "✓" if n.status == "completed" else "✗"
            error_str = f"  error={n.error}" if n.error else ""
            print(f"      {n_icon} {n.name:<25} {n.status}{error_str}")
            if n.output_snippet:
                print(f"        output: {n.output_snippet[:120]}")
        if r.error:
            print(f"    error: {r.error}")

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print(f"\n{'─' * 70}")
    print(f"  Result: {passed}/{total} scenarios passed")
    print("─" * 70 + "\n")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AS-1741 MCP direct-connect E2E tests.")
    p.add_argument(
        "--registry-url",
        default=os.getenv("REGISTRY_URL", "http://localhost:8000"),
        help="Registry base URL (default: $REGISTRY_URL or http://localhost:8000)",
    )
    p.add_argument(
        "--token",
        default=os.getenv("REGISTRY_TOKEN", ""),
        help="Bearer token for API auth (default: $REGISTRY_TOKEN)",
    )
    p.add_argument(
        "--mcp-key",
        default=os.getenv("MCP_EXECUTOR_KEY", "Mcp1ForFederationTesting"),
        help="MCP server executor key",
    )
    p.add_argument(
        "--a2a-key",
        default=os.getenv("A2A_EXECUTOR_KEY", "echo-a2a"),
        help="A2A agent executor key",
    )
    p.add_argument(
        "--scenario",
        type=int,
        choices=list(SCENARIOS.keys()),
        action="append",
        help="Run specific scenario(s) only (repeatable). Default: all.",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("WORKFLOW_TIMEOUT", "120")),
        help="Per-scenario poll timeout in seconds",
    )
    p.add_argument(
        "--keep",
        action="store_true",
        default=bool(os.getenv("KEEP_WORKFLOWS")),
        help="Keep test workflows after run",
    )
    return p.parse_args()


async def main() -> int:
    args = _parse_args()

    if not args.token:
        log.error("No token provided. Set REGISTRY_TOKEN or use --token.")
        return 1

    headers = {"Authorization": f"Bearer {args.token}"}
    scenarios_to_run = args.scenario or sorted(SCENARIOS.keys())

    log.info("Registry URL : %s", args.registry_url)
    log.info("MCP server   : %s", args.mcp_key)
    log.info("A2A agent    : %s", args.a2a_key)
    log.info("Scenarios    : %s", scenarios_to_run)
    log.info("Timeout      : %.0fs per scenario", args.timeout)
    print()

    results: list[ScenarioResult] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for sid in scenarios_to_run:
            result = await _run_scenario(
                client,
                args.registry_url,
                headers,
                scenario_id=sid,
                mcp_key=args.mcp_key,
                a2a_key=args.a2a_key,
                timeout=args.timeout,
                keep=args.keep,
            )
            results.append(result)
            print()

    _print_report(results)

    all_passed = all(r.passed for r in results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
