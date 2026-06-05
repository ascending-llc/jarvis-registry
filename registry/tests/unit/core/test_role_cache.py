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
async def test_load_role_cache_rejects_duplicate_key(mock_role):
    """Two roles sharing resourceType+permBits fail loudly at startup."""
    roles = [_role("workflow", RoleBits.VIEWER), _role("workflow", RoleBits.VIEWER)]
    mock_role.find.return_value.to_list = AsyncMock(return_value=roles)

    container = RegistryContainer.__new__(RegistryContainer)
    with pytest.raises(RuntimeError, match="Duplicate ACL role"):
        await container._load_role_cache()
