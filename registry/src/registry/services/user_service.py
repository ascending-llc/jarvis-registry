import logging
from datetime import UTC, datetime

from beanie import PydanticObjectId

from registry_pkgs.models import User

logger = logging.getLogger(__name__)


class UserService:
    async def find_by_source_id(self, source_id: str) -> User | None:
        """Find a user by idOnTheSource (Entra ID or similar)."""
        if not source_id:
            logger.warning("No source_id provided to find_by_source_id.")
            return None
        try:
            user = await User.find_one({"idOnTheSource": source_id})
            return user
        except Exception as e:
            logger.error(f"Error finding user by source_id '{source_id}': {e}")
            return None

    async def get_user_by_user_id(self, user_id: str) -> User | None:
        """
        Find a user by user_id
        """
        try:
            try:
                obj_id = PydanticObjectId(user_id)
            except Exception:
                logger.warning(f"Invalid user ID format: {user_id}")
                return None
            user = await User.get(obj_id)
            return user
        except Exception as e:
            logger.error(f"Error finding user by user_id '{user_id}': {e}")
            return None

    async def get_or_create_user(self, email: str) -> User | None:
        """
        Get or create an user
            :param email: email of the user
        """
        user = await User.find_one({"email": email})
        if not user:
            now = datetime.now(UTC)
            # Create user
            user_data = {
                "email": email,
                "emailVerified": False,
                "role": "USER",
                "provider": "local",
                "createdAt": now,
                "updatedAt": now,
            }
            collection = User.get_pymongo_collection()
            await collection.insert_one(user_data)
            logger.info(f"Created user record for token storage: {email}")
            user = await User.find_one({"email": email})
        return user

    async def search_users(self, query: str) -> list[User]:
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
            results = await User.find(search_query).to_list()
            return results
        except Exception as e:
            logger.error(f"Error searching users with query '{search_query}': {e}")
            return []

    async def create_user(self, user_claims: dict) -> User | None:
        """
        Create a new user in MongoDB.

        Args:
            user_claims: Dictionary containing user information (name, username, email, idp_id)

        Returns:
            user_id as string if created, None on error
        """
        try:
            new_user = User(
                name=user_claims.get("name"),
                username=user_claims.get("sub"),
                email=user_claims.get("sub"),
                emailVerified=True,
                role="USER",
                provider="openid",
                openidId="",
                idOnTheSource=user_claims.get("idp_id"),
                plugins=[],
                termsAccepted=False,
                backupCodes=[],
                refreshToken=[],
                favorites=[],
                createdAt=datetime.now(UTC),
                updatedAt=datetime.now(UTC),
            )

            created_user = await new_user.create()
            logger.info(
                f"Created new user record in MongoDB with id: {created_user.id} for username: {user_claims.get('username')}"
            )
            return created_user
        except Exception as e:
            logger.error(f"Error creating new user for username: {user_claims.get('username')}: {e}")
            return None
