import logging

from registry_pkgs.models import IGroup

logger = logging.getLogger(__name__)


class GroupService:
    async def search_groups(self, query: str) -> list[IGroup]:
        """
        Search groups by name or email (case-insensitive substring match).
        Returns a list of IGroup instances representing matching groups (e.g., with id, name, email fields).
        """
        try:
            search_query = {
                "$or": [
                    {"name": {"$regex": query, "$options": "i"}},
                    {"email": {"$regex": query, "$options": "i"}},
                ]
            }
            results = await IGroup.find(search_query).to_list()
            return results
        except Exception as e:
            logger.error(f"Error searching groups with query '{search_query}': {e}")
            return []
