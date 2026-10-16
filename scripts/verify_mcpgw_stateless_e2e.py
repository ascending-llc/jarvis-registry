""" E2E verification: the stateless MCP gateway + Redis client-info cache.

Probes a RUNNING gateway (e.g. local docker compose) to confirm the behaviors introduced,
which the pytest suite can only assert in-process:

  Transport (stateless)
    1. `initialize` returns 200 and NO `Mcp-Session-Id` response header.
    2. `tools/list` with no session id is served (no per-pod affinity).
    3. `tools/list` with a stale `Mcp-Session-Id` is served, not 404'd.

  Redis client-info cache (keyed per the strategy)
    4. A DCR client id (`mcp-client-*`) caches under `{prefix}:mcpgw:client:{user}:{client}`,
       storing only protocolVersion/capabilities/clientInfo with a ~30-day TTL.
    5. A shared client id (`jarvis-headless-agent`) WITH a jti caches under `...:{client}:{jti}`.
    6. The same shared client id WITHOUT a jti is NOT cached (capability-leak guard).
    7. A non-`initialize` request (tools/list only) writes no key.

The script mints managed-agent tokens locally with the registry's own signing keys (from `.env`), so
it needs no browser/OAuth flow. It writes only under a throwaway, per-run user id and deletes those
keys on exit (unless --keep).

Usage:
    uv run python scripts/verify_mcpgw_stateless_e2e.py
    uv run python scripts/verify_mcpgw_stateless_e2e.py \\
        --registry-url http://localhost:7860 --redis-url redis://localhost:6379/1

Environment / flags:
    REGISTRY_URL   gateway base URL           (default http://localhost:7860)
    REDIS_URL      registry's Redis DB URL    (default redis://localhost:6379/1 — note DB 1)
    REDIS_PREFIX   redis_key_prefix           (default: settings.redis_key_prefix)
    --keep         leave the written keys in Redis for inspection
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv
from redis import Redis

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry.core.config import settings  # noqa: E402  (import after .env load)
from registry_pkgs.core.client_categories import MCP_CLIENT_ID_PREFIX  # noqa: E402
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token  # noqa: E402

_GATEWAY_PATH = "/proxy/mcpgw/mcp"
_MCP_ACCEPT = "application/json, text/event-stream"
_THIRTY_DAYS = 2592000


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, passed, detail))
        icon = "✓" if passed else "✗"
        print(f"  {icon} {name}" + (f"  — {detail}" if detail else ""))
        return passed


def _mint(client_id: str, user_id: str, *, jti: str | None = None, scope: str = "mcp-proxy-ops") -> str:
    extra: dict = {"user_id": user_id}
    if jti is not None:
        extra["jti"] = jti
    return mint_managed_agent_token(
        settings.jwt_token_config,
        subject="as1914-verify",
        client_id=client_id,
        requested_scopes=scope,
        expires_in_seconds=600,
        extra_claims=extra,
    )


@dataclass
class RpcResult:
    status: int
    has_session_header: bool
    content_type: str
    # The first JSON-RPC message parsed from the SSE body, or None if the body was truncated/cancelled
    # before a complete event arrived (e.g. a prematurely torn-down stream).
    message: dict | None = None


def _rpc(
    url: str,
    token: str | None,
    method: str = "",
    params: dict | None = None,
    *,
    session_id: str | None = None,
    raw_body: bytes | None = None,
) -> RpcResult:
    """POST one JSON-RPC call and read the first SSE event of the response.

    Reading the body (not just headers) is deliberate: a cancelled/torn-down stream returns 200 headers
    but no complete event, so header-only checks miss truncation. A clean read of one JSON-RPC message
    proves the streaming response actually completed.

    ``token=None`` sends no Authorization header (for the unauthenticated boundary case). ``raw_body``
    sends arbitrary bytes verbatim (for malformed-JSON and missing-params boundary cases).
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": _MCP_ACCEPT,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    body = (
        raw_body
        if raw_body is not None
        else json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    )
    with httpx.Client(timeout=15) as client:
        with client.stream("POST", url, headers=headers, content=body) as resp:
            has_session = "mcp-session-id" in {k.lower() for k in resp.headers}
            ctype = resp.headers.get("content-type", "")
            message: dict | None = None
            if resp.status_code == 200 and "text/event-stream" in ctype:
                try:
                    for line in resp.iter_lines():
                        if line.startswith("data:"):
                            try:
                                message = json.loads(line[len("data:") :].strip())
                            except json.JSONDecodeError:
                                message = None
                            break
                except httpx.RemoteProtocolError:
                    # Stream closed mid-body without a clean terminator — a cancelled/truncated response.
                    message = None
            return RpcResult(resp.status_code, has_session, ctype, message)


