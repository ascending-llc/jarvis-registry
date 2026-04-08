# Microsoft Entra ID Implementation

This document provides technical details about the Microsoft Entra ID (Azure AD) authentication provider implementation in the MCP Gateway Registry.

## Overview

The `EntraIDProvider` class implements the `AuthProvider` interface to provide Microsoft Entra ID (Azure AD) authentication capabilities. It supports OAuth2 authorization code flow, JWT token validation, and integration with Microsoft Graph API.

## Architecture

### Class Hierarchy

```
AuthProvider (Abstract Base Class)
└── EntraIDProvider (Concrete Implementation)
```

### Dependencies

- **Python-jose**: For JWT token validation and decoding
- **Requests**: For HTTP API calls to Microsoft endpoints
- **PyJWT**: For JWT header parsing and key handling

## Implementation Details

### Initialization

The `EntraIDProvider` is initialized with the following parameters:

```python
def __init__(
    self,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    auth_url: str,
    token_url: str,
    jwks_url: str,
    logout_url: str,
    userinfo_url: str,
    graph_url: str,
    m2m_scope: str,
    scopes: Optional[list] = None,
    grant_type: str = "authorization_code",
    username_claim: str = "preferred_username",
    groups_claim: str = "groups",
    email_claim: str = "email",
    name_claim: str = "name"
):
```

**Parameters:**
- `tenant_id`: Azure AD tenant ID (use 'common' for multi-tenant)
- `client_id`: Azure AD application (client) ID
- `client_secret`: Azure AD client secret
- `auth_url`: OAuth2 authorization endpoint URL
- `token_url`: OAuth2 token endpoint URL
- `jwks_url`: JSON Web Key Set endpoint URL
- `logout_url`: Logout endpoint URL
- `userinfo_url`: User info endpoint URL (typically Graph API /me endpoint)
- `graph_url`: Microsoft Graph API base URL (for sovereign clouds)
- `m2m_scope`: Default scope for machine-to-machine authentication
- `scopes`: List of OAuth2 scopes (default: `['openid', 'profile', 'email', 'User.Read']`)
- `grant_type`: OAuth2 grant type (default: `'authorization_code'`)
- `username_claim`: Claim to use for username (default: `'preferred_username'`)
- `groups_claim`: Claim to use for groups (default: `'groups'`)
- `email_claim`: Claim to use for email (default: `'email'`)
- `name_claim`: Claim to use for display name (default: `'name'`)

**Endpoints Configured:**
- All endpoint URLs are explicitly provided via constructor parameters
- This design supports sovereign clouds and custom deployments
- `issuer`: Automatically derived as `https://login.microsoftonline.com/{tenant_id}/v2.0`

### Token Validation

The `validate_token` method performs comprehensive JWT validation:

```python
def validate_token(self, token: str, **kwargs: Any) -> Dict[str, Any]:
```

**Validation Steps:**
1. **JWKS Retrieval**: Fetches JSON Web Key Set from Microsoft with 1-hour caching
2. **Key Matching**: Matches token's `kid` header to the appropriate signing key
3. **JWT Decoding**: Validates using RS256 algorithm with multiple audience checks
4. **Claim Extraction**: Extracts user information from token claims

**Supported Audiences:**
- `client_id` (e.g., `12345678-1234-1234-1234-123456789012`)
- `api://{client_id}` (e.g., `api://12345678-1234-1234-1234-123456789012`)

**User Claim Resolution:**
The implementation uses configurable claim mappings for user information extraction:
- **Username**: Configurable via `username_claim` (default: `preferred_username`)
- **Email**: Configurable via `email_claim` (default: `email`)
- **Groups**: Configurable via `groups_claim` (default: `groups`)
- **Name**: Configurable via `name_claim` (default: `name`)

The implementation handles both string and list claims for groups and falls back to 'sub' claim for username if the configured claim is not found.

### JWKS Caching

The implementation includes intelligent JWKS caching:

```python
self._jwks_cache: Optional[Dict[str, Any]] = None
self._jwks_cache_time: float = 0
self._jwks_cache_ttl: int = 3600  # 1 hour
```

