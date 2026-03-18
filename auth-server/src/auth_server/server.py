"""
Simplified Authentication server that validates JWT tokens against Amazon Cognito.
Configuration is passed via headers instead of environment variables.
"""

import argparse
import logging
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import database utilities
from registry_pkgs.database import close_mongodb, init_mongodb
from registry_pkgs.telemetry import setup_metrics

from .core.config import settings

# Import provider factory
from .providers.factory import get_auth_provider

# Import root-level authorize endpoint
from .routes.authorize import router as authorize_router

# Import internal-only routes
from .routes.internal import router as internal_router

# Import consolidated OAuth routes (device flow + auth code PKCE)
from .routes.oauth_flow import router as oauth_flow_router

# Import .well-known routes
from .routes.well_known import router as well_known_router

# Import validator service (moved out of server.py)
from .services.cognito_validator_service import SimplifiedCognitoValidator

# Instantiate a default validator (main() may replace region)
validator = SimplifiedCognitoValidator()

# Configure logging
settings.configure_logging()

logger = logging.getLogger(__name__)

# Configuration for token generation (from settings)
JWT_ISSUER = settings.jwt_issuer
JWT_AUDIENCE = settings.jwt_audience
JWT_SELF_SIGNED_KID = settings.jwt_self_signed_kid
MAX_TOKEN_LIFETIME_HOURS = settings.max_token_lifetime_hours
DEFAULT_TOKEN_LIFETIME_HOURS = settings.default_token_lifetime_hours

# Rate limiting for token generation (simple in-memory counter)
user_token_generation_counts = {}
MAX_TOKENS_PER_USER_PER_HOUR = settings.max_tokens_per_user_per_hour

from .utils.security_mask import hash_username


def check_rate_limit(username: str) -> bool:
    """
    Check if user has exceeded token generation rate limit.

    Args:
        username: Username to check

    Returns:
        True if under rate limit, False if exceeded
    """
    current_time = int(time.time())
    current_hour = current_time // 3600

    # Clean up old entries (older than 1 hour)
    keys_to_remove = []
    for key in user_token_generation_counts:
        stored_hour = int(key.split(":")[1])
        if current_hour - stored_hour > 1:
            keys_to_remove.append(key)

    for key in keys_to_remove:
        del user_token_generation_counts[key]

    # Check current hour count
    rate_key = f"{username}:{current_hour}"
    current_count = user_token_generation_counts.get(rate_key, 0)

    if current_count >= MAX_TOKENS_PER_USER_PER_HOUR:
        logger.warning(f"Rate limit exceeded for user {hash_username(username)}: {current_count} tokens this hour")
        return False

    # Increment counter
    user_token_generation_counts[rate_key] = current_count + 1
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle management."""
    logger.info("🚀 Starting Auth Server...")

    try:
        # Initialize MongoDB connection
        logger.info("🗄️  Initializing MongoDB connection...")
        await init_mongodb(settings.mongo_config)
        logger.info("✅ MongoDB connection established")
        logger.info("✅ Auth server initialized successfully!")

    except Exception as e:
        logger.error(f"❌ Failed to initialize services: {e}", exc_info=True)
        raise

    # Application is ready
    yield

    # Shutdown tasks
    logger.info("🔄 Shutting down Auth Server...")
    try:
        # Close MongoDB connection
        logger.info("🗄️  Closing MongoDB connection...")
        await close_mongodb()
        logger.info("✅ Shutdown completed successfully!")
    except Exception as e:
        logger.error(f"❌ Error during shutdown: {e}", exc_info=True)


# Create FastAPI app
api_prefix = settings.auth_server_api_prefix.rstrip("/") if settings.auth_server_api_prefix else ""
logger.info(f"Auth server API prefix: '{api_prefix}'")

app = FastAPI(
    title="Jarvis Auth Server",
    description="Authentication server to integrate with Identity Providers like Cognito, Keycloak, Entra ID",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=f"{api_prefix}/docs" if api_prefix else "/docs",
    redoc_url=f"{api_prefix}/redoc" if api_prefix else "/redoc",
    openapi_url=f"{api_prefix}/openapi.json" if api_prefix else "/openapi.json",
)

logger.info("🔭 Initializing Telemetry...")
try:
    setup_metrics("auth-server", settings.telemetry_config)
except Exception as e:
    logger.warning(f"Failed to initialize telemetry: {e}")


# Add CORS middleware to support browser-based OAuth clients (like Claude Desktop)
# Parse CORS origins from settings (comma-separated list or "*")
cors_origins_list = (
    [origin.strip() for origin in settings.cors_origins.split(",")] if settings.cors_origins != "*" else ["*"]
)
logger.info(f"CORS origins configured: {cors_origins_list}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["WWW-Authenticate", "X-User-Id", "X-Username", "X-Client-Id", "X-Scopes", "X-Jarvis-Auth"],
)

# Include .well-known routes at root level (for mcp-remote RFC 8414 compliance)
# mcp-remote strips path when building /.well-known/oauth-authorization-server URL /authorize
app.include_router(well_known_router, prefix="", tags=["well-known-root"])
app.include_router(authorize_router, prefix="", tags=["authorize-root"])

# Include consolidated OAuth routes with prefix
app.include_router(oauth_flow_router, prefix=api_prefix)

# Include internal-only routes (mounted under the same API prefix)
app.include_router(internal_router, prefix=api_prefix)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "simplified-auth-server"}


@app.get(f"{api_prefix}/config")
async def get_auth_config():
    """Return the authentication configuration info"""
    try:
        auth_provider = get_auth_provider()
        provider_info = auth_provider.get_provider_info()

        if provider_info.get("provider_type") == "keycloak":
            return {
                "auth_type": "keycloak",
                "description": "Keycloak JWT token validation",
                "required_headers": ["Authorization: Bearer <token>"],
                "optional_headers": [],
                "provider_info": provider_info,
            }
        else:
            return {
                "auth_type": "cognito",
                "description": "Header-based Cognito token validation",
                "required_headers": [
                    "Authorization: Bearer <token>",
                    "X-User-Pool-Id: <pool_id>",
                    "X-Client-Id: <client_id>",
                ],
                "optional_headers": ["X-Region: <region> (default: us-east-1)"],
                "provider_info": provider_info,
            }
    except Exception as e:
        logger.error(f"Error getting auth config: {e}")
        return {"auth_type": "unknown", "description": f"Error getting provider config: {e}", "error": str(e)}


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Simplified Auth Server")

    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host for the server to listen on (default: 0.0.0.0)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8888,
        help="Port for the server to listen on (default: 8888)",
    )

    parser.add_argument(
        "--region",
        type=str,
        default="us-east-1",
        help="Default AWS region (default: us-east-1)",
    )

    return parser.parse_args()


# TODO: This function is completely skipped in the dockerfile.
def main():
    """Run the server"""
    args = parse_arguments()

    # Update global validator with default region
    global validator
    validator = SimplifiedCognitoValidator(region=args.region)

    logger.info(f"Starting simplified auth server on {args.host}:{args.port}")
    logger.info(f"Default region: {args.region}")

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
