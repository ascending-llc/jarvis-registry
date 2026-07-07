class DownstreamOAuthConstants:
    """Layer B (registry-as-AS) downstream OAuth protocol invariants"""

    CODE_TTL_SECONDS = 600
    ACCESS_TOKEN_TTL_SECONDS = 3600
    SUPPORTED_RESPONSE_TYPE = "code"
    SUPPORTED_CODE_CHALLENGE_METHOD = "S256"
    PROXY_OPS_SCOPE = "mcp-proxy-ops"
