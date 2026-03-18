"""
Workday ASOR (Agent Service Operating Registry) federation client.

Fetches agent configurations from Workday ASOR API and transforms them
to the gateway's internal format.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from ...schemas.federation_schema import AsorAgentConfig
from .base_client import BaseFederationClient

# Get logger - logging is configured centrally in main.py via settings.configure_logging()
logger = logging.getLogger(__name__)


class AsorFederationClient(BaseFederationClient):
    """Client for fetching agents from Workday ASOR."""

    def __init__(
        self,
        endpoint: str,
        auth_type: str = "oauth2",
        access_token: str | None = None,
        client_credentials: str | None = None,
        tenant_url: str | None = None,
        timeout_seconds: int = 30,
        retry_attempts: int = 3,
    ):
        """
        Initialize ASOR federation client.

        Args:
            endpoint: Base URL for ASOR API
            auth_type: Authentication type (oauth2, api-key)
            access_token: Pre-obtained access token
            client_credentials: Credentials in client_id:client_secret format
            tenant_url: Workday tenant URL (for authentication)
            timeout_seconds: HTTP request timeout
            retry_attempts: Number of retry attempts
        """
        super().__init__(endpoint, timeout_seconds, retry_attempts)
        self.auth_type = auth_type
        self.access_token = access_token
        self.client_credentials = client_credentials
        self.tenant_url = tenant_url
        self._access_token: str | None = None
        self._token_expiry: datetime | None = None

    def _get_access_token(self) -> str | None:
        """
        Get or refresh OAuth2 access token from Workday.

        Returns:
            Access token or None if authentication fails
        """
        # Always check for pre-obtained access token first (for 3LO scenarios)
        if self.access_token:
            logger.info("Using pre-obtained ASOR access token from settings")
            self._access_token = self.access_token
            # Set a reasonable expiry (1 hour from now)
            self._token_expiry = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)
            return self._access_token

        # Check if we have a valid cached token (only for client credentials)
        if self._access_token and self._token_expiry and datetime.now(UTC) < self._token_expiry:
            logger.debug("Using cached access token")
            return self._access_token

        # Get credentials from environment
        if self.client_credentials:
            credentials = self.client_credentials
            try:
                parts = credentials.split(":")
                if len(parts) >= 2:
                    client_id, client_secret = parts[0], parts[1]
                else:
                    raise ValueError("Invalid credentials format")
                try:
                    import base64

                    decoded_client_id = base64.b64decode(client_id).decode("utf-8")
                    client_id = decoded_client_id
                    logger.info(f"Decoded base64 client_id: {client_id}")
                except Exception:
                    logger.info(f"Using original client_id: {client_id}")
            except ValueError:
                logger.error("ASOR credentials must be in format 'client_id:client_secret'")
                return None
        else:
            logger.error("No client credentials configured for ASOR")
            return None

        # Request token from Workday - use tenant-specific URL
        token_url = "https://wcpdev-services1.wd103.myworkday.com/ccx/oauth2/awsasor_wcpdev1/token"

        logger.info(f"Requesting access token from Workday: {token_url}")

        # Use Basic Auth like agentcore integration
        import base64

        credentials = f"{client_id}:{client_secret}"
        credentials_b64 = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {credentials_b64}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        data = {"grant_type": "client_credentials"}

        try:
            response = self.client.post(token_url, data=data, headers=headers)
            response.raise_for_status()
            token_data = response.json()

            self._access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in", 3600)

            # Set expiry slightly before actual expiry (5 min buffer)
            self._token_expiry = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=expires_in - 300)

            logger.info(f"Successfully obtained access token (expires in {expires_in}s)")
            return self._access_token

        except Exception as e:
            logger.error(f"Failed to obtain access token via client credentials: {e}")
            logger.info("ASOR typically requires 3-legged OAuth. To use ASOR federation:")
            logger.info("1. Run the test_asor_complete.py script to get an access token")
            logger.info("2. Set the ASOR_ACCESS_TOKEN environment variable with the token")
            logger.info("3. Restart the registry to use the pre-obtained token")
            return None

    def fetch_agent(self, agent_id: str, agent_config: AsorAgentConfig | None = None) -> dict[str, Any] | None:
        """
        Fetch a single agent from ASOR.

        Args:
            agent_id: Agent ID in ASOR
            agent_config: Optional agent configuration

        Returns:
            Agent data dictionary or None if fetch fails
        """
        # Use direct ASOR API endpoint
        url = f"{self.endpoint}/agentDefinition/{agent_id}"

        # Get access token
        access_token = self._get_access_token()
        if not access_token:
            logger.error("Failed to authenticate with Workday")
            return None

        logger.debug(f"Using access token for API call: {access_token[:50]}...")

        # Build headers - match working test script format
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        }

        # Make request
        logger.info(f"Fetching agent {agent_id} from ASOR")
        response = self._make_request(url, headers=headers)

        if not response:
            logger.error(f"Failed to fetch agent {agent_id}")
            return None

        # Transform response to internal format
        return self._transform_agent_response(response, agent_id, agent_config)

    def list_all_agents(self) -> list[dict[str, Any]]:
        """
        List all agent definitions from ASOR.

        Returns:
            List of all agent definitions
        """
        # ASOR API: GET /asor/v1/agentDefinition (singular, per OpenAPI spec)
        url = f"{self.endpoint}/agentDefinition"

        # Get access token
        access_token = self._get_access_token()
        if not access_token:
            logger.error("Failed to authenticate with Workday")
            return []

        logger.info(f"ASOR DEBUG - URL: {url}")
        logger.info(f"ASOR DEBUG - Token (first 50 chars): {access_token[:50]}...")
        logger.info(f"ASOR DEBUG - Endpoint: {self.endpoint}")

        # Build headers - match working test script format
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        }

        logger.info(f"ASOR DEBUG - Headers: {headers}")

        # Make request
        logger.info("Listing all agents from ASOR")
        response = self._make_request(url, method="GET", headers=headers)

        if not response:
            logger.error("Failed to list agents")
            return []

        # Response should be a list of agent definitions or wrapped in data field
        if isinstance(response, dict) and "data" in response:
            agents = response["data"]
            total = response.get("total", len(agents))
            logger.info(f"Found {total} agents in ASOR (from data field)")
        elif isinstance(response, list):
            agents = response
            logger.info(f"Found {len(agents)} agents in ASOR (direct list)")
        else:
            agents = []
            logger.warning(f"Unexpected ASOR response format: {type(response)}")

        return agents

    def fetch_all_agents(self, agent_configs: list[AsorAgentConfig]) -> list[dict[str, Any]]:
        """
        Fetch multiple agents from ASOR.

        Args:
            agent_configs: List of agent configurations

        Returns:
            List of agent data dictionaries
        """
        agents = []

        # If no configs provided, list all agents
        if not agent_configs:
            logger.info("No agent configs provided, listing all agents from ASOR")
            return self.list_all_agents()

        for config in agent_configs:
            agent_data = self.fetch_agent(config.id, config)
            if agent_data:
                agents.append(agent_data)
            else:
                logger.warning(f"Failed to fetch agent: {config.id}")

        logger.info(f"Successfully fetched {len(agents)}/{len(agent_configs)} agents")
        return agents

    def fetch_server(self, server_name: str, **kwargs) -> dict[str, Any] | None:
        """
        Fetch a single server (agent) from ASOR.

        Args:
            server_name: Agent ID
            **kwargs: Additional parameters

        Returns:
            Server data dictionary
        """
        return self.fetch_agent(server_name, kwargs.get("agent_config"))

    def fetch_all_servers(self, server_names: list[str], **kwargs) -> list[dict[str, Any]]:
        """
        Fetch multiple servers (agents) from ASOR.

        Args:
            server_names: List of agent IDs
            **kwargs: Additional parameters

        Returns:
            List of server data dictionaries
        """
        # Convert server names to agent configs
        agent_configs = [AsorAgentConfig(id=name) for name in server_names]
        return self.fetch_all_agents(agent_configs)

    def _transform_agent_response(
        self, response: dict[str, Any], agent_id: str, agent_config: AsorAgentConfig | None
    ) -> dict[str, Any]:
        """
        Transform ASOR API response to internal gateway format.

        Args:
            response: Raw response from ASOR API
            agent_id: Agent ID
            agent_config: Optional agent configuration

        Returns:
            Transformed agent data
        """
        # Extract agent details from response
        # Note: Adjust field names based on actual ASOR API response structure
        name = response.get("name", agent_id)
        description = response.get("description", "")
        version = response.get("version", "1.0.0")

        # Extract endpoint/URL
        endpoint = response.get("endpoint") or response.get("url")

        # Extract capabilities
        capabilities = response.get("capabilities", [])
        tools = response.get("tools", [])

        # Generate tags
        tags = ["asor", "workday", "federated"]
        # Build transformed agent object
        transformed = {
            "source": "asor",
            "server_name": f"asor/{agent_id}",
            "description": description,
            "version": version,
            "title": name,
            "proxy_pass_url": endpoint,
            "transport_type": "streamable-http",  # Assume HTTP transport
            "requires_auth": True,  # ASOR agents likely require auth
            "auth_headers": [],  # Auth handled by gateway
            "tags": tags,
            "metadata": {
                "original_response": response,
                "agent_id": agent_id,
                "capabilities": capabilities,
                "tools": tools,
                "config_metadata": {},
            },
            "cached_at": datetime.now(UTC).isoformat(),
            "is_read_only": True,
            "attribution_label": "ASOR",
            # Additional fields for compatibility
            "path": f"/asor-{agent_id}",
            "is_enabled": True,
            "health_status": "unknown",
            "num_tools": len(tools) if tools else 0,
        }

        return transformed


# Import timedelta for token expiry calculation