**Features:**
- 1-hour TTL for JWKS cache
- Automatic cache refresh on expiration
- Error handling for JWKS retrieval failures

### OAuth2 Flow Implementation

#### Authorization URL Generation

```python
def get_auth_url(self, redirect_uri: str, state: str, scope: Optional[str] = None) -> str:
```

**Default Scopes:** `openid profile email User.Read`
**Response Mode:** `query`

#### Code Exchange

```python
def exchange_code_for_token(self, code: str, redirect_uri: str) -> Dict[str, Any]:
```

**Request Parameters:**
- `grant_type`: `authorization_code`
- `code`: Authorization code
- `redirect_uri`: Must match the authorization request
- `scope`: `openid profile email User.Read`

#### Token Refresh

```python
def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
```

**Request Parameters:**
- `grant_type`: `refresh_token`
- `refresh_token`: The refresh token
- `scope`: `openid profile email User.Read offline_access`

### User Information Retrieval

The implementation provides flexible user information extraction with automatic fallback mechanisms:

```python
def get_user_info(
    self,
    access_token: str,
    id_token: Optional[str] = None
) -> Dict[str, Any]:
```

**Token Strategy:**

The method supports three extraction strategies controlled by the `ENTRA_TOKEN_KIND` environment variable:

1. **ID Token Extraction (Recommended)**: `ENTRA_TOKEN_KIND=id`
   - Extracts user information from the ID token (OpenID Connect standard)
   - Fast: Local JWT decoding, no network calls
   - Contains standard user claims: username, email, name, groups

2. **Access Token Extraction**: `ENTRA_TOKEN_KIND=access`
   - Extracts user information from the access token
   - Used when ID token is not available
   - May not contain all user claims

3. **Graph API Fallback** (Automatic):
   - Falls back to Microsoft Graph API if token extraction fails
   - Makes HTTP request to `{graph_url}/v1.0/me`
   - Provides complete user profile information

**Returned Fields:**
- `username`: User Principal Name (UPN) or preferred_username
- `email`: Mail address or UPN
- `name`: Display name
- `given_name`: First name (Graph API only)
- `family_name`: Last name (Graph API only)
- `id`: Object ID
- `job_title`: Job title (Graph API only)
- `office_location`: Office location (Graph API only)
- `groups`: List of group display names (from separate Graph API call)

### Group Membership Retrieval

User groups are fetched separately using the Microsoft Graph API:

```python
def get_user_groups(self, access_token: str) -> list:
```

**Graph API Endpoint:** `{graph_url}/v1.0/me/transitiveMemberOf/microsoft.graph.group?$count=true&$select=id,displayName`

**Features:**
- Fetches transitive group memberships (includes nested groups)
- Uses `$count=true` for accurate count metadata
- Uses `$select=id,displayName` to optimize the response payload
- Returns group display names as a list
- Automatically called by `get_user_info()` method
- Handles errors gracefully (returns empty list on failure)

### Machine-to-Machine (M2M) Support

#### M2M Token Generation

```python
def get_m2m_token(
    self,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    scope: Optional[str] = None
) -> Dict[str, Any]:
```

**Client Credentials Flow:**
- `grant_type`: `client_credentials`
- Default scope: Configured via `m2m_scope` parameter (typically `https://graph.microsoft.com/.default`)
- Supports custom client credentials for service accounts
- Sovereign clouds: Scope is automatically adjusted based on `graph_url` configuration

#### M2M Token Validation

```python
def validate_m2m_token(self, token: str) -> Dict[str, Any]:
```

Uses the same validation logic as user tokens with appropriate audience checks.

### Logout Implementation

```python
def get_logout_url(self, redirect_uri: str) -> str:
```

Generates Microsoft Entra ID logout URL with post-logout redirect.

## Configuration

### Provider Configuration (oauth2_providers.yml)

