import logging
import secrets
from functools import cached_property
from typing import Any, Self
from urllib.parse import urlparse

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .scopes import ScopesConfig, load_scopes_config


class ChunkingConfig(BaseModel):
    max_chunk_size: int = Field(default=2048, description="Maximum size of text chunks for vectorization")
    chunk_overlap: int = Field(default=200, description="Overlap size between consecutive chunks")


class VectorConfig(BaseModel):
    vector_store_type: str = Field(default="weaviate", description="Vector database type")
    embedding_provider: str = Field(default="aws_bedrock", description="Embedding provider")
    weaviate_host: str = Field(default="127.0.0.1", description="Weaviate host address")
    weaviate_port: int = Field(default=8080, description="Weaviate port")
    weaviate_api_key: str | None = Field(default=None, description="Weaviate API key")
    weaviate_collection_prefix: str = Field(default="", description="Weaviate collection prefix")
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    openai_model: str = Field(default="text-embedding-3-small", description="OpenAI embedding model")
    aws_region: str = Field(default="us-east-1", description="AWS region for Bedrock")
    embedding_model: str = Field(default="amazon.titan-embed-text-v2:0", description="Embedding model ID")
    aws_access_key_id: str | None = Field(default=None, description="AWS access key ID")
    aws_secret_access_key: str | None = Field(default=None, description="AWS secret access key")
    aws_session_token: str | None = Field(default=None, description="AWS session token")
    azure_openai_api_key: str | None = Field(default=None, description="Azure OpenAI API key")
    azure_openai_endpoint: str = Field(default="", description="Azure OpenAI endpoint URL")
    azure_openai_api_version: str = Field(default="2024-06-01", description="Azure OpenAI API version")
    azure_openai_resource_name: str = Field(default="", description="Azure OpenAI resource name")
    azure_openai_embedding_deployment: str = Field(default="", description="Azure OpenAI embedding deployment name")
    azure_openai_llm_deployment: str = Field(default="", description="Azure OpenAI LLM deployment name")
    llm_model: str = Field(default="gpt-4", description="LLM model name")


class MongoConfig(BaseModel):
    mongo_uri: str = Field(
        default="mongodb://127.0.0.1:27017/jarvis",
        description="MongoDB connection URI (mongodb://host:port/dbname)",
    )
    mongodb_username: str = Field(default="", description="MongoDB username")
    mongodb_password: str = Field(default="", description="MongoDB password")


class RedisConfig(BaseModel):
    redis_uri: str = Field(default="redis://registry-redis:6379/1", description="Redis connection URI")
    redis_key_prefix: str = Field(default="jarvis-registry", description="Redis key prefix")


class TelemetryConfig(BaseModel):
    otel_metrics_config_path: str = Field(default="", description="Metrics config file path")
    otel_exporter_otlp_endpoint: str = Field(
        default="http://otel-collector:4318", description="OTLP collector endpoint"
    )
    otel_prometheus_enabled: bool = Field(default=False, description="Enable Prometheus metrics endpoint")
    otel_prometheus_port: int = Field(default=9464, description="Prometheus metrics port")


