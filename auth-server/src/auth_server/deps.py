from __future__ import annotations

from fastapi import Depends, Request

from .container import AuthContainer
from .services.cognito_validator_service import SimplifiedCognitoValidator
from .services.user_service import UserService


def get_container(request: Request) -> AuthContainer:
    return request.app.state.container


def get_user_service(container: AuthContainer = Depends(get_container)) -> UserService:
    return container.user_service


def get_validator(container: AuthContainer = Depends(get_container)) -> SimplifiedCognitoValidator:
    return container.validator


def get_oauth2_config(container: AuthContainer = Depends(get_container)) -> dict:
    return container.oauth2_config
