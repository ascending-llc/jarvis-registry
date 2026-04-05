# Agent Workflow Platform — Technical Design

> **Architecture:** Agno as outer workflow runtime (operator controls, pause/resume, SSE streaming) + PydanticAI as internal protocol executor (MCP tool calls, A2A agent handoffs). Customer-facing schema is pure Pydantic — no runtime internals exposed.

---

## 1. Model Design

### 1.1 Node Definitions (Customer-Facing, Drag-and-Drop Schema)

```python
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional
from enum import Enum


class ResolutionMode(str, Enum):
    fixed = "fixed"   # exactly one binding, pre-set by customer
    pool  = "pool"    # customer drags 2–5 candidates; executor picks at runtime


class EdgeType(str, Enum):
    sequential  = "sequential"
    conditional = "conditional"
    parallel    = "parallel"


class NodeKind(str, Enum):
    mcp      = "mcp"       # calls an MCP tool/resource/prompt
    a2a      = "a2a"       # delegates to an A2A agent
    router   = "router"    # conditional branch
    join     = "join"      # barrier for parallel branches
    terminal = "terminal"  # explicit end


class FixedRef(BaseModel):
    server_id:    Optional[str] = None
    tool_name:    Optional[str] = None
    resource_uri: Optional[str] = None
    prompt_name:  Optional[str] = None
    agent_id:     Optional[str] = None   # for A2A fixed binding
    endpoint:     Optional[str] = None   # for A2A fixed endpoint


class NodeDependency(BaseModel):
    dependency_id:   str
    dependency_type: Literal["mcp_tool", "mcp_resource", "mcp_prompt", "a2a_agent"]
    resolution_mode: ResolutionMode
    required:        bool = True
    fixed_ref:       Optional[FixedRef] = None   # used when mode=fixed
    pool_refs:       list[FixedRef]     = []      # used when mode=pool (2–5 items max)

    @field_validator("pool_refs")
    @classmethod
    def _validate_pool_size(cls, v: list) -> list:
        if len(v) > 5:
            raise ValueError("pool_refs must contain at most 5 candidates")
        return v


class RetryPolicy(BaseModel):
    max_attempts:       int   = 3
    backoff_ms:         int   = 500
    backoff_multiplier: float = 2.0
    retry_on:           list[str] = ["timeout", "rate_limit"]


class NodePermission(BaseModel):
    allowed_roles:    list[str] = []      # e.g. ["admin", "operator"]
    allowed_users:    list[str] = []      # user_ids
    require_approval: bool      = False   # pause before executing this node
    acl_server_ids:   list[str] = []      # visible MCP servers for this node


class WorkflowNode(BaseModel):
    node_id:        str
    kind:           NodeKind
    label:          str                        # display name in GUI
    capability:     Optional[str] = None       # for mcp: what to do
    task:           Optional[str] = None       # for a2a: what to delegate
    condition_expr: Optional[str] = None       # for router: python-eval expression
    join_group:     Optional[str] = None       # for join: matches parallel branch group
    dependencies:   list[NodeDependency] = []
    retry_policy:   RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_ms:     int = 30_000
    on_error:       Literal["fail_workflow", "skip_node", "route_to_node"] = "fail_workflow"
    permissions:    NodePermission = Field(default_factory=NodePermission)
    input_mapping:  dict = {}                  # maps workflow state keys → node input
    output_mapping: dict = {}                  # maps node output keys → workflow state


class WorkflowEdge(BaseModel):
    edge_id:       str
    from_node_id:  str
    to_node_id:    str
    edge_type:     EdgeType
    condition_key: Optional[str] = None        # state key to evaluate for conditional
    join_group:    Optional[str] = None


class WorkflowPermission(BaseModel):
    allowed_roles:    list[str] = []
    allowed_users:    list[str] = []
    require_approval: bool      = False        # pause entire workflow before start
    acl_scope:        list[str] = []           # visible MCP servers at workflow level


class WorkflowDefinition(BaseModel):
    workflow_id:       str
    version:           int = 1
    name:              str
    description:       str = ""
    nodes:             list[WorkflowNode]
    edges:             list[WorkflowEdge]
    concurrency_limit: int = 4
    permissions:       WorkflowPermission = Field(default_factory=WorkflowPermission)
    created_at:        Optional[str] = None
    updated_at:        Optional[str] = None
```

