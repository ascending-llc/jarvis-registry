"""Test direct MCP proxy authorization against a running Registry.

Run:
  uv run python scripts/verify_direct_proxy_live_e2e.py

Uses real HTTP, MongoDB, Redis, and consent/OAuth APIs with a temporary MCP/OAuth
server. Creates isolated test data, cleans it up on exit, and prints the results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import httpx2
import uvicorn
from bson import ObjectId
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import ElicitResult
from pymongo import AsyncMongoClient
from redis.asyncio import Redis

from registry.core.config import settings
from registry.utils.csrf import compute_csrf_token
from registry_pkgs.core.jwt_tokens import mint_crud_session_token, mint_managed_agent_token

MODERN = "2026-07-28"
LEGACY = "2025-11-25"
INPUT_KEY = "jarvis_registry_authorization"
CAP_KEY = "io.modelcontextprotocol/clientCapabilities"
PROTO_KEY = "io.modelcontextprotocol/protocolVersion"
KINDS = ("modern", "legacy", "oauth", "disabled", "stdio")


class LiveProbe:
    def __init__(self, registry_url: str) -> None:
        self.base = registry_url.rstrip("/")
        self.run = "as1999-live-" + uuid.uuid4().hex[:12]
        self.user_id = ObjectId()
        self.client_id = "mcp-client-" + self.run
        self.servers = {kind: ObjectId() for kind in KINDS}
        self.paths = {kind: f"/{self.run}-{kind}" for kind in KINDS}
        self.checks: list[dict[str, Any]] = []
        self.observed: list[dict[str, Any]] = []
        self.nonces: set[str] = set()
        self.fixture_base = ""
        self.fixture_local_base = ""
        self.oauth_generation = 1
        self.reject_next = False
        self.reject_refresh = False
        self.token_exchanges = 0
        self.refresh_exchanges = 0
        self.discovery_requests = 0
        self.fixture_errors = 0
        self.redis = Redis.from_url(settings.redis_uri, decode_responses=True)
        mongo_cfg = settings.mongo_config
        kwargs: dict[str, Any] = {"serverSelectionTimeoutMS": 5000}
        if mongo_cfg.mongodb_username:
            kwargs.update(username=mongo_cfg.mongodb_username, password=mongo_cfg.mongodb_password)
        self.mongo: AsyncMongoClient = AsyncMongoClient(mongo_cfg.mongo_uri, **kwargs)
        self.db = self.mongo.get_default_database()
        self.http = httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False)

    def check(self, name: str, condition: bool) -> None:
        self.checks.append({"name": name, "passed": bool(condition)})
        print(f"{'PASS' if condition else 'FAIL'} {name}", flush=True)
        if not condition:
            raise AssertionError(name)

    def token(self, *, client_id: str | None = None, subject: str | None = None) -> str:
        return mint_managed_agent_token(
            settings.jwt_token_config,
            subject=subject or str(self.user_id),
            client_id=client_id or self.client_id,
            requested_scopes="mcp-proxy-ops",
            expires_in_seconds=900,
            extra_claims={"preferred_username": self.run},
        )

    def message(self, method: str = "tools/call", *, action: str | None = None, capable: bool = True) -> dict[str, Any]:
        params: dict[str, Any] = {
            "name": "echo",
            "arguments": {"text": "实际测试"},
            "_meta": {PROTO_KEY: MODERN, CAP_KEY: {"elicitation": {"url": {}}} if capable else {}},
        }
        if action:
            params["inputResponses"] = {INPUT_KEY: {"action": action}}
        return {"jsonrpc": "2.0", "id": self.run, "method": method, "params": params}

    async def rpc(
        self,
        kind: str,
        message: dict[str, Any],
        *,
        version: str | None = MODERN,
        token: str | None = None,
        raw: bytes | None = None,
        url_user_id: str | None = None,
    ) -> tuple[httpx.Response, dict[str, Any]]:
        headers = {
            "Authorization": "Bearer " + (token or self.token()),
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if version is not None:
            headers["MCP-Protocol-Version"] = version
        response = await self.http.post(
            f"{self.base}/proxy/server/{url_user_id or self.user_id}{self.paths[kind]}",
            headers=headers,
            content=raw if raw is not None else json.dumps(message, ensure_ascii=False).encode(),
        )
        if "text/event-stream" in response.headers.get("content-type", ""):
            result = next(json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:"))
        else:
            result = response.json() if response.content else {}
        return response, result

    def prompt_url(self, body: dict[str, Any]) -> str:
        result = body.get("result", {})
        self.check("input_required wire", result.get("resultType") == "input_required")
        self.check("no Registry requestState", "requestState" not in result)
        prompt = result["inputRequests"][INPUT_KEY]
        self.check("URL prompt shape", prompt["method"] == "elicitation/create" and prompt["params"]["mode"] == "url")
        self.check("no modern elicitationId", "elicitationId" not in prompt["params"])
        url = prompt["params"]["url"]
        nonce = parse_qs(urlsplit(url).query).get("nonce", [None])[0]
        if nonce:
            self.nonces.add(nonce)
        return url

    async def reset_consent(self) -> None:
        await self.redis.delete(f"{settings.auth_server_redis_key_prefix}:mcp:consent:{self.user_id}:{self.client_id}")

    async def approve(self, auth_url: str) -> None:
        nonce = parse_qs(urlsplit(auth_url).query)["nonce"][0]
        self.nonces.add(nonce)
        session = mint_crud_session_token(
            settings.jwt_token_config,
            subject=str(self.user_id),
            token_type="access_token",
            expires_in_seconds=900,
            extra_claims={"preferred_username": self.run, "scope": "user-read"},
        )
        cookie = {
            "Cookie": f"{settings.session_cookie_name}={session}",
            settings.csrf_header_name: compute_csrf_token(session),
        }
        url = f"{self.base}/api/{settings.api_version}/mcp/consent/server"
        context = await self.http.get(url, params={"nonce": nonce}, headers=cookie)
        self.check("real consent context API", context.status_code == 200)
        approved = await self.http.post(url, json={"nonce": nonce}, headers=cookie)
        self.check(
            "real consent approval API + CSRF", approved.status_code == 200 and approved.json().get("status") == "ok"
        )

    def fixture(self) -> FastAPI:
        app = FastAPI()

        @app.exception_handler(Exception)
        async def unexpected_fixture_error(request: Request, exc: Exception) -> JSONResponse:
            self.fixture_errors += 1
            return JSONResponse(status_code=500, content={"error": "unexpected_fixture_error"})

        @app.get("/.well-known/oauth-protected-resource/{path:path}")
        async def resource_metadata(path: str) -> dict[str, Any]:
            return {
                "resource": f"{self.fixture_base}/{path}",
                "authorization_servers": [self.fixture_base],
                "scopes_supported": ["read"],
            }

        @app.get("/.well-known/oauth-authorization-server")
        async def oauth_metadata() -> dict[str, Any]:
            return {
                "issuer": self.fixture_base,
                "authorization_endpoint": self.fixture_base + "/authorize",
                "token_endpoint": self.fixture_base + "/token",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
            }

        @app.get("/authorize")
        async def authorize(request: Request) -> RedirectResponse:
            query = request.query_params
            return RedirectResponse(
                query["redirect_uri"] + "?" + urlencode({"code": "fixture-code", "state": query["state"]})
            )

        @app.post("/token")
        async def token_endpoint(request: Request) -> JSONResponse:
            form = parse_qs((await request.body()).decode())
            if form.get("grant_type") == ["refresh_token"]:
                self.refresh_exchanges += 1
                if self.reject_refresh:
                    return JSONResponse(status_code=400, content={"error": "invalid_grant"})
                self.oauth_generation += 1
            else:
                self.token_exchanges += 1
            return JSONResponse(
                content={
                    "access_token": f"fixture-access-{self.oauth_generation}",
                    "refresh_token": "fixture-refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "read",
                }
            )

        @app.post("/mcp/{kind}")
        async def downstream(kind: str, request: Request) -> Any:
            raw = await request.body()
            message = json.loads(raw or b"{}")
            challenge = {
                "WWW-Authenticate": f'Bearer resource_metadata="{self.fixture_base}/.well-known/oauth-protected-resource/mcp/oauth"'
            }
            # OAuth discovery probes the resource with POST {} before any MCP call.
            if "method" not in message:
                self.discovery_requests += 1
                return JSONResponse(status_code=401, content={"error": "authorization_required"}, headers=challenge)
            method = message["method"]
            self.observed.append(
                {
                    "kind": kind,
                    "message": message,
                    "raw": raw,
                    "length": request.headers.get("content-length"),
                    "version": request.headers.get("mcp-protocol-version"),
                    "authorization_present": "authorization" in request.headers,
                }
            )
            if (
                kind == "oauth"
                and request.headers.get("authorization") != f"Bearer fixture-access-{self.oauth_generation}"
            ):
                return JSONResponse(status_code=401, content={"error": "invalid_token"}, headers=challenge)
            if kind == "oauth" and self.reject_next and method == "tools/call":
                self.reject_next = False
                return JSONResponse(
                    status_code=401,
                    content={"error": "expired"},
                    headers={
                        "WWW-Authenticate": f'Bearer resource_metadata="{self.fixture_base}/.well-known/oauth-protected-resource/mcp/oauth"'
                    },
                )
            if method == "server/discover":
                if kind == "legacy":
                    return JSONResponse(
                        content={
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {"code": -32601, "message": "Method not found"},
                        }
                    )
                result = {
                    "supportedVersions": [MODERN],
                    "capabilities": {"tools": {}},
                    "cacheScope": "private",
                    "ttlMs": 0,
                    "resultType": "complete",
                }
            elif method == "initialize":
                result = {
                    "protocolVersion": LEGACY,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self.run, "version": "1"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [{"name": "echo", "inputSchema": {"type": "object"}}],
                    "cacheScope": "private",
                    "ttlMs": 0,
                    "resultType": "complete",
                }
            elif method in {"resources/list", "resources/templates/list", "prompts/list"}:
                result = {"resources": [], "resourceTemplates": [], "prompts": [], "resultType": "complete"}
            elif message.get("params", {}).get("arguments", {}).get("sse"):
                wire = (
                    "data: "
                    + json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": {"content": [{"type": "text", "text": "stream-done"}], "resultType": "complete"},
                        }
                    )
                    + "\n\n"
                )

                async def chunks() -> Any:
                    yield wire[:20].encode()
                    yield wire[20:].encode()

                return StreamingResponse(chunks(), media_type="text/event-stream")
            elif message.get("params", {}).get("arguments", {}).get("downstream_input"):
                result = {
                    "resultType": "input_required",
                    "requestState": "fixture-opaque-state",
                    "inputRequests": {
                        "downstream": {
                            "method": "elicitation/create",
                            "params": {
                                "mode": "url",
                                "message": "downstream-owned",
                                "url": self.fixture_base + "/authorize",
                            },
                        }
                    },
                }
            else:
                result = {"content": [{"type": "text", "text": "done"}], "resultType": "complete"}
            return JSONResponse(content={"jsonrpc": "2.0", "id": message.get("id"), "result": result})

        return app

    async def seed(self) -> None:
        await self.db.command("ping")
        await self.redis.ping()
        await self.db.users.insert_one(
            {
                "_id": self.user_id,
                "username": self.run,
                "email": self.run + "@example.invalid",
                "provider": "local",
                "role": "USER",
                "emailVerified": True,
            }
        )
        for kind, server_id in self.servers.items():
            config: dict[str, Any] = {
                "enabled": kind != "disabled",
                "type": "stdio" if kind == "stdio" else "streamable-http",
                "url": f"{self.fixture_base}/mcp/{'modern' if kind in {'disabled', 'stdio'} else kind}",
            }
            if kind == "oauth":
                config.update(
                    requiresOAuth=True,
                    oauth={
                        "client_id": self.run,
                        "token_endpoint_auth_method": "none",
                        "authorization_url": self.fixture_base + "/authorize",
                        "token_url": self.fixture_base + "/token",
                        "scope": "read",
                    },
                )
            await self.db.mcpservers.insert_one(
                {
                    "_id": server_id,
                    "serverName": self.run + "-" + kind,
                    "normalizedServerName": self.run + "-" + kind,
                    "path": self.paths[kind],
                    "config": config,
                    "author": self.user_id,
                    "scope": "private_user",
                    "tags": [self.run],
                    "createdAt": datetime.now(UTC),
                }
            )
            await self.db.aclentries.insert_one(
                {
                    "_id": ObjectId(),
                    "principalType": "user",
                    "principalId": self.user_id,
                    "principalModel": "User",
                    "resourceType": "mcpServer",
                    "resourceId": server_id,
                    "permBits": 15,
                    "grantedBy": self.user_id,
                    "grantedAt": datetime.now(UTC),
                }
            )

    async def sdk(self, kind: str, *, action: str | None = "accept", approve: bool = False) -> None:
        await self.reset_consent()
        calls = 0
        before = len(self.observed)

        async def answer(_ctx: Any, params: Any) -> ElicitResult:
            nonlocal calls
            calls += 1
            if approve:
                await self.approve(params.url)
            else:
                nonce = parse_qs(urlsplit(params.url).query).get("nonce", [None])[0]
                if nonce:
                    self.nonces.add(nonce)
            return ElicitResult(action=action)

        headers = {"Authorization": "Bearer " + self.token()}
        async with httpx2.AsyncClient(timeout=15, trust_env=False, headers=headers) as http:
            async with Client(
                streamable_http_client(f"{self.base}/proxy/server/{self.user_id}{self.paths[kind]}", http_client=http),
                mode="auto",
                elicitation_callback=answer if action else None,
            ) as client:
                self.check(
                    f"SDK auto negotiates {kind}", client.protocol_version == (LEGACY if kind == "legacy" else MODERN)
                )
                await client.list_tools()
                if kind == "legacy":
                    from mcp.shared.exceptions import MCPError

                    try:
                        await client.call_tool("echo", {})
                    except MCPError as exc:
                        self.check("legacy SDK receives -32042", exc.code == -32042)
                    else:
                        self.check("legacy SDK receives -32042", False)
                    return
                result = await client.call_tool("echo", {"text": "SDK"})
                self.check(f"SDK terminal result ({action}, approve={approve})", result.is_error != approve)
        self.check("SDK prompts at most once", calls == (1 if action else 0))
        sent = [row for row in self.observed[before:] if row["message"]["method"] == "tools/call"]
        self.check("SDK downstream execution count", len(sent) == (1 if approve and action == "accept" else 0))
        if sent:
            self.check(
                "SDK strips Registry answer", INPUT_KEY not in sent[0]["message"]["params"].get("inputResponses", {})
            )

    async def exercise(self) -> None:
        for method in ("server/discover", "tools/list", "resources/list", "resources/templates/list", "prompts/list"):
            response, body = await self.rpc("modern", self.message(method))
            self.check(
                f"no-consent {method} reaches downstream (HTTP {response.status_code}, "
                f"RPC error {body.get('error', {}).get('code')})",
                response.status_code == 200 and "result" in body,
            )
        for method in ("tools/call", "prompts/get", "resources/read"):
            response, body = await self.rpc("modern", self.message(method))
            self.check(f"{method} authorization HTTP 200", response.status_code == 200)
            self.prompt_url(body)
        for version in (None, LEGACY, "unknown"):
            _, body = await self.rpc("modern", self.message(), version=version)
            self.check(
                f"authorization format {version}",
                body.get("error", {}).get("code") == (-32003 if version == "unknown" else -32042),
            )
            for prompt in body.get("error", {}).get("data", {}).get("elicitations", []):
                nonce = parse_qs(urlsplit(prompt["url"]).query)["nonce"][0]
                self.nonces.add(nonce)
        _, body = await self.rpc("modern", self.message(capable=False))
        self.check(
            "incapable client gets complete error",
            body.get("result", {}).get("resultType") == "complete" and body["result"].get("isError"),
        )
        _, body = await self.rpc("modern", self.message("completion/complete"))
        self.check("noninteractive method gets -32003", body.get("error", {}).get("code") == -32003)
        for action in ("accept", "decline", "cancel"):
            before = len(self.observed)
            _, body = await self.rpc("modern", self.message(action=action))
            self.check(
                f"missing-consent retry {action} terminates", body.get("result", {}).get("resultType") == "complete"
            )
            self.check(f"retry {action} has no downstream call", len(self.observed) == before)
        for invalid in (None, {}, {"action": "invalid"}):
            msg = self.message()
            msg["params"]["inputResponses"] = {INPUT_KEY: invalid}
            _, body = await self.rpc("modern", msg)
            self.check("invalid Registry response gets -32602", body.get("error", {}).get("code") == -32602)
        _, body = await self.rpc("modern", self.message())
        await self.approve(self.prompt_url(body))
        for action in ("decline", "cancel"):
            before = len(self.observed)
            _, body = await self.rpc("modern", self.message(action=action))
            self.check(
                f"already-authorized {action} does not execute",
                body.get("result", {}).get("isError") is True and len(self.observed) == before,
            )
        msg = self.message(action="accept")
        msg["params"]["inputResponses"]["downstream"] = {"action": "accept", "custom": [1, 2]}
        msg["params"]["requestState"] = "下游-owned-state"
        msg["unknown"] = {"未知": True}
        raw = ("  " + json.dumps(msg, ensure_ascii=False, indent=2) + "\n").encode()
        _, body = await self.rpc("modern", msg, raw=raw)
        sent = self.observed[-1]
        expected = json.loads(raw)
        del expected["params"]["inputResponses"][INPUT_KEY]
        self.check(
            "authorized retry returns downstream result",
            body.get("result", {}).get("content", [{}])[0].get("text") == "done",
        )
        self.check("only Registry answer removed", sent["message"] == expected)
        self.check("rewritten UTF-8 content-length matches bytes", int(sent["length"]) == len(sent["raw"]))
        msg = self.message()
        raw = (" \n" + json.dumps(msg, ensure_ascii=False, indent=2)).encode()
        await self.rpc("modern", msg, raw=raw)
        self.check("ordinary call bytes preserved", self.observed[-1]["raw"] == raw)
        await self.rpc("modern", msg, version="unknown", raw=raw)
        self.check(
            "unknown protocol forwarded untouched",
            self.observed[-1]["version"] == "unknown" and self.observed[-1]["raw"] == raw,
        )
        for flag in ("sse", "downstream_input"):
            msg = self.message()
            msg["params"]["arguments"][flag] = True
            response, body = await self.rpc("modern", msg)
            if flag == "sse":
                self.check(
                    "real SSE drains to complete result",
                    "text/event-stream" in response.headers.get("content-type", "")
                    and body["result"]["content"][0]["text"] == "stream-done",
                )
                self.check("SSE has no stale content-length", "content-length" not in response.headers)
            else:
                self.check(
                    "downstream input and requestState pass through",
                    body["result"]["requestState"] == "fixture-opaque-state"
                    and set(body["result"]["inputRequests"]) == {"downstream"},
                )
        before = len(self.observed)
        foreign_id = str(ObjectId())
        response, _ = await self.rpc("modern", self.message(action="accept"), token=self.token(subject=foreign_id))
        self.check("token identity must match proxy URL", response.status_code == 401 and len(self.observed) == before)
        _, body = await self.rpc(
            "modern", self.message(action="accept"), token=self.token(subject=foreign_id), url_user_id=foreign_id
        )
        self.check(
            "real Mongo ACL denies foreign user", "Access denied" in json.dumps(body) and len(self.observed) == before
        )
        for kind in ("disabled", "stdio"):
            response, body = await self.rpc(kind, self.message("tools/list"))
            self.check(
                f"{kind} remains refused", response.status_code == 200 and body.get("error", {}).get("code") == -32003
            )
        for action, approve in (
            ("accept", True),
            ("accept", False),
            ("decline", False),
            ("cancel", False),
            (None, False),
        ):
            await self.sdk("modern", action=action, approve=approve)
        await self.sdk("legacy")
        await self.oauth_cases()

    async def oauth_cases(self) -> None:
        await self.reset_consent()
        _, body = await self.rpc("oauth", self.message())
        await self.approve(self.prompt_url(body))
        for method in ("initialize", "server/discover", "tools/list"):
            response, _ = await self.rpc("oauth", self.message(method))
            self.check(
                f"OAuth missing {method} gets Registry 401",
                response.status_code == 401 and "resource_metadata=" in response.headers.get("www-authenticate", ""),
            )
        for method in ("resources/list", "resources/templates/list", "prompts/list"):
            response, body = await self.rpc("oauth", self.message(method))
            self.check(
                f"OAuth missing {method} stays in-band",
                response.status_code == 200 and body.get("error", {}).get("code") == -32003,
            )
        _, body = await self.rpc("oauth", self.message(action="accept"))
        self.check(
            "consent then missing OAuth does not prompt again", body.get("result", {}).get("resultType") == "complete"
        )
        _, body = await self.rpc("oauth", self.message())
        auth_url = self.prompt_url(body)
        authorized = await self.http.get(auth_url.replace(self.fixture_base, self.fixture_local_base, 1))
        self.check("fixture OAuth authorize redirects", authorized.status_code in {302, 307})
        callback = urlsplit(authorized.headers["location"])
        completed = await self.http.get(self.base + callback.path + "?" + callback.query)
        self.check(
            "real OAuth callback exchanges token", completed.status_code in {302, 307} and self.token_exchanges == 1
        )
        tokens = await self.db.tokens.find({"userId": self.user_id, "type": "mcp_oauth"}).to_list()
        self.check(
            "real OAuth tokens stored encrypted",
            len(tokens) == 1 and not tokens[0]["token"].startswith("fixture-access"),
        )
        _, body = await self.rpc("oauth", self.message(action="accept"))
        self.check(
            "OAuth authorized retry executes",
            body.get("result", {}).get("content", [{}])[0].get("text") == "done"
            and self.observed[-1]["authorization_present"],
        )
        self.reject_next = True
        before = len(self.observed)
        msg = self.message(action="accept")
        _, body = await self.rpc("oauth", msg)
        sent = self.observed[before:]
        self.check(
            "real downstream 401 refreshes once",
            body.get("result", {}).get("content", [{}])[0].get("text") == "done"
            and self.refresh_exchanges == 1
            and len(sent) == 2,
        )
        self.check(
            "401 resend reuses cleaned body and length",
            sent[0]["raw"] == sent[1]["raw"]
            and INPUT_KEY not in sent[1]["message"]["params"].get("inputResponses", {})
            and all(int(row["length"]) == len(row["raw"]) for row in sent),
        )
        self.reject_next = True
        self.reject_refresh = True
        response, body = await self.rpc("oauth", self.message())
        self.check("401 + invalid refresh stays in-band", response.status_code == 200)
        self.prompt_url(body)

    async def cleanup(self) -> dict[str, int]:
        removed: dict[str, int] = {}
        # Every filter is constrained to IDs generated by this run.
        for collection, query in (
            ("tokens", {"userId": self.user_id}),
            ("aclentries", {"resourceId": {"$in": list(self.servers.values())}}),
            ("mcpservers", {"_id": {"$in": list(self.servers.values())}}),
            ("users", {"_id": self.user_id}),
        ):
            removed[collection] = (await self.db[collection].delete_many(query)).deleted_count
        keys: list[str] = []
        prefixes = {settings.redis_key_prefix, settings.auth_server_redis_key_prefix}
        for prefix in prefixes:
            for pattern in (f"{prefix}:*{self.user_id}*", f"{prefix}:*{self.client_id}*"):
                keys.extend([key async for key in self.redis.scan_iter(match=pattern)])
        # Consent nonces can be created by fallback branches that don't expose structured prompts.
        async for key in self.redis.scan_iter(match=f"{settings.auth_server_redis_key_prefix}:mcp:consent-pending:*"):
            value = await self.redis.get(key)
            if value:
                try:
                    if json.loads(value).get("user_id") == str(self.user_id):
                        keys.append(key)
                except json.JSONDecodeError:
                    continue
        if keys:
            removed["redis_keys"] = await self.redis.delete(*set(keys))
        else:
            removed["redis_keys"] = 0
        return removed

    async def run_all(self) -> int:
        started = time.monotonic()
        server: uvicorn.Server | None = None
        task: asyncio.Task[None] | None = None
        error = None
        cleanup: dict[str, Any] = {}
        sock = socket.socket()
        try:
            # A containerized Registry must reach a host-run fixture through the
            # Docker host gateway, rather than its own loopback interface.
            in_container = os.path.exists("/.dockerenv")
            sock.bind(("127.0.0.1" if in_container else "0.0.0.0", 0))
            port = sock.getsockname()[1]
            self.fixture_local_base = f"http://127.0.0.1:{port}"
            self.fixture_base = f"http://{'127.0.0.1' if in_container else 'host.docker.internal'}:{port}"
            server = uvicorn.Server(uvicorn.Config(self.fixture(), log_level="error", access_log=False, lifespan="off"))
            task = asyncio.create_task(server.serve(sockets=[sock]))
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.02)
            await self.seed()
            await self.exercise()
            self.check("fixture completed without server errors", self.fixture_errors == 0)
        except Exception as exc:
            error = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
            print(f"RUN FAILED: {error}", flush=True)
        finally:
            try:
                cleanup = await self.cleanup()
                self.check(
                    "temporary Mongo records removed",
                    not await self.db.mcpservers.count_documents({"_id": {"$in": list(self.servers.values())}})
                    and not await self.db.users.count_documents({"_id": self.user_id}),
                )
            except Exception as exc:
                cleanup["error"] = type(exc).__name__
                error = error or "cleanup failed"
            await self.http.aclose()
            await self.redis.aclose()
            await self.mongo.close()
            if server:
                server.should_exit = True
            if task:
                await task
            sock.close()
        passed = sum(check["passed"] for check in self.checks)
        failed = len(self.checks) - passed
        print(f"CLEANUP {cleanup}", flush=True)
        print(f"RESULT {passed} passed, {failed} failed in {time.monotonic() - started:.2f}s", flush=True)
        return 1 if error or failed else 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-url", default="http://127.0.0.1:7860")
    args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    return await LiveProbe(args.registry_url).run_all()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