```yaml
entra:
  display_name: "Microsoft Entra ID"
  client_id: "${ENTRA_CLIENT_ID}"
  client_secret: "${ENTRA_CLIENT_SECRET}"
  # Tenant ID can be specific tenant or 'common' for multi-tenant
  tenant_id: "${ENTRA_TENANT_ID}"
  auth_url: "https://login.microsoftonline.com/${ENTRA_TENANT_ID}/oauth2/v2.0/authorize"
  token_url: "https://login.microsoftonline.com/${ENTRA_TENANT_ID}/oauth2/v2.0/token"
  jwks_url: "https://login.microsoftonline.com/${ENTRA_TENANT_ID}/discovery/v2.0/keys"
  user_info_url: "https://graph.microsoft.com/v1.0/me"
  logout_url: "https://login.microsoftonline.com/${ENTRA_TENANT_ID}/oauth2/v2.0/logout"
  scopes: ["openid", "profile", "email", "User.Read"]
  response_type: "code"
  grant_type: "authorization_code"
  # Entra ID specific claim mapping
  username_claim: "${ENTRA_USERNAME_CLAIM}"
  groups_claim: "${ENTRA_GROUPS_CLAIM}"
  email_claim: "${ENTRA_EMAIL_CLAIM}"
  name_claim: "${ENTRA_NAME_CLAIM}"
  # Microsoft Graph API base URL (for sovereign clouds)
  graph_url: "${ENTRA_GRAPH_URL:-https://graph.microsoft.com}"
  # M2M (Machine-to-Machine) default scope
  m2m_scope: "${ENTRA_M2M_SCOPE:-https://graph.microsoft.com/.default}"
  enabled: true
```

### Environment Variables

#### Required Variables

```bash
# Microsoft Entra ID Configuration
ENTRA_CLIENT_ID=your-application-client-id
ENTRA_CLIENT_SECRET=your-client-secret-value
ENTRA_TENANT_ID=your-tenant-id-or-common
```

#### Optional Configuration Variables

```bash
# Token Configuration
# Determines which token to use for extracting user information
# - 'id': Extract user info from ID token (default, recommended)
# - 'access': Extract user info from access token
# If token extraction fails, the system will automatically fallback to Graph API
ENTRA_TOKEN_KIND=id

# Microsoft Graph API Configuration
# For sovereign clouds or custom Graph API endpoints
# Default: https://graph.microsoft.com
ENTRA_GRAPH_URL=https://graph.microsoft.com

# M2M (Machine-to-Machine) Scope Configuration
# Default scope for client credentials flow
# Default: https://graph.microsoft.com/.default
ENTRA_M2M_SCOPE=https://graph.microsoft.com/.default

# Custom Claim Mappings (defaults are shown)
ENTRA_USERNAME_CLAIM=preferred_username
ENTRA_GROUPS_CLAIM=groups
ENTRA_EMAIL_CLAIM=email
ENTRA_NAME_CLAIM=name
```

#### Sovereign Cloud Configuration

For non-global Azure clouds, update `ENTRA_GRAPH_URL` and `ENTRA_M2M_SCOPE`:

```bash
# US Government Cloud
ENTRA_TENANT_ID=your-tenant-id
ENTRA_GRAPH_URL=https://graph.microsoft.us
ENTRA_M2M_SCOPE=https://graph.microsoft.us/.default

# China Cloud (operated by 21Vianet)
ENTRA_TENANT_ID=your-tenant-id
ENTRA_GRAPH_URL=https://microsoftgraph.chinacloudapi.cn
ENTRA_M2M_SCOPE=https://microsoftgraph.chinacloudapi.cn/.default

# Germany Cloud
ENTRA_TENANT_ID=your-tenant-id
ENTRA_GRAPH_URL=https://graph.microsoft.de
ENTRA_M2M_SCOPE=https://graph.microsoft.de/.default
```

## Error Handling

The implementation includes comprehensive error handling:

### Token Validation Errors
- `jwt.ExpiredSignatureError`: Token has expired
- `jwt.InvalidTokenError`: Invalid token structure or signature
- `ValueError`: Missing key ID or no matching key found

### API Call Errors
- `requests.RequestException`: Network or HTTP errors
- Detailed error logging with response bodies for debugging

### JWKS Retrieval Errors
- Fallback handling for JWKS endpoint failures
- Graceful degradation with appropriate error messages

## Logging