### 1.2 Runtime Records (Persisted Per-Run in MongoDB)

```python
class ResolvedDependency(BaseModel):
    dependency_id:  str
    server_id:      Optional[str] = None
    tool_name:      Optional[str] = None
    resource_uri:   Optional[str] = None
    agent_id:       Optional[str] = None
    endpoint:       Optional[str] = None
    score:          Optional[float] = None     # if resolved via pool selection
    pool_index:     Optional[int]   = None     # which pool_refs index was chosen


class NodeRunStatus(str, Enum):
    pending                = "pending"
    resolving_dependencies = "resolving_dependencies"
    awaiting_approval      = "awaiting_approval"
    running                = "running"
    success                = "success"
    failed                 = "failed"
    skipped                = "skipped"


class NodeRun(BaseModel):
    node_run_id:           str
    run_id:                str
    node_id:               str
    status:                NodeRunStatus
    attempt:               int = 1
    resolved_dependencies: list[ResolvedDependency] = []
    input_snapshot:        dict = {}
    output_snapshot:       dict = {}
    error:                 Optional[str] = None
    started_at:            Optional[str] = None
    finished_at:           Optional[str] = None


class WorkflowRunStatus(str, Enum):
    queued    = "queued"
    running   = "running"
    paused    = "paused"
    success   = "success"
    failed    = "failed"
    cancelled = "cancelled"


class WorkflowRun(BaseModel):
    run_id:           str
    workflow_id:      str
    workflow_version: int
    triggered_by:     str                      # user_id or system
    status:           WorkflowRunStatus
    state_snapshot:   dict = {}                # live Agno ctx.state
    current_node_id:  Optional[str] = None
    node_runs:        list[NodeRun]  = []
    started_at:       Optional[str]  = None
    finished_at:      Optional[str]  = None
    error:            Optional[str]  = None
```

---

## 2. Service Design

### 2.1 Directory Structure

New code lives inside the existing `registry/` workspace following the project's established layout conventions.

```
registry/src/registry/
│
├── api/v1/                          # existing — add new sub-routers here
│   ├── workflows/
│   │   ├── __init__.py
│   │   ├── workflow_routes.py       # POST/GET/PUT/DELETE /workflows
│   │   └── run_routes.py           # POST /runs, GET /runs, GET /runs/{id}
│   └── operator/
│       ├── __init__.py
│       └── operator_routes.py      # pause / resume / cancel / retry / approve
│
├── services/                        # existing — add new service files
│   ├── workflow_service.py          # CRUD + version management for WorkflowDefinition
│   ├── run_service.py               # WorkflowRun + NodeRun persistence (Beanie)
│   ├── compiler_service.py          # compiles WorkflowDefinition → CompiledWorkflow (Agno)
│   ├── pool_resolver_service.py     # resolves pool deps via PydanticAI context selector
│   └── executor_service.py          # PydanticAI MCP + A2A node executors
│                                    # NOTE: access_control_service.py already exists — reuse as-is
│
├── schemas/                         # existing — add new schema files
│   ├── workflow_schemas.py          # WorkflowDefinition, WorkflowNode request/response
│   └── run_schemas.py               # WorkflowRun, NodeRun, operator action bodies
│
└── models/                          # existing — API-layer models only (no Beanie here)
    └── workflow_models.py           # non-Beanie enums and value objects for workflow layer

# auth-server/src/auth_server/scopes.yml — add three new scope entries:
#   workflows-read:  GET /workflows, /workflows/{id}, /runs, /runs/{id}, /runs/{id}/graph
#   workflows-write: POST/PUT/DELETE /workflows, POST /runs, POST /runs/{id}/pause|resume|cancel|retry|approve
#   workflows-share: PUT /permissions/workflow/{resource_id}
# ScopePermissionMiddleware picks these up automatically — no code changes needed.

registry-pkgs/src/registry_pkgs/
│
├── models/
│   ├── workflow.py                  # NEW — Beanie Documents: WorkflowDefinition, WorkflowRun, NodeRun
│   └── (existing: extended_mcp_server.py, a2a_agent.py, extended_acl_entry.py …)
│
└── runtime/                        # NEW — shared Agno workflow runtime primitives
    ├── __init__.py
    ├── agno_workflow.py             # CompiledWorkflow(Workflow) — Agno outer runtime
    ├── step_factory.py             # topological sort + conditional/parallel dispatch (stdlib graphlib)
    └── graph_utils.py              # _build_execution_order, _apply_input_mapping, _apply_output_mapping
```