def _initialize_params(client_name: str, *, url_elicitation: bool) -> dict:
    caps: dict = {"elicitation": {"url": {}}} if url_elicitation else {}
    return {
        "protocolVersion": "2025-11-25",
        "capabilities": caps,
        "clientInfo": {"name": client_name, "version": "1.0"},
    }


def _client_key(prefix: str, user_id: str, client_id: str, jti: str | None = None) -> str:
    base = f"{prefix}:mcpgw:client:{user_id}:{client_id}"
    return f"{base}:{jti}" if jti else base


def _wait_for_key(redis: Redis, key: str, *, present: bool, attempts: int = 10) -> bool:
    """Poll for a key to (dis)appear. Capture is synchronous server-side, so this resolves fast."""
    for _ in range(attempts):
        if bool(redis.exists(key)) is present:
            return True
        time.sleep(0.1)
    return bool(redis.exists(key)) is present


def main() -> int:
    parser = argparse.ArgumentParser(description="Stateless MCP gateway E2E verification.")
    parser.add_argument("--registry-url", default=os.getenv("REGISTRY_URL", "http://localhost:7860"))
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", "redis://localhost:6379/1"))
    parser.add_argument("--redis-prefix", default=os.getenv("REDIS_PREFIX", settings.redis_key_prefix))
    parser.add_argument("--keep", action="store_true", help="Leave written keys in Redis")
    args = parser.parse_args()

    url = args.registry_url.rstrip("/") + _GATEWAY_PATH
    run = uuid.uuid4().hex[:8]
    user_id = "507f1f77bcf86cd799439011"  # a valid, throwaway ObjectId; need not exist as a user
    dcr_client = f"{MCP_CLIENT_ID_PREFIX}verify-{run}"
    shared_client = settings.headless_agent_client_id  # shared id -> jti-keyed
    jti = f"as1914-{run}"
    noinit_client = f"{MCP_CLIENT_ID_PREFIX}noinit-{run}"

    redis = Redis.from_url(args.redis_url, decode_responses=True)
    report = Report()

    print(f"\nGateway : {url}")
    print(f"Redis   : {args.redis_url}  (prefix: {args.redis_prefix})")
    print(f"Run id  : {run}\n")

    try:
        redis.ping()
    except Exception as exc:  # noqa: BLE001 — surface a clear setup error
        print(f"  ✗ cannot reach Redis at {args.redis_url}: {exc}", file=sys.stderr)
        return 2

    print("Transport — statelessness:")
    dcr_token = _mint(dcr_client, user_id)
    init = _rpc(url, dcr_token, "initialize", _initialize_params("claude-code", url_elicitation=True))
    report.add("initialize returns 200", init.status == 200, f"status={init.status}")
    report.add("initialize sends no Mcp-Session-Id", not init.has_session_header, f"content-type={init.content_type}")
    # The SSE body must complete, not be torn down early (regression guard for the capture middleware's
    # receive replay cancelling the streaming response before its JSON-RPC result is sent).
    has_result = bool(init.message and "result" in init.message)
    report.add(
        "initialize SSE body returns a complete JSON-RPC result (stream not cancelled)",
        has_result,
        "result present" if has_result else "no complete event — stream truncated/cancelled",
    )

    tlist = _rpc(url, dcr_token, "tools/list", {})
    report.add("tools/list served without a session id (no affinity)", tlist.status == 200, f"status={tlist.status}")
    report.add(
        "tools/list SSE body returns a complete JSON-RPC result",
        bool(tlist.message and "result" in tlist.message),
        "result present" if (tlist.message and "result" in tlist.message) else "no complete event",
    )

    stale = _rpc(url, dcr_token, "tools/list", {}, session_id="stale-from-a-dead-pod")
    report.add("stale Mcp-Session-Id served, not 404", stale.status == 200, f"status={stale.status}")

    print("\nRedis client-info cache:")
    dcr_key = _client_key(args.redis_prefix, user_id, dcr_client)
    found = _wait_for_key(redis, dcr_key, present=True)
    report.add("DCR client cached (no jti in key)", found, dcr_key)
    if found:
        raw = redis.get(dcr_key)
        try:
            doc = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            doc = {}
        report.add(
            "cached value holds only protocolVersion/capabilities/clientInfo",
            set(doc) == {"protocolVersion", "capabilities", "clientInfo"},
            f"fields={sorted(doc)}",
        )
        report.add(
            "cached value preserves the client's url-elicitation capability",
            (doc.get("capabilities", {}).get("elicitation") or {}).get("url") is not None,
            "elicitation.url present",
        )
        ttl = redis.ttl(dcr_key)
        report.add("TTL is set and <= 30 days", 0 < ttl <= _THIRTY_DAYS, f"ttl={ttl}s")

    # Shared id WITH a jti -> cached under the jti-suffixed key.
    shared_token_jti = _mint(shared_client, user_id, jti=jti)
    _rpc(url, shared_token_jti, "initialize", _initialize_params("headless", url_elicitation=False))
    shared_jti_key = _client_key(args.redis_prefix, user_id, shared_client, jti)
    report.add(
        "shared id WITH jti cached under jti-suffixed key",
        _wait_for_key(redis, shared_jti_key, present=True),
        shared_jti_key,
    )

    # Shared id WITHOUT a jti -> must NOT be cached (capability-leak guard).
    shared_token_nojti = _mint(shared_client, user_id)
    _rpc(url, shared_token_nojti, "initialize", _initialize_params("headless", url_elicitation=False))
    shared_nojti_key = _client_key(args.redis_prefix, user_id, shared_client)
    report.add(
        "shared id WITHOUT jti is NOT cached (leak guard)",
        _wait_for_key(redis, shared_nojti_key, present=False),
        shared_nojti_key,
    )

    # A non-initialize request writes no key.
    noinit_token = _mint(noinit_client, user_id)
    _rpc(url, noinit_token, "tools/list", {})
    noinit_key = _client_key(args.redis_prefix, user_id, noinit_client)
    report.add("non-initialize request writes no key", _wait_for_key(redis, noinit_key, present=False), noinit_key)

    print("\nBoundary / fail-open cases:")

    # Malformed JSON that still trips the cheap `"initialize"` pre-filter: the parse must fail closed
    # inside the middleware (never break the request) and write no key.
    bad_client = f"{MCP_CLIENT_ID_PREFIX}badjson-{run}"
    bad_token = _mint(bad_client, user_id)
    bad = _rpc(url, bad_token, raw_body=b'{"jsonrpc":"2.0","id":1,"method":"initialize" <<not json')
    bad_key = _client_key(args.redis_prefix, user_id, bad_client)
    report.add(
        "malformed initialize body: request not 5xx (middleware fails open)", bad.status < 500, f"status={bad.status}"
    )
    report.add("malformed initialize body: no key written", _wait_for_key(redis, bad_key, present=False), bad_key)

    # Pre-filter false positive: a tools/list whose params merely contain the substring "initialize".
    fp_client = f"{MCP_CLIENT_ID_PREFIX}falsepos-{run}"
    fp_token = _mint(fp_client, user_id)
    fp = _rpc(url, fp_token, "tools/list", {"cursor": "initialize-ish"})
    fp_key = _client_key(args.redis_prefix, user_id, fp_client)
    report.add("tools/list containing 'initialize' is served", fp.status == 200, f"status={fp.status}")
    report.add("tools/list containing 'initialize' writes no key", _wait_for_key(redis, fp_key, present=False), fp_key)

    # initialize with no params: InitializeRequestParams validation fails -> fail open, write no key.
    np_client = f"{MCP_CLIENT_ID_PREFIX}noparams-{run}"
    np_token = _mint(np_client, user_id)
    np = _rpc(url, np_token, raw_body=b'{"jsonrpc":"2.0","id":1,"method":"initialize"}')
    np_key = _client_key(args.redis_prefix, user_id, np_client)
    report.add("initialize with no params: request not 5xx", np.status < 500, f"status={np.status}")
    report.add("initialize with no params: no key written", _wait_for_key(redis, np_key, present=False), np_key)

    # Unauthenticated: auth middleware rejects before capture -> 401 and no key.
    unauth_client = f"{MCP_CLIENT_ID_PREFIX}unauth-{run}"
    unauth = _rpc(url, None, "initialize", _initialize_params("claude-code", url_elicitation=True))
    unauth_key = _client_key(args.redis_prefix, user_id, unauth_client)
    report.add("unauthenticated initialize is rejected (401)", unauth.status == 401, f"status={unauth.status}")
    report.add("unauthenticated initialize writes no key", _wait_for_key(redis, unauth_key, present=False), unauth_key)

    # Per-token isolation: the same shared client id with two distinct jti caches under two keys.
    jti_b = f"as1914-{run}-b"
    _rpc(
        url,
        _mint(shared_client, user_id, jti=jti_b),
        "initialize",
        _initialize_params("headless", url_elicitation=False),
    )
    key_a = _client_key(args.redis_prefix, user_id, shared_client, jti)
    key_b = _client_key(args.redis_prefix, user_id, shared_client, jti_b)
    report.add(
        "same shared id, two jti -> two independent keys (no collision)",
        _wait_for_key(redis, key_b, present=True) and bool(redis.exists(key_a)),
        f"{key_a} | {key_b}",
    )

    if not args.keep:
        # Clean up every key written under this run's throwaway user id.
        to_delete = list(redis.scan_iter(match=f"{args.redis_prefix}:mcpgw:client:{user_id}:*"))
        if to_delete:
            redis.delete(*to_delete)
            print(f"\nCleaned up {len(to_delete)} test key(s).")

    passed = sum(1 for c in report.checks if c.passed)
    total = len(report.checks)
    print(f"\n{'─' * 60}\n  Result: {passed}/{total} checks passed\n{'─' * 60}\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
