from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import grpc
from a2a.client import ClientConfig, ClientFactory
from a2a.client.middleware import ClientCallContext
from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
    TextPart,
    TransportProtocol,
)

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt
from registry_pkgs.models.a2a_agent import TRANSPORT_GRPC, TRANSPORT_HTTP_JSON, TRANSPORT_JSONRPC, A2AAgent
from registry_pkgs.models.enums import FederationProviderType

logger = logging.getLogger(__name__)

_A2A_JWT_TTL_SECONDS = 300
_A2A_HTTP_TIMEOUT = 300

_AGENTCORE_IAM_UNSUPPORTED_MSG = (
    "IAM-authenticated AgentCore A2A runtime is not supported for direct invocation. "
    "Please configure JWT auth on the AgentCore runtime (or use a proxy path that handles SigV4)."
)

_PROTOCOL_MAP: dict[str, TransportProtocol] = {
    TRANSPORT_JSONRPC: TransportProtocol.jsonrpc,
    TRANSPORT_HTTP_JSON: TransportProtocol.http_json,
    TRANSPORT_GRPC: TransportProtocol.grpc,
}


@dataclass
class A2ACallResult:
    """Result from a call_a2a invocation."""

    text: str
    artifacts: list[Part] = field(default_factory=list)
    success: bool = True
    error: str | None = None


def _normalize_claim_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_normalize_claim_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_claim_value(item) for item in value)
    if isinstance(value, dict):
        return {key: _normalize_claim_value(item) for key, item in value.items()}
    return value


def is_agentcore_runtime(agent: A2AAgent) -> bool:
    """Return True when the agent is a federated AWS Bedrock AgentCore runtime."""
    meta = agent.federationMetadata or {}
    return meta.get("providerType") == FederationProviderType.AWS_AGENTCORE


def get_agentcore_auth_mode(agent: A2AAgent) -> str:
    """Detect AgentCore data-plane auth mode from agent config or metadata."""
    if agent.config and agent.config.runtimeAccess:
        mode = agent.config.runtimeAccess.mode
        return str(mode.value if hasattr(mode, "value") else mode).upper()

    meta = agent.federationMetadata or {}
    config = meta.get("authorizerConfiguration") or {}
    if not isinstance(config, dict) or not config:
        return "IAM"

    authorizer_type = str(config.get("authorizerType") or config.get("type") or "").strip().upper()
    if authorizer_type in ("JWT", "OAUTH"):
        return "JWT"

    for key in ("customJWTAuthorizerConfiguration", "jwtAuthorizerConfiguration"):
        candidate = config.get(key)
        if isinstance(candidate, dict) and any(v not in (None, "", [], {}, ()) for v in candidate.values()):
            return "JWT"

    return "IAM"


def make_agent_jwt(
    *,
    agent_url: str,
    jwt_config: JwtSigningConfig,
    expires_in_seconds: int = _A2A_JWT_TTL_SECONDS,
) -> str:
    """Sign a short-lived JWT for direct service-to-agent authentication."""
    payload = build_jwt_payload(
        subject="jarvis-workflow",
        issuer=jwt_config.jwt_issuer,
        audience=agent_url,
        expires_in_seconds=expires_in_seconds,
    )
    return encode_jwt(payload, jwt_config.jwt_private_key, kid=jwt_config.jwt_self_signed_kid)


def _make_agentcore_jwt(
    agent: A2AAgent,
    *,
    jwt_config: JwtSigningConfig,
    expires_in_seconds: int = _A2A_JWT_TTL_SECONDS,
) -> str:
    runtime_jwt = None
    if agent.config and agent.config.runtimeAccess and agent.config.runtimeAccess.jwt:
        runtime_jwt = agent.config.runtimeAccess.jwt

    extra_claims: dict[str, Any] = {}
    issuer = jwt_config.jwt_issuer
    audience = jwt_config.jwt_audience

    if runtime_jwt:
        if runtime_jwt.allowedClients:
            extra_claims["client_id"] = _normalize_claim_value(runtime_jwt.allowedClients[0])
        if runtime_jwt.allowedScopes:
            cleaned = [s for s in (_normalize_claim_value(s) for s in runtime_jwt.allowedScopes) if s]
            if cleaned:
                extra_claims["scope"] = " ".join(cleaned)
        if runtime_jwt.customClaims:
            extra_claims.update(_normalize_claim_value(runtime_jwt.customClaims))
        if runtime_jwt.discoveryUrl:
            parsed = urlparse(runtime_jwt.discoveryUrl)
            issuer = f"{parsed.scheme}://{parsed.netloc}"
        if runtime_jwt.audiences:
            audience = _normalize_claim_value(runtime_jwt.audiences[0])

    payload = build_jwt_payload(
        subject="jarvis-workflow",
        issuer=issuer,
        audience=audience,
        expires_in_seconds=expires_in_seconds,
        extra_claims=extra_claims or None,
    )
    return encode_jwt(payload, jwt_config.jwt_private_key, kid=jwt_config.jwt_self_signed_kid)


def agent_base_url(agent: A2AAgent) -> str:
    """Return the agent's base URL from its A2A card."""
    return str(agent.card.url).rstrip("/") if agent.card else "unknown"


def raise_if_iam_unsupported(agent: A2AAgent) -> None:
    """Raise NotImplementedError if the agent uses unsupported IAM auth."""
    if is_agentcore_runtime(agent) and get_agentcore_auth_mode(agent) == "IAM":
        raise NotImplementedError(_AGENTCORE_IAM_UNSUPPORTED_MSG)