### 2.2 Compiler Service (Agno Outer Runtime)

`compiler_service.py` in `registry/services/` compiles a `WorkflowDefinition` into a `CompiledWorkflow` instance from `registry_pkgs.runtime`. The registry service layer owns the compilation step; the runtime primitives live in the shared package.

```python
# registry/src/registry/services/compiler_service.py

from agno.workflow import RunResponse
from agno.utils.log import logger
from typing import AsyncIterator
from bson import ObjectId

from registry_pkgs.runtime.agno_workflow import CompiledWorkflow
from registry_pkgs.models.workflow import WorkflowDefinition
from registry_pkgs.models.enums import ExtendedResourceType
from registry.services.run_service import RunService
from registry.services.access_control_service import ACLService  # existing service
from registry.auth.dependencies import CurrentUser                # existing type alias


class CompiledWorkflow(Workflow):

    def __init__(
        self,
        definition:   WorkflowDefinition,
        run_service:  RunService,
        acl_service:  ACLService,
        caller_id:    str,
    ):
        super().__init__()
        self.definition  = definition
        self.run_service = run_service
        self.acl_service = acl_service
        self.caller_id   = caller_id

    async def run(
        self,
        initial_context: dict = {},
    ) -> AsyncIterator[RunResponse]:

        # ACL preflight: workflow-level permission check
        self.acl_service.assert_workflow_access(self.definition)

        run     = await self.run_service.create_run(self.definition, self.caller_id, initial_context)
        ctx     = dict(initial_context)
        ordered = _build_execution_order(self.definition)

        for node in ordered:
            # ACL preflight: node-level permission check
            self.acl_service.assert_node_access(node)

            # human approval gate
            if node.permissions.require_approval:
                await self.run_service.set_node_status(run.run_id, node.node_id, "awaiting_approval")
                yield RunResponse(
                    content={"event": "awaiting_approval", "node_id": node.node_id}
                )
                await self.run_service.wait_for_approval(run.run_id, node.node_id)

            node_run = await self.run_service.start_node_run(run.run_id, node, ctx)

            try:
                output = await _execute_node(node, ctx, node_run)
                ctx    = _apply_output_mapping(ctx, node.output_mapping, output)
                await self.run_service.complete_node_run(node_run, output)
                yield RunResponse(
                    content={"event": "node_complete", "node_id": node.node_id, "output": output}
                )
            except Exception as exc:
                await self.run_service.fail_node_run(node_run, exc)
                if node.on_error == "fail_workflow":
                    raise
                if node.on_error == "skip_node":
                    continue

        await self.run_service.complete_run(run.run_id, ctx)
        yield RunResponse(content={"event": "workflow_complete", "state": ctx})
```

---

## 3. A2A and MCP Integration + Discovery

### 3.1 Executor Service (PydanticAI Protocol Layer)

```python
# registry/src/registry/services/executor_service.py

from dataclasses import dataclass
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerHTTP

from registry_pkgs.models.workflow import WorkflowNode, ResolvedDependency


@dataclass
class MCPResult:
    output:    str
    tool_used: str
    server_id: str


@dataclass
class A2AResult:
    output:   str
    agent_id: str
    endpoint: str


# One agent per protocol — reused across all node executions
_mcp_agent = Agent(
    "openai:gpt-4o",
    mcp_servers=[MCPServerHTTP(url="http://mcp-registry/sse")],
    result_type=MCPResult,
)

_a2a_agent = Agent(
    "openai:gpt-4o",
    result_type=A2AResult,
)


async def run_mcp_node(
    node:     WorkflowNode,
    context:  dict,
    resolved: list[ResolvedDependency],
) -> dict:
    binding     = resolved[0] if resolved else None
    server_hint = (
        f"server_id={binding.server_id}, tool={binding.tool_name}"
        if binding else ""
    )
    async with _mcp_agent.run_mcp_servers():
        result = await _mcp_agent.run(
            f"Fulfill capability: {node.capability}. {server_hint}. Context: {context}"
        )
    return {
        "output":    result.data.output,
        "tool_used": result.data.tool_used,
        "server_id": result.data.server_id,
    }


async def run_a2a_node(
    node:     WorkflowNode,
    context:  dict,
    resolved: list[ResolvedDependency],
) -> dict:
    binding  = resolved[0] if resolved else None
    endpoint = binding.endpoint if binding else None
    result   = await _a2a_agent.run(
        f"Delegate task: {node.task}. Target endpoint: {endpoint}. Context: {context}"
    )
    return {
        "output":   result.data.output,
        "agent_id": result.data.agent_id,
        "endpoint": result.data.endpoint,
    }
```

