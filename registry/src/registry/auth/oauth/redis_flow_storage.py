import json
import logging
import time

from redis import Redis

from ...core.config import settings
from ...schemas.enums import OAuthFlowStatus
from ...schemas.oauth_schema import MCPOAuthFlowMetadata, OAuthFlow, OAuthTokens

logger = logging.getLogger(__name__)


class RedisFlowStorage:
    """
    Redis storage for OAuth flows
    """

    DEFAULT_TTL = 600  # 10 minutes

    def __init__(self, redis_client: Redis):
        """
        Initialize storage with Redis client
        """
        self.redis = redis_client
        self.key_prefix = f"{settings.redis_key_prefix}:oauth_flow:flow:"

    def _make_key(self, flow_id: str) -> str:
        """Generate Redis key for flow"""
        return f"{self.key_prefix}{flow_id}"

    def save_flow(self, flow: OAuthFlow, ttl: int = DEFAULT_TTL) -> bool:
        """
        Save OAuth flow to Redis
        """
        try:
            key = self._make_key(flow.flow_id)

            # Serialize flow to dictionary
            data = {
                "flow_id": flow.flow_id,
                "server_id": flow.server_id,
                "user_id": flow.user_id,
                "code_verifier": flow.code_verifier,
                "state": flow.state,
                "status": flow.status.value,  # Convert enum to string value
                "created_at": str(flow.created_at),
                "completed_at": str(flow.completed_at) if flow.completed_at else "",
                "error": flow.error or "",
            }

            # Serialize complex objects as JSON
            if flow.tokens:
                data["tokens_json"] = json.dumps(flow.tokens.dict())
            else:
                data["tokens_json"] = ""

            if flow.metadata:
                data["metadata_json"] = flow.metadata.model_dump_json()
            else:
                data["metadata_json"] = ""

            pipe = self.redis.pipeline()
            pipe.hmset(key, data)  # hmset works with older Redis versions
            pipe.expire(key, ttl)
            pipe.execute()

            logger.debug(f"Saved flow to Redis: {flow.flow_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to save flow to Redis: {e}", exc_info=True)
            return False

    def get_flow(self, flow_id: str) -> OAuthFlow | None:
        """
        Get OAuth flow from Redis
        """
        try:
            key = self._make_key(flow_id)
            data = self.redis.hgetall(key)

            if not data:
                return None

            # Deserialize tokens
            tokens = None
            if data.get("tokens_json"):
                tokens_dict = json.loads(data["tokens_json"])
                tokens = OAuthTokens(**tokens_dict)

            # Deserialize metadata
            metadata = None
            if data.get("metadata_json"):
                metadata_dict = json.loads(data["metadata_json"])
                metadata = MCPOAuthFlowMetadata(**metadata_dict)

            # Reconstruct OAuthFlow
            return OAuthFlow(
                flow_id=data["flow_id"],
                server_id=data["server_id"],
                server_name=metadata.server_name,
                user_id=data["user_id"],
                code_verifier=data["code_verifier"],
                state=data["state"],
                status=OAuthFlowStatus(data["status"]),  # Convert string back to enum
                created_at=float(data["created_at"]),
                completed_at=float(data["completed_at"]) if data.get("completed_at") else None,
                tokens=tokens,
                error=data.get("error") or None,
                metadata=metadata,
            )

        except Exception as e:
            logger.error(f"Failed to get flow from Redis: {e}", exc_info=True)
            return None

    def delete_flow(self, flow_id: str) -> bool:
        """
        Delete OAuth flow from Redis
        """
        try:
            key = self._make_key(flow_id)
            result = self.redis.delete(key)
            return result > 0
        except Exception as e:
            logger.error(f"Failed to delete flow from Redis: {e}")
            return False

    def find_flows(self, user_id: str, server_id: str) -> list[OAuthFlow]:
        """
        Find flows by user_id and server_id
        """
        try:
            pattern = f"{self.key_prefix}*"
            flows = []

            # Scan all flow keys
            for key in self.redis.scan_iter(match=pattern, count=100):
                flow = self.get_flow(key.decode() if isinstance(key, bytes) else key.replace(self.key_prefix, ""))
                if flow and flow.user_id == user_id and flow.server_id == server_id:
                    flows.append(flow)

            return flows

        except Exception as e:
            logger.error(f"Failed to find flows in Redis: {e}")
            return []

    def cleanup_expired(self, ttl: int = DEFAULT_TTL) -> int:
        """
        Clean up expired flows

        """
        try:
            pattern = f"{self.key_prefix}*"
            cleaned = 0
            current_time = time.time()

            for key in self.redis.scan_iter(match=pattern, count=100):
                data = self.redis.hgetall(key)
                if data and data.get("created_at"):
                    created_at = float(data["created_at"])
                    if current_time - created_at > ttl:
                        self.redis.delete(key)
                        cleaned += 1

            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} expired flows")

            return cleaned

        except Exception as e:
            logger.error(f"Failed to cleanup expired flows: {e}")
            return 0
