MAX_RETURN_PATH_LENGTH = 2048
OAUTH_AUTHORIZE_RETURN_URL_TOO_LONG_DETAIL = "OAuth authorize return URL is too long"

# Inline skill supporting-file limits. Each SkillFile is its own MongoDB document, so the single-file
# cap keeps one file inside the 16 MB document limit; the total/count caps bound a single skill's payload.
MAX_SKILL_FILE_SIZE = 5 * 1024 * 1024
MAX_SKILL_FILES_TOTAL_SIZE = 10 * 1024 * 1024
MAX_SKILL_FILE_COUNT = 50
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