### 3.2 Pool Resolver Service

Customers pre-attach 2–5 MCP servers or A2A agents to a node in the drag-and-drop UI. At runtime, PydanticAI selects the best candidate from that pool based on the current task context. No registry search call is made.

The GUI populates the pool picker via `GET /api/v1/servers` and `GET /api/v1/agents` — both already exist in the registry.

```python
# registry/src/registry/services/pool_resolver_service.py

from pydantic_ai import Agent

from registry_pkgs.models.workflow import NodeDependency, ResolvedDependency, ResolutionMode, FixedRef

_selector_agent = Agent("openai:gpt-4o", result_type=int)


async def resolve(
    dep:     NodeDependency,
    context: dict,
) -> ResolvedDependency | None:
    """Resolve a NodeDependency to a single concrete binding."""
    if dep.resolution_mode == ResolutionMode.fixed:
        return _resolve_fixed(dep)

    # pool mode: pick best candidate using LLM context selector
    return await _resolve_pool(dep, context)


def _resolve_fixed(dep: NodeDependency) -> ResolvedDependency:
    r = dep.fixed_ref
    return ResolvedDependency(
        dependency_id=dep.dependency_id,
        server_id=r.server_id,
        tool_name=r.tool_name,
        resource_uri=r.resource_uri,
        agent_id=r.agent_id,
        endpoint=r.endpoint,
    )


async def _resolve_pool(
    dep:     NodeDependency,
    context: dict,
) -> ResolvedDependency | None:
    if not dep.pool_refs:
        return None
    if len(dep.pool_refs) == 1:
        return _ref_to_resolved(dep.dependency_id, dep.pool_refs[0], index=0)

    # describe the pool for the LLM
    candidates_desc = [
        f"{i}: server={r.server_id} tool={r.tool_name or ''} agent={r.agent_id or ''} endpoint={r.endpoint or ''}"
        for i, r in enumerate(dep.pool_refs)
    ]
    result = await _selector_agent.run(
        f"Task context: {context}\n"
        f"Pick the best candidate index (0-based) for dependency type '{dep.dependency_type}':\n"
        + "\n".join(candidates_desc)
    )
    index  = max(0, min(result.data, len(dep.pool_refs) - 1))  # clamp to valid range
    chosen = dep.pool_refs[index]
    return _ref_to_resolved(dep.dependency_id, chosen, index=index)


def _ref_to_resolved(
    dependency_id: str,
    ref:           FixedRef,
    index:         int,
) -> ResolvedDependency:
    return ResolvedDependency(
        dependency_id=dependency_id,
        server_id=ref.server_id,
        tool_name=ref.tool_name,
        resource_uri=ref.resource_uri,
        agent_id=ref.agent_id,
        endpoint=ref.endpoint,
        pool_index=index,
    )
```

---

## 4. Permission Control (Node + Workflow Level)

### 4.1 ACL Integration

No new ACL service is written. The existing `access_control_service.ACLService` (in `registry/src/registry/services/access_control_service.py`) is reused directly via the existing DI container.

**Step 1 — Add `workflow` to `ExtendedResourceType`** (in `registry-pkgs/src/registry_pkgs/models/extended_acl_entry.py`):

```python
class ExtendedResourceType(StrEnum):
    AGENT        = "agent"
    MCPSERVER    = "mcpServer"
    REMOTE_AGENT = "remoteAgent"
    FEDERATION   = "federation"
    WORKFLOW     = "workflow"   # ← add this
```

**Step 2 — Permission bits** reuse the existing `PermissionBits` model:

| Bit | Meaning for workflows |
|---|---|
| `VIEW (1)` | See the workflow definition and its runs |
| `EDIT (2)` | Modify workflow definition |
| `DELETE (4)` | Delete workflow |
| `SHARE (8)` | Manage who can access the workflow |

**Step 3 — Route handler pattern** — same as `server_routes.py` / `agent_routes.py`, using the existing `CurrentUser` type alias:

```python
# registry/src/registry/api/v1/workflows/workflow_routes.py

from registry.auth.dependencies import CurrentUser
from registry.deps import get_acl_service
from registry.services.access_control_service import ACLService
from registry_pkgs.models.extended_acl_entry import ExtendedResourceType
from bson import ObjectId

async def trigger_workflow_run(
    workflow_id:  str,
    user_context: CurrentUser,
    acl_service:  ACLService = Depends(get_acl_service),
    run_service:  RunService  = Depends(get_run_service),
):
    user_id = user_context.get("user_id")

    # Check caller has at least VIEW on this workflow
    await acl_service.check_user_permission(
        user_id=ObjectId(user_id),
        resource_type=ExtendedResourceType.WORKFLOW,
        resource_id=ObjectId(workflow_id),
        required_permission="VIEW",
    )
    ...

async def list_workflows(
    user_context: CurrentUser,
    acl_service:  ACLService = Depends(get_acl_service),
):
    user_id = user_context.get("user_id")

    # Returns only workflow IDs the caller has VIEW access to
    accessible_ids = await acl_service.get_accessible_resource_ids(
        user_id=ObjectId(user_id),
        resource_type=ExtendedResourceType.WORKFLOW,
    )
    ...
```

**Step 4 — Scope enforcement via scopes.yml** (`ScopePermissionMiddleware` picks up automatically):

```yaml
# auth-server/src/auth_server/scopes.yml  — append these entries
workflows-read:
  - method: GET
    path: /api/v1/workflows*
  - method: GET
    path: /api/v1/runs*

workflows-write:
  - method: POST
    path: /api/v1/workflows*
  - method: PUT
    path: /api/v1/workflows*
  - method: DELETE
    path: /api/v1/workflows*
  - method: POST
    path: /api/v1/runs*

workflows-share:
  - method: PUT
    path: /api/v1/permissions/workflow*
```

Group → scope mapping follows the existing pattern:
- `jarvis-registry-admin` / `jarvis-registry-power-user` → `workflows-read` + `workflows-write` + `workflows-share`
- `jarvis-registry-user` → `workflows-read` + `workflows-write`
- `jarvis-registry-read-only` → `workflows-read` only

**Step 5 — `require_approval` node gate** is a separate runtime pause flag in `WorkflowNode.permissions`, not an ACL entry. An operator with `EDIT` permission calls `POST /runs/{id}/nodes/{id}/approve`. No new ACL concept needed.

### 4.2 Permission Matrix

| Enforcement | Mechanism | Where |
|---|---|---|
| Workflow visibility | `acl_service.get_accessible_resource_ids(user_id, WORKFLOW)` | `GET /workflows` list handler |
| Workflow run access | `acl_service.check_user_permission(..., "VIEW")` | `POST /workflows/{id}/runs` |
| Workflow edit/delete | `acl_service.check_user_permission(..., "EDIT" / "DELETE")` | `PUT` / `DELETE /workflows/{id}` |
| Scope guard (coarse) | `ScopePermissionMiddleware` + `scopes.yml` | Middleware layer, before any handler |
| Node approval gate | `WorkflowNode.permissions.require_approval` flag | `agno_workflow.py` step loop |
| Pool picker scope | `WorkflowPermission.acl_scope` + `NodePermission.acl_server_ids` | `pool_resolver_service` filters refs |

---

## 5. Runtime Ops Scenarios

### 5.1 Graph Utilities (stdlib only — no third-party library)

