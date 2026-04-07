"""
Service module for Cognito token validation.
"""

import json
import logging

import boto3
import httpx
import jwt
from botocore.exceptions import ClientError
from jwt.api_jwk import PyJWK

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
            unverified_header = jwt.get_unverified_header(access_token)
            kid = unverified_header.get("kid")
            if not kid:
                raise ValueError("Token missing 'kid' in header")

            jwks = await self._get_jwks(user_pool_id, region)
            signing_key = None
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    try:
                        from jwt.algorithms import RSAAlgorithm

                        signing_key = RSAAlgorithm.from_jwk(key)
                    except (ImportError, AttributeError):
                        try:
                            from jwt.algorithms import get_default_algorithms

                            algorithms = get_default_algorithms()
                            signing_key = algorithms["RS256"].from_jwk(key)
                        except (ImportError, AttributeError):
                            signing_key = PyJWK.from_jwk(json.dumps(key)).key
                    break

            if not signing_key:
                raise ValueError(f"No matching key found for kid: {kid}")

            issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
            claims = jwt.decode(
                access_token,
                signing_key,
                algorithms=["RS256"],
                issuer=issuer,
                options={
                    "verify_aud": False,
                    "verify_exp": True,
                    "verify_iat": True,
                },
            )

            token_use = claims.get("token_use")
            if token_use not in ["access", "id"]:
                raise ValueError(f"Invalid token_use: {token_use}")

            token_client_id = claims.get("client_id")
            if token_client_id and token_client_id != client_id:
                logger.warning(f"Token issued for different client: {token_client_id} vs expected {client_id}")

            logger.info("Successfully validated JWT token for client/user")
            return claims
        except jwt.ExpiredSignatureError:
            error_msg = "Token has expired"
            logger.warning(error_msg)
            raise ValueError(error_msg)
        except jwt.InvalidTokenError as e:
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
            claims = jwt.decode(
                access_token,
                settings.secret_key,
                algorithms=["HS256"],
                issuer=settings.jwt_issuer,
                audience=settings.jwt_audience,
                options={"verify_exp": True, "verify_iat": True, "verify_iss": True, "verify_aud": True},
                leeway=30,
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
        except jwt.ExpiredSignatureError:
            error_msg = "Self-signed token has expired"
            logger.warning(error_msg)
            raise ValueError(error_msg)
        except jwt.InvalidTokenError as e:
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
            unverified_claims = jwt.decode(access_token, options={"verify_signature": False})
            if unverified_claims.get("iss") == settings.jwt_issuer:
                logger.debug("Token appears to be self-signed, validating...")
                return self.validate_self_signed_token(access_token)
        except Exception:
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