def build_headers(agent: A2AAgent, *, jwt_config: JwtSigningConfig) -> dict[str, str]:
    """Build per-call HTTP auth headers for an A2A invocation."""
    if is_agentcore_runtime(agent):
        return {
            "Authorization": f"Bearer {_make_agentcore_jwt(agent, jwt_config=jwt_config)}",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": str(uuid.uuid4()),
        }
    token = make_agent_jwt(agent_url=str(agent.card.url).rstrip("/"), jwt_config=jwt_config)
    return {"Authorization": f"Bearer {token}"}


def _create_message(text: str) -> Message:
    return Message(
        kind="message",
        role=Role.user,
        parts=[Part(root=TextPart(kind="text", text=text))],
        message_id=uuid.uuid4().hex,
    )


def _make_grpc_channel_factory(base_url: str) -> Callable[[str], grpc.aio.Channel]:
    """Return a gRPC channel factory that selects TLS mode from the agent URL scheme."""
    scheme = urlparse(base_url).scheme.lower()
    secure = scheme in {"https", "grpcs"}

    def _factory(target: str) -> grpc.aio.Channel:
        if secure:
            return grpc.aio.secure_channel(target, grpc.ssl_channel_credentials())
        return grpc.aio.insecure_channel(target)

    return _factory


def _extract_event(
    event: tuple[Task, TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None] | Message,
) -> tuple[str, list[Part]]:
    """Extract text and artifact parts from a Client.send_message() event.

    Events yielded by the high-level Client are either:
    - Message: direct agent response — extract text/artifact parts directly.
    - tuple[Task, update]: task-mode response — only TaskArtifactUpdateEvent
      carries new content; TaskStatusUpdateEvent and None are status signals only.
    """
    raw_parts: list[Part] = []

    if isinstance(event, Message):
        raw_parts = event.parts or []
    elif isinstance(event, tuple):
        _, update = event
        if isinstance(update, TaskArtifactUpdateEvent):
            raw_parts = update.artifact.parts or []
        # TaskStatusUpdateEvent / None → no content, skip

    text_chunks: list[str] = []
    artifact_parts: list[Part] = []
    for part in raw_parts:
        if isinstance(part.root, TextPart):
            text_chunks.append(part.root.text)
        else:
            artifact_parts.append(part)

    return "".join(text_chunks), artifact_parts


async def call_a2a(
    agent: A2AAgent,
    text: str,
    *,
    jwt_config: JwtSigningConfig,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
) -> A2ACallResult:
    """Invoke an A2A agent via the a2a-sdk ClientFactory.

    Transport (jsonrpc / http_json / grpc) is selected from agent.config.type.
    ClientFactory manages its own httpx client internally; auth headers and
    timeout are injected per-call via ClientCallContext. Events are streamed
    and on_chunk is called for each text chunk received.

    Args:
        agent:      A2AAgent document from MongoDB.
        text:       User message / task description to send.
        jwt_config: JWT signing config for service-to-agent auth.
        on_chunk:   Optional async callback invoked for each text chunk received.
                    Use this to send MCP log notifications for real-time streaming.

    Returns:
        A2ACallResult with accumulated text, artifacts, success flag, and error.
    """
    agent_name = agent.config.title if agent.config else agent.card.name
    transport_type = (agent.config.type if agent.config else TRANSPORT_JSONRPC).lower()
    base_url = str(agent.config.url if agent.config and agent.config.url else agent.card.url).rstrip("/")

    logger.debug(
        "→ calling A2A agent %r  transport=%s  url=%s  prompt=%r",
        agent_name,
        transport_type,
        base_url,
        text[:120],
    )

    agent_card = agent.card.model_copy(deep=True)
    agent_card.url = base_url  # type: ignore[assignment]

    protocol = _PROTOCOL_MAP.get(transport_type, TransportProtocol.jsonrpc)
    text_chunks: list[str] = []
    artifact_parts: list[Part] = []

    # Auth headers and timeout are injected per-call via ClientCallContext so that
    # ClientFactory manages its own httpx client internally (no per-call client creation).
    context = ClientCallContext(
        state={
            "http_kwargs": {
                "headers": build_headers(agent, jwt_config=jwt_config),
                "timeout": _A2A_HTTP_TIMEOUT,
            }
        }
    )

    try:
        config = ClientConfig(
            supported_transports=[protocol],
            use_client_preference=True,
            grpc_channel_factory=(_make_grpc_channel_factory(base_url) if transport_type == TRANSPORT_GRPC else None),
        )
        client = ClientFactory(config).create(agent_card)

        async for event in client.send_message(_create_message(text), context=context):
            chunk_text, parts = _extract_event(event)
            if chunk_text:
                text_chunks.append(chunk_text)
                if on_chunk is not None:
                    try:
                        await on_chunk(chunk_text)
                    except Exception as e:
                        logger.warning("on_chunk callback failed:%s, continuing accumulation", e, exc_info=True)
            artifact_parts.extend(parts)

        full_text = "".join(text_chunks)
        if not full_text and not artifact_parts:
            logger.warning("← A2A agent %r returned no content", agent_name)
            return A2ACallResult(text="", success=False, error="A2A agent returned no content")

        logger.debug(
            "← A2A agent %r responded (%d chars, %d artifacts)",
            agent_name,
            len(full_text),
            len(artifact_parts),
        )
        return A2ACallResult(text=full_text, artifacts=artifact_parts, success=True)

    except Exception as exc:
        logger.exception("A2A call to %r failed", agent_name)
        return A2ACallResult(text="", success=False, error=str(exc))