```python
# registry-pkgs/src/registry_pkgs/runtime/graph_utils.py
# Uses Python 3.12 stdlib graphlib — no new dependencies.

from graphlib import TopologicalSorter, CycleError
from registry_pkgs.models.workflow import WorkflowDefinition, WorkflowNode


def build_execution_order(defn: WorkflowDefinition) -> list[WorkflowNode]:
    """Topological sort of nodes. Raises CycleError if the graph is cyclic."""
    graph = {node.node_id: set() for node in defn.nodes}
    for edge in defn.edges:
        graph[edge.to_node_id].add(edge.from_node_id)  # to_node depends on from_node
    ts       = TopologicalSorter(graph)
    ordered  = list(ts.static_order())
    node_map = {n.node_id: n for n in defn.nodes}
    return [node_map[nid] for nid in ordered]


def get_ready_nodes(defn: WorkflowDefinition, completed: set[str]) -> list[str]:
    """Returns node_ids ready to run in parallel (all predecessors complete)."""
    graph = {node.node_id: set() for node in defn.nodes}
    for edge in defn.edges:
        graph[edge.to_node_id].add(edge.from_node_id)
    ts = TopologicalSorter(graph)
    ts.prepare()
    for node_id in completed:
        ts.done(node_id)
    return list(ts.get_ready())


def apply_input_mapping(state: dict, mapping: dict) -> dict:
    """mapping = {node_input_key: state_key} — renames state keys for node input."""
    if not mapping:
        return state
    return {node_key: state[state_key] for node_key, state_key in mapping.items() if state_key in state}


def apply_output_mapping(state: dict, mapping: dict, output: dict) -> dict:
    """mapping = {state_key: node_output_key} — merges node output back into state."""
    patch = {state_key: output[out_key] for state_key, out_key in mapping.items() if out_key in output} if mapping else output
    return {**state, **patch}


def build_graph_response(defn: WorkflowDefinition, node_runs: list) -> dict:
    """Pure dict transform for GET /workflows/{id}/graph and GET /runs/{id}/graph."""
    status_map = {nr.node_id: nr for nr in node_runs}
    return {
        "nodes": [
            {
                **node.model_dump(),
                "status": status_map[node.node_id].status if node.node_id in status_map else "pending",
                "started_at":  status_map[node.node_id].started_at  if node.node_id in status_map else None,
                "finished_at": status_map[node.node_id].finished_at if node.node_id in status_map else None,
            }
            for node in defn.nodes
        ],
        "edges": [edge.model_dump() for edge in defn.edges],
    }
```

### 5.2 SSE Streaming

Follows the existing `StreamingResponse` pattern already used in `registry/src/registry/api/proxy_routes.py`:

```python
# registry/src/registry/api/v1/workflows/run_routes.py

import json
from fastapi.responses import StreamingResponse
from registry.auth.dependencies import CurrentUser

async def stream_run_events(
    run_id:       str,
    user_context: CurrentUser,
    run_service:  RunService = Depends(get_run_service),
):
    async def event_generator():
        async for event in run_service.subscribe_run_events(run_id):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )
```

### 5.3 Operator Control Actions

async def operator_pause(run_id: str, run_service: RunService) -> WorkflowRun:
    run = await run_service.get_run(run_id)
    if run.status != WorkflowRunStatus.running:
        raise HTTPException(400, "Run is not currently running")
    await run_service.set_run_status(run_id, WorkflowRunStatus.paused)
    return await run_service.get_run(run_id)
    # Agno runtime checks paused flag at the top of each step loop iteration


# ── SCENARIO B: Resume paused or approval-blocked run ────────────────────────

async def operator_resume(
    run_id:   str,
    node_id:  str | None,
    approved: bool,
    run_service: RunService,
) -> WorkflowRun:
    run = await run_service.get_run(run_id)
    if run.status != WorkflowRunStatus.paused:
        raise HTTPException(400, "Run is not paused")
    if node_id:
        await run_service.approve_node(run_id, node_id, approved)
    await run_service.set_run_status(run_id, WorkflowRunStatus.running)
    return await run_service.get_run(run_id)
    # Agno workflow's wait_for_approval coroutine unblocks


# ── SCENARIO C: Cancel a running or paused workflow ──────────────────────────

async def operator_cancel(run_id: str, run_service: RunService) -> WorkflowRun:
    run = await run_service.get_run(run_id)
    if run.status not in (WorkflowRunStatus.running, WorkflowRunStatus.paused):
        raise HTTPException(400, "Run cannot be cancelled in its current state")
    await run_service.set_run_status(run_id, WorkflowRunStatus.cancelled)
    return await run_service.get_run(run_id)
    # Agno workflow checks cancelled flag and raises CancelledError


# ── SCENARIO D: Retry a single failed node ───────────────────────────────────

