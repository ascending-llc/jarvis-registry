from registry_pkgs.oauth.oauth_service import MCPOAuthService


class MCPService:
    """Application-scoped OAuth service facade."""

    def __init__(self, oauth_service: MCPOAuthService) -> None:
        self.oauth_service = oauth_service

    async def initialize(self) -> None:
        await self.oauth_service.flow_manager.initialize()
