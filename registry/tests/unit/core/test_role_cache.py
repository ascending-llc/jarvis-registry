from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.services.access_control_service import load_role_cache
from registry_pkgs.models.enums import RoleBits


def _role(resource_type: str, perm_bits: int) -> MagicMock:
    return MagicMock(resourceType=resource_type, permBits=perm_bits, id=PydanticObjectId())


@pytest.mark.asyncio
@patch("registry.services.access_control_service.ExtendedAccessRole")
async def test_load_role_cache_builds_unique_map(mock_role):
    """load_role_cache keys roles by (resourceType, permBits) -> ObjectId."""
    roles = [_role("mcpServer", RoleBits.VIEWER), _role("workflow", RoleBits.OWNER)]
    mock_role.find.return_value.to_list = AsyncMock(return_value=roles)

    cache = await load_role_cache()

    assert cache[("mcpServer", RoleBits.VIEWER)] == roles[0].id
    assert cache[("workflow", RoleBits.OWNER)] == roles[1].id


@pytest.mark.asyncio
@patch("registry.services.access_control_service.ExtendedAccessRole")
async def test_load_role_cache_filters_to_registry_owned_resource_types(mock_role):
    """The startup query MUST filter to Registry-owned resource types."""
    mock_role.find.return_value.to_list = AsyncMock(return_value=[])

    await load_role_cache()

    mock_role.find.assert_called_once()
    (query,), _ = mock_role.find.call_args
    valid = set(query["resourceType"]["$in"])
    assert {"mcpServer", "remoteAgent", "federation", "workflow"} <= valid
    assert "skill" not in valid
    assert "agent" not in valid


@pytest.mark.asyncio
@patch("registry.services.access_control_service.ExtendedAccessRole")
async def test_load_role_cache_skips_duplicate_key(mock_role):
    """A duplicate resourceType+permBits is skipped (first wins), not fatal.

    The catalog is partly maintained externally (Jarvis Chat); a duplicate must not
    crash registry startup.
    """
    first = _role("workflow", RoleBits.VIEWER)
    second = _role("workflow", RoleBits.VIEWER)
    mock_role.find.return_value.to_list = AsyncMock(return_value=[first, second])

    # Does not raise; first role wins and the duplicate is dropped.
    cache = await load_role_cache()

    assert cache[("workflow", RoleBits.VIEWER)] == first.id
    assert len(cache) == 1


@pytest.mark.asyncio
@patch("registry.services.access_control_service.ExtendedAccessRole")
async def test_load_role_cache_never_crashes_startup(mock_role):
    """A failure loading the catalog must not propagate — the registry must still boot."""
    mock_role.find.return_value.to_list = AsyncMock(side_effect=RuntimeError("boom"))

    cache = await load_role_cache()

    assert cache == {}