async def operator_retry_node(
    run_id:           str,
    node_id:          str,
    run_service:      RunService,
    compiler_service: CompilerService,
) -> NodeRun:
    run  = await run_service.get_run(run_id)
    defn = await compiler_service.get_definition(run.workflow_id)
    node = next((n for n in defn.nodes if n.node_id == node_id), None)
    if node is None:
        raise HTTPException(404, f"Node {node_id} not found in workflow definition")

    # reset just this NodeRun, keep rest of state_snapshot intact
    await run_service.reset_node_run(run_id, node_id)
    await run_service.set_run_status(run_id, WorkflowRunStatus.running)

    ctx    = run.state_snapshot
    output = await _execute_node(node, ctx, node_run=None)
    return await run_service.complete_node_run_by_id(run_id, node_id, output)
```

---

## 6. API Design for GUI

### 6.1 Workflow Definition CRUD

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/workflows` | Create new workflow definition (v1 assigned) |
| `GET` | `/api/v1/workflows` | List workflows (`?name=&page=&limit=`) |
| `GET` | `/api/v1/workflows/{workflow_id}` | Get latest version of a workflow |
| `GET` | `/api/v1/workflows/{workflow_id}/versions` | List all versions with checksums |
| `PUT` | `/api/v1/workflows/{workflow_id}` | Update workflow (bumps version) |
| `DELETE` | `/api/v1/workflows/{workflow_id}` | Delete workflow definition |

### 6.2 Run Lifecycle

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/workflows/{workflow_id}/runs` | Trigger a new run (`{ input_data, version? }`) |
| `GET` | `/api/v1/runs` | List runs (`?workflow_id=&status=&page=&limit=`) |
| `GET` | `/api/v1/runs/{run_id}` | Get run with all `NodeRun` records inline |
| `GET` | `/api/v1/runs/{run_id}/stream` | SSE stream of live run events |

### 6.3 Operator Controls

| Method | Path | Body | Description |
|---|---|---|---|
| `POST` | `/api/v1/runs/{run_id}/pause` | — | Pause a running workflow |
| `POST` | `/api/v1/runs/{run_id}/resume` | `{ node_id?, approved? }` | Resume a paused run |
| `POST` | `/api/v1/runs/{run_id}/cancel` | — | Cancel a running or paused workflow |
| `POST` | `/api/v1/runs/{run_id}/nodes/{node_id}/retry` | — | Retry a single failed node |
| `POST` | `/api/v1/runs/{run_id}/nodes/{node_id}/approve` | `{ approved, comment? }` | Approve or reject a node pending approval |

### 6.4 GUI Support

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/workflows/{workflow_id}/graph` | Graph topology for rendering (`nodes[]`, `edges[]`) |
| `GET` | `/api/v1/runs/{run_id}/graph` | Same topology with live node statuses overlaid |
| `GET` | `/api/v1/runs/{run_id}/nodes/{node_id}/detail` | Full `NodeRun` with snapshots, resolved deps, attempts |

### 6.5 Node Pool Binding UI (Server + Agent Lists)

No dedicated discovery endpoints are needed. The drag-and-drop UI populates the pool picker from existing registry list endpoints:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/servers` | List registered MCP servers to drag into a node pool |
| `GET` | `/api/v1/agents` | List registered A2A agents to drag into a node pool |

Pool size is capped at 5 per `NodeDependency`. The executor selects the best candidate at runtime using `pool_resolver_service.resolve()`.

### 6.6 SSE Event Stream Shape (`GET /api/v1/runs/{run_id}/stream`)

```json
{ "event": "node_start",        "node_id": "review",    "attempt": 1 }
{ "event": "awaiting_approval", "node_id": "review",    "comment": "Approve before executing" }
{ "event": "node_complete",     "node_id": "review",    "output": { "...": "..." } }
{ "event": "node_failed",       "node_id": "review",    "error": "timeout", "attempt": 1 }
{ "event": "workflow_complete", "state":   { "...": "..." } }
```

---

## 7. Full Integration Example

End-to-end example showing customer schema → PydanticAI protocol execution → Agno workflow runtime → API entry point.

```python
import asyncio
from dataclasses import dataclass
from typing import Literal, AsyncIterator

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerHTTP
from agno.workflow import Workflow, RunResponse
from agno.utils.log import logger


