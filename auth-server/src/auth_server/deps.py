from fastapi import Depends, Request
from itsdangerous import URLSafeTimedSerializer

from .container import AuthContainer
from .core.types import AllowedProvider
from .providers.base import AuthProvider
from .services.cognito_validator_service import SimplifiedCognitoValidator
from .services.user_service import UserService
from .utils.config_loader import OAuth2Config


def get_container(request: Request) -> AuthContainer:
    return request.app.state.container


def get_user_service(container: AuthContainer = Depends(get_container)) -> UserService:
    return container.user_service


def get_validator(container: AuthContainer = Depends(get_container)) -> SimplifiedCognitoValidator:
    return container.validator


def get_oauth2_config(container: AuthContainer = Depends(get_container)) -> OAuth2Config:
    return container.oauth2_config


def get_signer(container: AuthContainer = Depends(get_container)) -> URLSafeTimedSerializer:
    return container.signer


def get_auth_provider(provider: AllowedProvider, container: AuthContainer = Depends(get_container)) -> AuthProvider:
    return container.get_auth_provider(provider)