The provider uses structured logging with the following patterns:

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
```

**Key Log Events:**
- Token validation successes and failures
- JWKS cache hits and misses
- API call attempts and results
- User authentication events

## Security Features

### Token Security
- **Signature Validation**: Validates token signatures using Microsoft's JWKS
- **Expiration Checking**: Verifies token expiration timestamps
- **Audience Validation**: Checks token audience against client ID
- **Issuer Verification**: Validates token issuer against Microsoft endpoints

### API Security
- **HTTPS Enforcement**: All Microsoft endpoints use HTTPS
- **Client Secret Protection**: Secrets are passed securely in token requests
- **Redirect URI Validation**: Ensures redirect URIs match configured endpoints

### Caching Security
- **JWKS Cache TTL**: 1-hour cache with automatic refresh
- **No Sensitive Data**: Cache only contains public keys

## Performance Considerations

### JWKS Caching
- Reduces API calls to Microsoft endpoints
- 1-hour cache TTL balances performance and security
- Automatic cache refresh prevents stale key usage

### Token Validation
- Efficient key lookup using key ID (kid)
- Supports multiple audience formats for compatibility
- Minimal overhead for token parsing and validation

## Extensibility

### Sovereign Cloud Support

The implementation is fully compatible with sovereign clouds through configuration:

**Azure US Government:**
```bash
ENTRA_TENANT_ID=your-tenant-id
ENTRA_GRAPH_URL=https://graph.microsoft.us
ENTRA_M2M_SCOPE=https://graph.microsoft.us/.default
```

**Azure China (21Vianet):**
```bash
ENTRA_TENANT_ID=your-tenant-id
ENTRA_GRAPH_URL=https://microsoftgraph.chinacloudapi.cn
ENTRA_M2M_SCOPE=https://microsoftgraph.chinacloudapi.cn/.default
```

**Azure Germany:**
```bash
ENTRA_TENANT_ID=your-tenant-id
ENTRA_GRAPH_URL=https://graph.microsoft.de
ENTRA_M2M_SCOPE=https://graph.microsoft.de/.default
```

### Custom Scopes
Easily extendable to support additional Microsoft Graph permissions:

```yaml
scopes: ["openid", "profile", "email", "User.Read", "Mail.Read", "Calendars.Read", "Group.Read.All"]
```

### Multi-Tenant Support
- Use `tenant_id: "common"` for multi-tenant applications
- Use `tenant_id: "organizations"` for organizational accounts only
- Use `tenant_id: "consumers"` for personal Microsoft accounts only
- Automatic tenant discovery and validation

### Token Kind Flexibility
Configure token extraction strategy based on your needs:
- `ENTRA_TOKEN_KIND=id` for standard OpenID Connect flow (recommended)
- `ENTRA_TOKEN_KIND=access` for access token-based extraction
- Automatic fallback to Graph API ensures reliability

## Testing

### Unit Testing
The implementation can be tested with:
- Mock JWKS endpoints
- Mock Microsoft Graph API responses
- Test tokens with known signatures

### Integration Testing
- End-to-end OAuth2 flow testing
- Token validation with real Microsoft endpoints
- Error scenario testing

## Usage Examples

### Basic Authentication Flow

```python
from auth_server.providers.entra import EntraIdProvider
import os

# Set environment variables
os.environ['ENTRA_TOKEN_KIND'] = 'id'  # Use ID token for user info

# Initialize provider with all required parameters
provider = EntraIdProvider(
    tenant_id="your-tenant-id",
    client_id="your-client-id",
    client_secret="your-client-secret",
    auth_url="https://login.microsoftonline.com/your-tenant-id/oauth2/v2.0/authorize",
    token_url="https://login.microsoftonline.com/your-tenant-id/oauth2/v2.0/token",
    jwks_url="https://login.microsoftonline.com/your-tenant-id/discovery/v2.0/keys",
    logout_url="https://login.microsoftonline.com/your-tenant-id/oauth2/v2.0/logout",
    userinfo_url="https://graph.microsoft.com/v1.0/me",
    graph_url="https://graph.microsoft.com",
    m2m_scope="https://graph.microsoft.com/.default"
)

# Generate authorization URL
auth_url = provider.get_auth_url(
    redirect_uri="https://your-app/callback",
    state="security-token"
)

