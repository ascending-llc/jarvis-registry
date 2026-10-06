MAX_RETURN_PATH_LENGTH = 2048
OAUTH_AUTHORIZE_RETURN_URL_TOO_LONG_DETAIL = "OAuth authorize return URL is too long"

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
# SkillFile.source for files whose bytes Registry stores inline in `body` (created in Registry or by GitHub
# skill sync). Any other source was written by Jarvis Chat, whose file storage Registry cannot read.
REGISTRY_SKILL_FILE_SOURCE = "registry-inline"


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
