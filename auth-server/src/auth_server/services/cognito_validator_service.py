"""
Service module for Cognito token validation.
"""

import logging

import boto3
import httpx
from botocore.exceptions import ClientError

from registry_pkgs.core.jwt_utils import (
    ExpiredSignatureError,
    InvalidTokenError,
    decode_jwt,
    decode_jwt_unverified,
    decode_jwt_with_jwk,
    get_token_unverified_header,
)

from ..core.config import settings
from ..utils.security_mask import hash_username

logger = logging.getLogger(__name__)


class SimplifiedCognitoValidator:
    """
    Simplified Cognito token validator that doesn't rely on environment variables.
    """

    def __init__(self, region: str = "us-east-1"):
        self.default_region = region
        self._cognito_clients = {}
        self._jwks_cache = {}

    def _get_cognito_client(self, region: str):
        if region not in self._cognito_clients:
            self._cognito_clients[region] = boto3.client("cognito-idp", region_name=region)
        return self._cognito_clients[region]

    async def _get_jwks(self, user_pool_id: str, region: str) -> dict:
        cache_key = f"{region}:{user_pool_id}"
        if cache_key not in self._jwks_cache:
            try:
                issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
                jwks_url = f"{issuer}/.well-known/jwks.json"
                async with httpx.AsyncClient() as client:
                    response = await client.get(jwks_url, timeout=10)
                    response.raise_for_status()
                    jwks = response.json()
                self._jwks_cache[cache_key] = jwks
                logger.debug(f"Retrieved JWKS for {cache_key} with {len(jwks.get('keys', []))} keys")
            except Exception as e:
                logger.error(f"Failed to retrieve JWKS from {jwks_url}: {e}")
                raise ValueError(f"Cannot retrieve JWKS: {e}")
        return self._jwks_cache[cache_key]

    async def validate_jwt_token(
        self, access_token: str, user_pool_id: str, client_id: str, region: str = None
    ) -> dict:
        if not region:
            region = self.default_region
        try:
            unverified_header = get_token_unverified_header(access_token)
            kid = unverified_header.get("kid")
            if not kid:
                raise ValueError("Token missing 'kid' in header")

            jwks = await self._get_jwks(user_pool_id, region)
            matching_key = None
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    matching_key = key
                    break

            if not matching_key:
                raise ValueError(f"No matching key found for kid: {kid}")

            issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
            claims = decode_jwt_with_jwk(
                access_token,
                matching_key,
                algorithms=["RS256"],
                issuer=issuer,
                audience=None,  # Cognito access tokens don't carry a standard aud
            )

            token_use = claims.get("token_use")
            if token_use not in ["access", "id"]:
                raise ValueError(f"Invalid token_use: {token_use}")

            token_client_id = claims.get("client_id")
            if token_client_id and token_client_id != client_id:
                logger.warning(f"Token issued for different client: {token_client_id} vs expected {client_id}")

            logger.info("Successfully validated JWT token for client/user")
            return claims
        except ExpiredSignatureError:
            error_msg = "Token has expired"
            logger.warning(error_msg)
            raise ValueError(error_msg)
        except InvalidTokenError as e:
            error_msg = f"Invalid token: {e}"
            logger.warning(error_msg)
            raise ValueError(error_msg)
        except Exception as e:
            error_msg = f"JWT validation error: {e}"
            logger.error(error_msg)
            raise ValueError(f"Token validation failed: {e}")

    def validate_with_boto3(self, access_token: str, region: str = None) -> dict:
        if not region:
            region = self.default_region
        try:
            cognito_client = self._get_cognito_client(region)
            response = cognito_client.get_user(AccessToken=access_token)
            user_attributes = {}
            for attr in response.get("UserAttributes", []):
                user_attributes[attr["Name"]] = attr["Value"]

            result = {
                "username": response.get("Username"),
                "user_attributes": user_attributes,
                "user_status": response.get("UserStatus"),
                "token_use": "access",
                "auth_method": "boto3",
            }
            logger.info(f"Successfully validated token via boto3 for user {hash_username(result['username'])}")
            return result
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            if error_code == "NotAuthorizedException":
                error_msg = "Invalid or expired access token"
                logger.warning(f"Cognito error {error_code}: {error_message}")
                raise ValueError(error_msg)
            elif error_code == "UserNotFoundException":
                error_msg = "User not found"
                logger.warning(f"Cognito error {error_code}: {error_message}")
                raise ValueError(error_msg)
            else:
                logger.error(f"Cognito error {error_code}: {error_message}")
                raise ValueError(f"Token validation failed: {error_message}")
        except Exception as e:
            logger.error(f"Boto3 validation error: {e}")
            raise ValueError(f"Token validation failed: {e}")

    def validate_self_signed_token(self, access_token: str) -> dict:
        try:
            claims = decode_jwt(
                access_token,
                settings.jwt_public_key,
                issuer=settings.jwt_issuer,
                audience=settings.jwt_audience,
            )

            token_use = claims.get("token_use")
            if token_use != "access":
                raise ValueError(f"Invalid token_use: {token_use}")

            scope_string = claims.get("scope", "")
            scopes = scope_string.split() if scope_string else []

            logger.info(f"Successfully validated self-signed token for user: {claims.get('sub')}")

            return {
                "valid": True,
                "method": "self_signed",
                "data": claims,
                "client_id": claims.get("client_id", "user-generated"),
                "username": claims.get("sub", ""),
                "expires_at": claims.get("exp"),
                "scopes": scopes,
                "groups": [],
                "token_type": "user_generated",
            }
        except ExpiredSignatureError:
            error_msg = "Self-signed token has expired"
            logger.warning(error_msg)
            raise ValueError(error_msg)
        except InvalidTokenError as e:
            error_msg = f"Invalid self-signed token: {e}"
            logger.warning(error_msg)
            raise ValueError(error_msg)
        except Exception as e:
            error_msg = f"Self-signed token validation error: {e}"
            logger.error(error_msg)
            raise ValueError(f"Self-signed token validation failed: {e}")

    async def validate_token(self, access_token: str, user_pool_id: str, client_id: str, region: str = None) -> dict:
        if not region:
            region = self.default_region
        try:
            unverified_claims = decode_jwt_unverified(access_token)
            if unverified_claims.get("iss") == settings.jwt_issuer:
                logger.debug("Token appears to be self-signed, validating...")
                return self.validate_self_signed_token(access_token)
        except Exception:
            logger.exception("failed to decode jwt token")

            pass

        try:
            jwt_claims = await self.validate_jwt_token(access_token, user_pool_id, client_id, region)
            scopes = []
            if "scope" in jwt_claims:
                scopes = jwt_claims["scope"].split() if jwt_claims["scope"] else []

            return {
                "valid": True,
                "method": "jwt",
                "data": jwt_claims,
                "client_id": jwt_claims.get("client_id") or "",
                "username": jwt_claims.get("cognito:username") or jwt_claims.get("username") or "",
                "expires_at": jwt_claims.get("exp"),
                "scopes": scopes,
                "groups": jwt_claims.get("cognito:groups", []),
            }
        except ValueError as jwt_error:
            logger.debug(f"JWT validation failed: {jwt_error}, trying boto3")
            try:
                boto3_data = self.validate_with_boto3(access_token, region)
                return {
                    "valid": True,
                    "method": "boto3",
                    "data": boto3_data,
                    "client_id": "",
                    "username": boto3_data.get("username") or "",
                    "user_attributes": boto3_data.get("user_attributes", {}),
                    "scopes": [],
                    "groups": [],
                }
            except ValueError as boto3_error:
                logger.debug(f"Boto3 validation failed: {boto3_error}")
                raise ValueError(f"All validation methods failed. JWT: {jwt_error}, Boto3: {boto3_error}")
