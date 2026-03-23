from __future__ import annotations

from functools import cached_property

from itsdangerous import URLSafeTimedSerializer

from .core.config import AuthSettings
from .providers.factory import get_auth_provider
from .services.cognito_validator_service import SimplifiedCognitoValidator
from .services.user_service import UserService
from .utils.config_loader import OAuth2ConfigLoader


class AuthContainer:
    """App-scoped dependencies for the auth server."""

    def __init__(self, settings: AuthSettings):
        self.settings = settings

    @cached_property
    def oauth2_config_loader(self) -> OAuth2ConfigLoader:
        return OAuth2ConfigLoader()

    @property
    def oauth2_config(self) -> dict:
        return self.oauth2_config_loader.config

    @cached_property
    def user_service(self) -> UserService:
        return UserService()

    @cached_property
    def validator(self) -> SimplifiedCognitoValidator:
        return SimplifiedCognitoValidator(region=self.settings.aws_region)

    def build_signer(self) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self.settings.secret_key)

    def get_provider_config(self, provider_name: str) -> dict | None:
        return self.oauth2_config_loader.get_provider_config(provider_name)

    def get_auth_provider(self, provider_type: str | None = None):
        return get_auth_provider(
            provider_type=provider_type,
            settings_override=self.settings,
            oauth2_config=self.oauth2_config,
        )
