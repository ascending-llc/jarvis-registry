"""
Federation service for managing federated registry integrations.

Handles:
- Loading federation configuration
- Syncing servers from federated registries
- Caching federated server data with TTL
- Periodic sync scheduling
"""

import json
import logging
from pathlib import Path
from typing import Any

from ..core.config import settings
from ..schemas.federation_schema import (
    FederationConfig,
)
from .federation.anthropic_client import AnthropicFederationClient
from .federation.asor_client import AsorFederationClient

# Get logger - logging is configured centrally in main.py via settings.configure_logging()
logger = logging.getLogger(__name__)


class FederationService:
    """Service for managing federated registry integrations."""

    def __init__(self, config_path: str | None = None):
        """
        Initialize federation service.

        Args:
            config_path: Path to federation.json config file
        """
        # Set default paths
        if config_path is None:
            config_path = settings.federation_config_path

        self.config_path = config_path

        # Load configuration
        self.config = self._load_config()

        # Initialize clients
        self.anthropic_client: AnthropicFederationClient | None = None
        if self.config.anthropic.enabled:
            self.anthropic_client = AnthropicFederationClient(endpoint=self.config.anthropic.endpoint)

        self.asor_client: AsorFederationClient | None = None
        if self.config.asor.enabled:
            # Extract tenant URL from endpoint or use default
            tenant_url = (
                self.config.asor.endpoint.split("/api")[0]
                if "/api" in self.config.asor.endpoint
                else self.config.asor.endpoint
            )

            self.asor_client = AsorFederationClient(
                endpoint=self.config.asor.endpoint,
                access_token=settings.asor_access_token,
                client_credentials=settings.asor_client_credentials,
                tenant_url=tenant_url,
            )

        logger.info(f"Federation service initialized with config: {config_path}")
        if self.config.is_any_federation_enabled():
            logger.info(f"Enabled federations: {', '.join(self.config.get_enabled_federations())}")
        else:
            logger.info("No federations enabled")

    def _load_config(self) -> FederationConfig:
        """
        Load federation configuration from JSON file.

        Returns:
            FederationConfig instance
        """
        config_file = Path(self.config_path)

        if not config_file.exists():
            logger.warning(f"Federation config not found at {self.config_path}, using defaults")
            return FederationConfig()

        try:
            with open(config_file) as f:
                config_data = json.load(f)

            # Remove JSON comments if present
            config_data.pop("_comment", None)
            config_data.pop("_description", None)

            config = FederationConfig(**config_data)
            logger.info(f"Loaded federation config from {self.config_path}")
            return config

        except Exception as e:
            logger.error(f"Failed to load federation config: {e}")
            return FederationConfig()

    def sync_all(self) -> dict[str, list[dict[str, Any]]]:
        """
        Sync servers from all enabled federated registries.

        Returns:
            Dictionary mapping source name to list of synced servers
        """
        results = {}

        if self.config.anthropic.enabled:
            logger.info("Syncing servers from Anthropic MCP Registry...")
            anthropic_servers = self._sync_anthropic()
            results["anthropic"] = anthropic_servers
            logger.info(f"Synced {len(anthropic_servers)} servers from Anthropic")

        # Sync ASOR agents
        logger.info("Syncing agents from ASOR...")
        asor_agents = self._sync_asor()
        results["asor"] = asor_agents
        logger.info(f"Synced {len(asor_agents)} agents from ASOR")

        return results

    def _sync_anthropic(self) -> list[dict[str, Any]]:
        """
        Sync servers from Anthropic MCP Registry.

        Returns:
            List of synced server data
        """
        if not self.anthropic_client:
            logger.error("Anthropic client not initialized")
            return []

        # Fetch servers
        servers = self.anthropic_client.fetch_all_servers(self.config.anthropic.servers)

        # Save servers as files to external mount
        import json

        for server_data in servers:
            try:
                # Create filename from server name
                server_name = server_data.get("server_name", "unknown-server")
                filename = server_name.replace("/", "-").replace(".", "-") + ".json"
                file_path = settings.servers_dir / filename

                # Save to file
                with open(file_path, "w") as f:
                    json.dump(server_data, f, indent=2)

                # Update server_state.json to enable the server
                server_path = server_data.get("path", f"/{server_name.replace('/', '-')}")
                self._update_server_state(server_path, True)

                logger.info(f"Saved Anthropic server file: {server_name} -> {file_path}")

            except Exception as e:
                logger.error(f"Failed to save Anthropic server {server_data.get('server_name', 'unknown')}: {e}")

        return servers

    def _sync_asor(self) -> list[dict[str, Any]]:
        """
        Sync agents from Workday ASOR.

        NOTE: Legacy file-based agent registration has been removed.
        ASOR agent sync is disabled pending migration to new A2A Agent Management V1 API.

        Returns:
            Empty list (ASOR sync disabled)
        """
        logger.warning(
            "ASOR agent sync is disabled - legacy file-based agent service has been removed. "
            "Please migrate to A2A Agent Management V1 API (/api/v1/agents)"
        )
        return []

    def get_federated_servers(self, source: str | None = None, force_refresh: bool = False) -> list[dict[str, Any]]:
        """
        Get federated servers by syncing from sources.

        Args:
            source: Filter by source (anthropic, asor, etc.) or None for all
            force_refresh: Ignored (always syncs fresh)

        Returns:
            List of federated server data
        """
        servers = []

        if source is None or source == "anthropic":
            servers.extend(self._sync_anthropic())

        if source is None or source == "asor":
            servers.extend(self._sync_asor())

        return servers

    def get_federated_items(
        self, source: str | None = None, force_refresh: bool = False
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Get both federated servers and agents from specified source or all sources.

        Args:
            source: Federation source name (e.g., "anthropic", "asor") or None for all
            force_refresh: Ignored (always syncs fresh)

        Returns:
            Dict with 'servers' and 'agents' keys containing respective federated items
        """
        result = {"servers": [], "agents": []}

        if source is None or source == "anthropic":
            result["servers"].extend(self._sync_anthropic())

        if source is None or source == "asor":
            # ASOR provides agents, not servers
            asor_agents = self._sync_asor()
            result["agents"].extend(asor_agents)

        return result

    def _update_server_state(self, server_path: str, enabled: bool) -> None:
        """
        Update server_state.json to enable/disable a server.

        Args:
            server_path: Server path (e.g., "/ai.klavis-strata")
            enabled: Whether to enable the server
        """
        try:
            from ..core.config import settings

            state_file = settings.servers_dir / "server_state.json"

            # Load existing state
            state = {}
            if state_file.exists():
                with open(state_file) as f:
                    state = json.load(f)

            # Update state
            state[server_path] = enabled

            # Save state
            with open(state_file, "w") as f:
                json.dump(state, f, indent=2)

            logger.info(f"Updated server state: {server_path} = {enabled}")

        except Exception as e:
            logger.error(f"Failed to update server state for {server_path}: {e}")


# Global instance
_federation_service: FederationService | None = None


def get_federation_service() -> FederationService:
    """
    Get global federation service instance (singleton).

    Returns:
        FederationService instance
    """
    global _federation_service

    if _federation_service is None:
        _federation_service = FederationService()

    return _federation_service
