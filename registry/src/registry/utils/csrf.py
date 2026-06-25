import hashlib
import hmac

from ..core.config import settings


def compute_csrf_token(access_token: str) -> str:
    return hmac.new(
        key=settings.secret_key.encode(),
        msg=access_token.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
