"""
Test data factories for generating mock data.
"""

from typing import Any

import factory
from faker import Faker

fake = Faker()


class ServerInfoFactory(factory.DictFactory):
    """Factory for creating server info dictionaries."""

    server_name = factory.LazyFunction(lambda: fake.company())
    description = factory.LazyFunction(lambda: fake.text(max_nb_chars=200))
    path = factory.LazyFunction(lambda: fake.slug())  # Path is now in slug format (no slashes)
    proxy_pass_url = factory.LazyFunction(lambda: f"http://localhost:{fake.port_number()}")
    tags = factory.LazyFunction(lambda: fake.words(nb=3))
    num_tools = factory.LazyFunction(lambda: fake.random_int(min=0, max=20))
    num_stars = factory.LazyFunction(lambda: fake.random_int(min=0, max=100))
    is_python = factory.LazyFunction(lambda: fake.boolean())
    license = factory.LazyFunction(
        lambda: fake.random_element(elements=["MIT", "Apache-2.0", "GPL-3.0", "BSD-3-Clause", "N/A"])
    )
    tool_list = factory.LazyFunction(lambda: [])


class ToolInfoFactory(factory.DictFactory):
    """Factory for creating tool info dictionaries."""

    name = factory.LazyFunction(lambda: fake.word())
    description = factory.LazyFunction(lambda: fake.sentence())
    input_schema = factory.LazyFunction(
        lambda: {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The query parameter"}},
            "required": ["query"],
        }
    )


class HealthStatusFactory(factory.DictFactory):
    """Factory for creating health status dictionaries."""

    status = factory.LazyFunction(lambda: fake.random_element(elements=["healthy", "unhealthy", "unknown"]))
    last_checked_iso = factory.LazyFunction(lambda: fake.iso8601())
    num_tools = factory.LazyFunction(lambda: fake.random_int(min=0, max=20))


class UserFactory(factory.DictFactory):
    """Factory for creating user dictionaries."""

    username = factory.LazyFunction(lambda: fake.user_name())
    email = factory.LazyFunction(lambda: fake.email())
    is_admin = factory.LazyFunction(lambda: fake.boolean())


def create_server_with_tools(num_tools: int = 3) -> dict[str, Any]:
    """Create a server with a specified number of tools."""
    server = ServerInfoFactory()
    server["num_tools"] = num_tools
    server["tool_list"] = [ToolInfoFactory() for _ in range(num_tools)]
    return server


def create_multiple_servers(count: int = 5) -> dict[str, dict[str, Any]]:
    """Create multiple servers indexed by path."""
    servers = {}
    for _ in range(count):
        server = ServerInfoFactory()
        servers[server["path"]] = server
    return servers


def create_health_data(service_paths: list[str]) -> dict[str, dict[str, Any]]:
    """Create health status data for multiple services."""
    health_data = {}
    for path in service_paths:
        health_data[path] = HealthStatusFactory()
    return health_data
