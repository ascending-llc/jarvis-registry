import hashlib
import logging

logger = logging.getLogger(__name__)


def hash_username(username: str) -> str:
    """Hash username for privacy compliance."""
    if not username:
        return "anonymous"
    return f"user_{hashlib.sha256(username.encode()).hexdigest()[:8]}"


def mask_token(token: str) -> str:
    """Mask JWT token showing only last 4 characters."""
    if not token:
        return "***EMPTY***"
    if len(token) > 20:
        return f"...{token[-4:]}"
    return "***MASKED***"