# ─── Customer-facing schema (drag-and-drop builder output) ───────────────────

class MCPNodeDef(BaseModel):
    node_id:    str
    kind:       Literal["mcp"]
    capability: str          # e.g. "code_review"

class A2ANodeDef(BaseModel):
    node_id: str
    kind:    Literal["a2a"]
    task:    str             # e.g. "summarize"

class WorkflowDef(BaseModel):
    workflow_id: str
    steps:       list[MCPNodeDef | A2ANodeDef]


# ─── PydanticAI: protocol-strict executors (internal — never exposed) ────────

@dataclass
class MCPResult:
    output: str

@dataclass
class A2AResult:
    output: str

_mcp_agent = Agent(
    "openai:gpt-4o",
    mcp_servers=[MCPServerHTTP(url="http://mcp-registry/sse")],
    result_type=MCPResult,
)

_a2a_agent = Agent(
    "openai:gpt-4o",
    result_type=A2AResult,
)

async def run_mcp_node(node: MCPNodeDef, context: dict) -> str:
    """PydanticAI handles MCP protocol: discovery, tool selection, execution."""
    async with _mcp_agent.run_mcp_servers():
        result = await _mcp_agent.run(
            f"Use MCP tools to fulfill: {node.capability}. Context: {context}"
        )
    return result.data.output

async def run_a2a_node(node: A2ANodeDef, context: dict) -> str:
    """PydanticAI handles A2A protocol: agent discovery, handoff, response."""
    result = await _a2a_agent.run(
        f"Delegate to the best available agent for: {node.task}. Context: {context}"
    )
    return result.data.output


# ─── Agno: outer workflow runtime (operator controls, streaming, pause/resume) ─

class CompiledWorkflow(Workflow):
    """
    Agno drives the workflow lifecycle.
    PydanticAI executes inside each step as a silent protocol layer.
    Customers see operator controls; internal routing is invisible.
    """

    def __init__(self, definition: WorkflowDef):
        super().__init__()
        self.definition = definition

    async def run(self, initial_context: dict = {}) -> AsyncIterator[RunResponse]:
        ctx = dict(initial_context)

        for node_def in self.definition.steps:
            logger.info(f"Executing workflow step: {node_def.node_id}")

            # Internal routing — customer never sees this branch
            if node_def.kind == "mcp":
                output = await run_mcp_node(node_def, ctx)
            elif node_def.kind == "a2a":
                output = await run_a2a_node(node_def, ctx)

            ctx[node_def.node_id] = output
            yield RunResponse(
                content={"event": "node_complete", "node_id": node_def.node_id, "output": output}
            )


# ─── Platform entry point (API layer calls this) ──────────────────────────────

async def execute_customer_workflow(
    definition:  WorkflowDef,
    input_data:  dict,
) -> AsyncIterator[RunResponse]:
    workflow = CompiledWorkflow(definition=definition)
    async for chunk in workflow.run(initial_context=input_data):
        yield chunk


# ─── Example ─────────────────────────────────────────────────────────────────

async def main():
    customer_workflow = WorkflowDef(
        workflow_id="wf-001",
        steps=[
            MCPNodeDef(node_id="review",    kind="mcp", capability="code_review"),
            A2ANodeDef(node_id="summarize", kind="a2a", task="summarize_findings"),
        ],
    )

    async for response in execute_customer_workflow(
        customer_workflow,
        input_data={"pr_url": "https://github.com/org/repo/pull/42"},
    ):
        print(response.content)

asyncio.run(main())
```

---

## 8. Responsibility Summary

| Layer | Owner | What It Handles |
|---|---|---|
| Customer schema (`WorkflowDefinition`) | Pydantic models | Drag-and-drop output, permissions, node deps |
| Protocol execution (MCP + A2A calls) | PydanticAI agents | Pool selection, tool binding, agent handoff |
| Workflow runtime (step loop, pause, stream) | Agno `Workflow` | Operator controls, SSE streaming, approval gates |
| Permission enforcement | Existing `access_control_service.ACLService` | `ExtendedResourceType.WORKFLOW` + `PermissionBits`; scopes via `scopes.yml` |
| Persistence | MongoDB + `RunService` | `WorkflowRun` + `NodeRun` records per execution |
| GUI surface | REST + SSE API | Graph topology, live status overlay, discovery UI |
