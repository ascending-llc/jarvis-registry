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