# Exchange code for token
token_data = provider.exchange_code_for_token(
    code="authorization-code",
    redirect_uri="https://your-app/callback"
)

# Get user information (includes groups)
# Pass both access_token and id_token for best results
user_info = provider.get_user_info(
    access_token=token_data["access_token"],
    id_token=token_data.get("id_token")  # Optional but recommended
)

print(f"User: {user_info['username']}")
print(f"Email: {user_info['email']}")
print(f"Groups: {user_info['groups']}")

# Get user groups separately (if needed)
groups = provider.get_user_groups(token_data["access_token"])
```

### Machine-to-Machine Authentication

```python
# Get M2M token using client credentials flow
m2m_token = provider.get_m2m_token()

# Or specify custom scope
m2m_token = provider.get_m2m_token(
    scope="https://graph.microsoft.com/.default"
)

# Validate M2M token
validation_result = provider.validate_m2m_token(m2m_token["access_token"])
```

### Sovereign Cloud Example

```python
import os

# Configure for Azure US Government
os.environ['ENTRA_GRAPH_URL'] = 'https://graph.microsoft.us'
os.environ['ENTRA_M2M_SCOPE'] = 'https://graph.microsoft.us/.default'

provider = EntraIDProvider(
    tenant_id="your-tenant-id",
    client_id="your-client-id",
    client_secret="your-client-secret",
    auth_url="https://login.microsoftonline.us/your-tenant-id/oauth2/v2.0/authorize",
    token_url="https://login.microsoftonline.us/your-tenant-id/oauth2/v2.0/token",
    jwks_url="https://login.microsoftonline.us/your-tenant-id/discovery/v2.0/keys",
    logout_url="https://login.microsoftonline.us/your-tenant-id/oauth2/v2.0/logout",
    userinfo_url="https://graph.microsoft.us/v1.0/me",
    graph_url="https://graph.microsoft.us",
    m2m_scope="https://graph.microsoft.us/.default"
)

# Use provider normally - all Graph API calls will use the sovereign cloud endpoint
user_info = provider.get_user_info(
    access_token=token_data["access_token"],
    id_token=token_data.get("id_token")
)
```

## Troubleshooting

### Common Issues

1. **Token Validation Failures**
   - Check audience and issuer configuration
   - Verify JWKS endpoint accessibility
   - Ensure token hasn't expired
   - Check that `jwks_url` is correctly configured for your tenant

2. **API Permission Errors**
   - Verify delegated permissions are granted
   - Check admin consent for application permissions
   - Validate scope configuration
   - Ensure `Group.Read.All` permission if fetching groups

3. **Multi-Tenant Issues**
   - Ensure app registration allows multi-tenant access
   - Verify tenant ID is set to "common" for multi-tenant apps
   - Check that users are from supported tenant types

4. **Token Kind Configuration Issues**
   - If `ENTRA_TOKEN_KIND=id` but no ID token in response, check OAuth scopes include `openid`
   - System will automatically fallback to access token or Graph API
   - Check logs to see which extraction method was used

5. **Sovereign Cloud Issues**
   - Verify `ENTRA_GRAPH_URL` matches your cloud environment
   - Ensure `ENTRA_M2M_SCOPE` uses the correct Graph API URL
   - Check that all OAuth endpoints (auth_url, token_url, jwks_url) match your cloud
   - Confirm app registration is in the correct cloud tenant

6. **Group Retrieval Failures**
   - Ensure access token has `Group.Read.All` or `Directory.Read.All` permissions
   - Check that user is member of groups in Azure AD
   - Verify Graph API endpoint is accessible
   - Check logs for specific Graph API error messages

### Debug Mode

Enable debug logging for detailed troubleshooting:

```python
import logging
logging.getLogger().setLevel(logging.DEBUG)
```

## References

- [Microsoft Identity Platform Documentation](https://docs.microsoft.com/en-us/azure/active-directory/develop/)
- [Microsoft Graph API Reference](https://docs.microsoft.com/en-us/graph/api/overview)
- [OAuth 2.0 Authorization Code Flow](https://oauth.net/2/grant-types/authorization-code/)
