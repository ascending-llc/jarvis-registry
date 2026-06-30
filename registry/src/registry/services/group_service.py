import logging

from registry_pkgs.models import Group

logger = logging.getLogger(__name__)


class GroupService:
    async def search_groups(self, query: str, limit: int = 30) -> list[Group]:
        """
        Search groups by name or email (case-insensitive substring match).
        Returns a list of Group instances representing matching groups (e.g., with id, name, email fields).
        """
        try:
            search_query = {
                "$or": [
                    {"name": {"$regex": query, "$options": "i"}},
                    {"email": {"$regex": query, "$options": "i"}},
                ]
            }
            results = await Group.find(search_query).limit(limit).to_list()
            return results
        except Exception as e:
            logger.error(f"Error searching groups with query '{search_query}': {e}")
            return []