class JarvisBaseSettings(BaseSettings):
    """Shared base settings for all Jarvis services.

    Both `registry` and `auth-server` read from the same secret store (AWS Secrets Manager
    or Azure Key Vault) in every deployment, so shared fields belong here.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # ==================== Signature (NOT related to JWT) ====================
    secret_key: str = ""

    # ==================== JWT ====================
    jwt_private_key: str = ""  # PEM-encoded RSA private key (JWT_PRIVATE_KEY env var)
    jwt_public_key: str = ""  # PEM-encoded RSA public key (JWT_PUBLIC_KEY env var)
    jwt_audience: str = "jarvis-services"
    jwt_self_signed_kid: str = "self-signed-key-v1"

    # ==================== RFC 9110 realm ====================
    # "realm" value in the WWW-Authenticate header. According to RFC 9110, it is suppose to describe
    # the resource being protected. Since we use the same value for both `registry` and `auth-server`,
    # we use a generic value like below.
    jarvis_realm: str = "jarvis-resources"

    # ==================== Server URLs ====================
    auth_server_url: str = "http://localhost:8888"
    auth_server_external_url: str = "http://localhost:8888"
    auth_server_api_prefix: str = ""
    registry_url: str = "http://localhost:7860"
    registry_app_name: str = "jarvis-registry-client"

    # ==================== Logging ====================
    log_level: str = "INFO"
    log_format: str = "%(asctime)s,p%(process)s,{%(name)s:%(lineno)d},%(levelname)s,%(message)s"

    # ==================== MongoDB ====================
    mongo_uri: str = "mongodb://127.0.0.1:27017/jarvis"
    mongodb_username: str = ""
    mongodb_password: str = ""

    # ==================== Telemetry ====================
    otel_metrics_config_path: str = ""
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4318"
    otel_prometheus_enabled: bool = False
    otel_prometheus_port: int = 9464

    # ==================== Scopes ====================
    scopes_config_path: str = ""

    # ==================== Model Validation ====================
    # Skip model validation if set to "disabled". Disabling should only happen for import checks in CI.
    x_jarvis_registry_import_checks: str = "enabled"

    @model_validator(mode="after")
    def _validate_jwt_key_pair(self) -> Self:
        if self.x_jarvis_registry_import_checks == "disabled":
            logging.warning(
                "JWT_PRIVATE_KEY and JWT_PUBLIC_KEY validation is disabled. This should only happen in CI import checks."
            )

            return self

        private_raw = self.jwt_private_key.strip()
        public_raw = self.jwt_public_key.strip()

        if private_raw == "" or public_raw == "":
            raise ValueError("Both JWT_PRIVATE_KEY and JWT_PUBLIC_KEY must be provided.")

        try:
            private_key = load_pem_private_key(private_raw.encode(), password=None)
        except (ValueError, TypeError, UnsupportedAlgorithm) as e:
            raise ValueError("jwt_private_key is not a valid PEM-encoded RSA private key") from e

        if not isinstance(private_key, RSAPrivateKey):
            raise ValueError("jwt_private_key must be an RSA key (not EC or another algorithm)")

        try:
            public_key = load_pem_public_key(public_raw.encode())
        except (ValueError, TypeError, UnsupportedAlgorithm) as e:
            raise ValueError("jwt_public_key is not a valid PEM-encoded RSA public key") from e

        if not isinstance(public_key, RSAPublicKey):
            raise ValueError("jwt_public_key must be an RSA key (not EC or another algorithm)")

        derived = private_key.public_key().public_numbers()
        provided = public_key.public_numbers()
        if derived.n != provided.n or derived.e != provided.e:
            raise ValueError("jwt_private_key and jwt_public_key do not form a matching RSA key pair")

        return self

    def model_post_init(self, __context: Any) -> None:
        if not self.secret_key:
            self.secret_key = secrets.token_hex(32)

        if self.auth_server_api_prefix:
            prefix = self.auth_server_api_prefix.rstrip("/")
            if not self.auth_server_url.endswith(prefix):
                self.auth_server_url = f"{self.auth_server_url.rstrip('/')}{prefix}"
            if not self.auth_server_external_url.endswith(prefix):
                self.auth_server_external_url = f"{self.auth_server_external_url.rstrip('/')}{prefix}"

    # ==================== Shared Properties ====================

    @cached_property
    def mongo_config(self) -> MongoConfig:
        return MongoConfig(
            mongo_uri=self.mongo_uri,
            mongodb_username=self.mongodb_username,
            mongodb_password=self.mongodb_password,
        )

    @cached_property
    def telemetry_config(self) -> TelemetryConfig:
        return TelemetryConfig(
            otel_metrics_config_path=self.otel_metrics_config_path,
            otel_exporter_otlp_endpoint=self.otel_exporter_otlp_endpoint,
            otel_prometheus_enabled=self.otel_prometheus_enabled,
            otel_prometheus_port=self.otel_prometheus_port,
        )

    @cached_property
    def scopes_file_config(self) -> ScopesConfig:
        return ScopesConfig(scopes_config_path=self.scopes_config_path)

    @cached_property
    def scopes_config(self) -> dict[str, Any]:
        return load_scopes_config(self.scopes_file_config)

    @cached_property
    def jwt_issuer(self) -> str:
        """
        Per RFC 8414 requirement on issuer:
        - Both the "issuer" field of the response document of the well-known route(s) and the `iss`
          claim of the JWT tokens issued by our auth-server must be the URL that is the well-known
          URL with the well-known path portion stripped.
        - For example, our well-known routes are
          `https://jarvis-demo.ascendingdc.com/.well-known/openid-configuration`, and
          `https://jarvis-demo.ascendingdc.com/.well-known/oauth-authorization-server`. Therefore our
          "issuer" must be `https://jarvis-demo.ascendingdc.com`.
        """
        result = urlparse(self.auth_server_external_url)
        return f"{result.scheme}://{result.netloc}"

    def configure_logging(self, package_name: str) -> None:
        """
        Configure logging for the service identified by `package_name` and for `registry_pkgs`.

        We set handlers on two named loggers only to avoid noise from the root logger.
        Call this once at application startup, e.g. `settings.configure_logging("registry")`.
        """
        numeric_level = getattr(logging, self.log_level.upper(), logging.INFO)

        service_logger = logging.getLogger(package_name)
        service_logger.propagate = False
        service_logger.setLevel(numeric_level)

        if len(service_logger.handlers) == 0:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(self.log_format))
            service_logger.addHandler(handler)

        registry_pkgs_logger = logging.getLogger("registry_pkgs")
        registry_pkgs_logger.propagate = False
        registry_pkgs_logger.setLevel(numeric_level)

        if len(registry_pkgs_logger.handlers) == 0:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(self.log_format))
            registry_pkgs_logger.addHandler(handler)
