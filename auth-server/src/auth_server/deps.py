from fastapi import Depends, Request
from itsdangerous import URLSafeTimedSerializer
from redis import Redis

from registry_pkgs.core.oauth_state_store import OAuthStateStore

from .container import AuthContainer
from .core.types import AllowedProvider
from .providers.base import AuthProvider
from .services.cognito_validator_service import SimplifiedCognitoValidator
from .services.downstream_token_service import DownstreamTokenCheckService
from .services.user_service import UserService
from .utils.config_loader import OAuth2Config


def get_container(request: Request) -> AuthContainer:
    return request.app.state.container


def get_user_service(container: AuthContainer = Depends(get_container)) -> UserService:
    return container.user_service


def get_downstream_token_check(container: AuthContainer = Depends(get_container)) -> DownstreamTokenCheckService:
    return container.downstream_token_check


def get_validator(container: AuthContainer = Depends(get_container)) -> SimplifiedCognitoValidator:
    return container.validator


def get_oauth2_config(container: AuthContainer = Depends(get_container)) -> OAuth2Config:
    return container.oauth2_config


def get_signer(container: AuthContainer = Depends(get_container)) -> URLSafeTimedSerializer:
    return container.signer


def get_auth_provider(provider: AllowedProvider, container: AuthContainer = Depends(get_container)) -> AuthProvider:
    return container.get_auth_provider(provider)


def get_redis_client(container: AuthContainer = Depends(get_container)) -> Redis:
    return container.redis_client


def get_oauth_state_store(container: AuthContainer = Depends(get_container)) -> OAuthStateStore:
    return container.oauth_state_store


def check_if_https(request: Request) -> bool:
    x_forwarded_proto = request.headers.get("x-forwarded-proto", "")
    return x_forwarded_proto == "https" or request.url.scheme == "https"
