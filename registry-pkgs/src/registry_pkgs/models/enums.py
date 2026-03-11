from enum import StrEnum


class ToolDiscoveryMode(StrEnum):
    """Tool discovery mode enumeration"""

    EXTERNAL = "external"
    EMBEDDED = "embedded"


class ServerEntityType(StrEnum):
    """Entity type enumeration for vector documents"""

    SERVER = "server"
    TOOL = "tool"
    RESOURCE = "resource"
    PROMPT = "prompt"


class PermissionBits:
    VIEW = 1  # 0001
    EDIT = 2  # 0010
    DELETE = 4  # 0100
    SHARE = 8  # 1000


class RoleBits:
    VIEWER = PermissionBits.VIEW  # 1
    EDITOR = PermissionBits.VIEW | PermissionBits.EDIT  # 3
    MANAGER = PermissionBits.VIEW | PermissionBits.EDIT | PermissionBits.DELETE  # 7
    OWNER = PermissionBits.VIEW | PermissionBits.EDIT | PermissionBits.DELETE | PermissionBits.SHARE  # 15


class FederationSource(StrEnum):
    AGENTCORE = "agentcore"
    ANTHROPIC = "anthropic"
    ASOR = "asor"


class OAuthProviderType(StrEnum):
    COGNITO = "cognito"
    AUTH0 = "auth0"
    OKTA = "okta"
    ENTRA_ID = "entra_id"
    CUSTOM_OAUTH2 = "custom"
