from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.container import RegistryContainer
from registry_pkgs.models.enums import RoleBits


def _role(resource_type: str, perm_bits: int) -> MagicMock:
    return MagicMock(resourceType=resource_type, permBits=perm_bits, id=PydanticObjectId())


@pytest.mark.asyncio
@patch("registry.container.ExtendedAccessRole")
async def test_load_role_cache_builds_unique_map(mock_role):
    """_load_role_cache keys roles by (resourceType, permBits) -> ObjectId."""
    roles = [_role("mcpServer", RoleBits.VIEWER), _role("workflow", RoleBits.OWNER)]
    mock_role.find.return_value.to_list = AsyncMock(return_value=roles)

    container = RegistryContainer.__new__(RegistryContainer)
    cache = await container._load_role_cache()

    assert cache[("mcpServer", RoleBits.VIEWER)] == roles[0].id
    assert cache[("workflow", RoleBits.OWNER)] == roles[1].id


@pytest.mark.asyncio
@patch("registry.container.ExtendedAccessRole")
async def test_load_role_cache_skips_duplicate_key(mock_role):
    """A duplicate resourceType+permBits is skipped (first wins), not fatal.

    The catalog is partly maintained externally (Jarvis Chat); a duplicate must not
    crash registry startup.
    """
    first = _role("workflow", RoleBits.VIEWER)
    second = _role("workflow", RoleBits.VIEWER)
    mock_role.find.return_value.to_list = AsyncMock(return_value=[first, second])

    container = RegistryContainer.__new__(RegistryContainer)
    # Does not raise; first role wins and the duplicate is dropped.
    cache = await container._load_role_cache()

    assert cache[("workflow", RoleBits.VIEWER)] == first.id
    assert len(cache) == 1
