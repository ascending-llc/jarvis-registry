import logging

from beanie import PydanticObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.models import User

logger = logging.getLogger(__name__)


class UserService:
    async def get_user_by_user_id(
        self,
        user_id: str,
        session: AsyncClientSession | None = None,
    ) -> User | None:
        """
        Find a user by user_id
        """
        try:
            try:
                obj_id = PydanticObjectId(user_id)
            except Exception:
                logger.warning(f"Invalid user ID format: {user_id}")
                return None
            user = await User.get(obj_id, session=session)
            return user
        except Exception as e:
            logger.error(f"Error finding user by user_id '{user_id}': {e}")
            return None

    async def search_users(self, query: str, limit: int = 30) -> list[User]:
        """
        Search users by name, email, or username. Returns User model objects.
        """
        try:
            search_query = {
                "$or": [
                    {"email": {"$regex": query, "$options": "i"}},
                    {"name": {"$regex": query, "$options": "i"}},
                    {"username": {"$regex": query, "$options": "i"}},
                ]
            }
            results = await User.find(search_query).limit(limit).to_list()
            return results
        except Exception as e:
            logger.error(f"Error searching users with query '{search_query}': {e}")
            return []
