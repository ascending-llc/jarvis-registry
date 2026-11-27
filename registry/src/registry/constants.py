from enum import StrEnum


class ExecutionOutcome(StrEnum):
    """Bounded telemetry outcomes for an execution, including a pending input round."""

    SUCCESS = "success"
    FAILURE = "failure"
    INPUT_REQUIRED = "input_required"


class ProxyOutcome(StrEnum):
    """Bounded outcomes for a request the direct proxy sent to a downstream server."""

    SUCCESS = "success"
    FAILURE = "failure"
    INPUT_REQUIRED = "input_required"
    CANCELLED = "cancelled"


MAX_RETURN_PATH_LENGTH = 2048
OAUTH_AUTHORIZE_RETURN_URL_TOO_LONG_DETAIL = "OAuth authorize return URL is too long"
OPENAPI_SESSION_COOKIE_SCHEME = "SessionCookie"
MAX_CONSENT_REVOKE_BATCH = 100
# DCR client IDs are 33 characters; the static IDs are short config values.
MAX_CONSENT_CLIENT_ID_LENGTH = 256
# Server and agent paths have no length limit at creation, so a cap below a stored path would leave that approval
# removable only by revoking the whole app.
MAX_CONSENT_RESOURCE_PATH_LENGTH = 1024


class PendingConsentFlow(StrEnum):
    """The Registry consent page that may read a pending-consent record.

    Registry and auth-server share the pending-consent store, and both use ``flow_type`` for their own purposes,
    so Registry tags its records under the separate ``PENDING_CONSENT_FLOW_FIELD`` key.
    """

    DOWNSTREAM = "downstream"
    SERVER = "server"
    AGENT = "agent"


PENDING_CONSENT_FLOW_FIELD = "consent_flow"

# Inline skill supporting-file limits, shared by Registry's skill API and GitHub skill sync. Each SkillFile is
# its own MongoDB document, so the single-file cap keeps one file inside the 16 MB document limit. The total cap
# bounds a skill's whole payload: create_skill takes every file in one base64 JSON request, which must fit the
# frontend Nginx `client_max_body_size` (16m), and /skills/{id}/content returns every file in one response.
# The count cap only bounds per-file overhead.
MAX_SKILL_FILE_SIZE = 5 * 1024 * 1024
MAX_SKILL_FILES_TOTAL_SIZE = 10 * 1024 * 1024
MAX_SKILL_FILE_COUNT = 200
MAX_SKILL_FILE_RELATIVE_PATH_LENGTH = 512
RESERVED_SKILL_FILE_NAMES = frozenset({"skill.md"})


class DownstreamOAuthConstants:
    """Layer B (registry-as-AS) downstream OAuth protocol invariants"""

    CODE_TTL_SECONDS = 600
    DEVICE_CODE_TTL_SECONDS = 900
    # Redis retains an expired device_code this much longer than DEVICE_CODE_TTL_SECONDS so a poll
    # arriving just after expiry still finds the key and hits the explicit expires_at check
    # (returning RFC 8628's "expired_token"), instead of the key already being evicted and falling
    # back to the less specific "invalid_grant" / device_code-not-found response.
    DEVICE_CODE_GRACE_PERIOD_SECONDS = 60
    DEVICE_CODE_POLL_INTERVAL_SECONDS = 5
    ACCESS_TOKEN_TTL_SECONDS = 3600
    SUPPORTED_RESPONSE_TYPE = "code"
    SUPPORTED_CODE_CHALLENGE_METHOD = "S256"
    PROXY_OPS_SCOPE = "mcp-proxy-ops"


# auth_source recorded on a user context built from a managed-agent (proxy / Bearer) token.
MANAGED_AGENT_AUTH_SOURCE = "jwt_auth"


class UrlElicitationInputKey(StrEnum):
    """Input-request keys under which a 2026-07-28 URL elicitation goes out as an `InputRequiredResult`.

    The client's retry answer arrives in `inputResponses` under the same key. Each check has its own key, so
    a retry answering one check (consent) can still prompt for the next check that fails (downstream OAuth)
    instead of falling back to text. Shared by the mcpgw gateway and the direct-connect proxy so both
    surfaces expose one wire contract.
    """

    CONSENT = "jarvis_registry_consent"
    AUTHORIZATION = "jarvis_registry_authorization"


# /api/v1/tokens/generate error details. Scope lists are rendered sorted and comma-separated; {purpose}
# is the UI label of the token type, quoted rather than preceded by an article ("a Interactive token").
GENERATED_TOKEN_EMPTY_REQUEST_DETAIL = "requestedScopes must not be empty. Omit it to use your current scopes."
GENERATED_TOKEN_NO_USER_SCOPES_DETAIL = (
    "You have no scopes that can be granted to a token. Ask an administrator to add you to a Jarvis Registry group."
)
GENERATED_TOKEN_SCOPES_OUTSIDE_CEILING_DETAIL = 'These scopes cannot be granted to a token of type "{purpose}": {rejected}. This token type only accepts: {allowed}.'
GENERATED_TOKEN_NO_GRANTABLE_SCOPES_DETAIL = (
    'None of your scopes can be granted to a token of type "{purpose}", so no token was generated. '
    "This token type only accepts: {allowed}."
)

# What repairs a post-commit task the background-task tracker refused at shutdown, logged so a skipped
# write is traceable: the reconciler heals a sync's pending marker; the orphan sweep removes a skipped
# delete's vectors; a refresh fetches a new server's skipped capabilities.
VECTOR_SYNC_SKIPPED_FALLBACK = "the reconciler will heal it"
VECTOR_DELETE_SKIPPED_FALLBACK = "the orphan sweep will remove its vectors"
REGISTRATION_CAPABILITIES_SKIPPED_FALLBACK = "refresh the server to fetch its capabilities"

# JSON-RPC errors shared by direct-proxy routing and response construction.
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
# The gateway deliberately refused the request (access denied, server disabled, unsupported
# transport, downstream authorization rejected or changed mid-call). It sits in JSON-RPC 2.0's
# implementation-defined server-error range (-32000 to -32099), avoiding the codes the MCP spec
# and SDKs already use (-32000 connection closed, -32001 request timeout, -32002 resource not
# found, -32042 URL elicitation required).
MCP_REQUEST_REFUSED_ERROR_CODE = -32003
