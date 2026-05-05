from functools import cache, cached_property

from itsdangerous import URLSafeTimedSerializer

from .core.config import AuthSettings
from .core.types import AllowedProvider
from .providers.factory import get_auth_provider
from .services.cognito_validator_service import SimplifiedCognitoValidator
from .services.user_service import UserService
from .utils.config_loader import AuthProviderConfig, EntraConfig, OAuth2Config, OAuth2ConfigLoader


class AuthContainer:
    """App-scoped dependencies for the auth server."""

    def __init__(self, settings: AuthSettings):
        self._settings = settings
        # Eagerly load OAuth2 config so app can fail early and loudly on start-up if config file is off.
        self._config_loader = OAuth2ConfigLoader(self._settings)
        self._oauth2_config = self._config_loader.get_config()

    @property
    def oauth2_config(self) -> OAuth2Config:
        return self._oauth2_config

    @cached_property
    def user_service(self) -> UserService:
        return UserService()

    @cached_property
    def validator(self) -> SimplifiedCognitoValidator:
        return SimplifiedCognitoValidator(region=self._settings.aws_region)

    @cached_property
    def signer(self) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self._settings.secret_key)

    @cache
    def get_provider_config(self, provider: AllowedProvider) -> AuthProviderConfig | EntraConfig:
        return self._config_loader.get_provider_config(provider)

    @cache
    def get_auth_provider(self, provider: AllowedProvider):
        return get_auth_provider(
            provider,
            self._settings,
            self._oauth2_config,
        )
