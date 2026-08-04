"""Tests for A2A agent transport filtering and write validation."""

from unittest.mock import Mock

import pytest
from a2a.types import AgentCard, AgentInterface, TransportProtocol
from beanie import Insert, PydanticObjectId, Replace, Save, SaveChanges, Update
from beanie.odm.actions import ActionDirections, ActionRegistry, EventTypes

from registry_pkgs.models.a2a_agent import (
    A2AAgent,
    NoSupportedTransportError,
    strip_grpc_and_select_preferred_transport,
)


def _interface(transport: TransportProtocol, suffix: str) -> AgentInterface:
    return AgentInterface(
        transport=transport,
        url=f"https://agent.example.com/{suffix}",
    )


def _card(
    preferred_transport: TransportProtocol | None,
    additional_interfaces: list[AgentInterface] | None = None,
) -> AgentCard:
    return AgentCard(
        name="Transport Test Agent",
        description="Exercises transport selection",
        url="https://agent.example.com/a2a",
        version="1.0.0",
        capabilities={},
        defaultInputModes=["text/plain"],
        defaultOutputModes=["application/json"],
        skills=[],
        preferredTransport=preferred_transport,
        additionalInterfaces=additional_interfaces,
    )


def _agent(card: AgentCard) -> A2AAgent:
    return A2AAgent.model_construct(
        id=PydanticObjectId(),
        path="transport-test-agent",
        card=card,
        author=PydanticObjectId(),
    )


def test_strip_grpc_preserves_surviving_interface_order() -> None:
    jsonrpc = _interface(TransportProtocol.jsonrpc, "jsonrpc")
    grpc = _interface(TransportProtocol.grpc, "grpc")
    http_json = _interface(TransportProtocol.http_json, "http-json")
    card = _card(TransportProtocol.jsonrpc, [jsonrpc, grpc, http_json])

    preferred, interfaces = strip_grpc_and_select_preferred_transport(card)

    assert preferred == TransportProtocol.jsonrpc
    assert interfaces == [jsonrpc, http_json]
    assert card.additional_interfaces == [jsonrpc, grpc, http_json]


def test_grpc_preferred_falls_back_to_jsonrpc_before_http_json() -> None:
    http_json = _interface(TransportProtocol.http_json, "http-json")
    jsonrpc = _interface(TransportProtocol.jsonrpc, "jsonrpc")
    card = _card(TransportProtocol.grpc, [http_json, jsonrpc])

    preferred, interfaces = strip_grpc_and_select_preferred_transport(card)

    assert preferred == TransportProtocol.jsonrpc
    assert interfaces == [http_json, jsonrpc]


def test_grpc_preferred_falls_back_to_http_json() -> None:
    http_json = _interface(TransportProtocol.http_json, "http-json")
    card = _card(TransportProtocol.grpc, [http_json])

    preferred, interfaces = strip_grpc_and_select_preferred_transport(card)

    assert preferred == TransportProtocol.http_json
    assert interfaces == [http_json]


@pytest.mark.parametrize(
    "preferred_transport",
    [TransportProtocol.grpc, None],
)
@pytest.mark.parametrize(
    "additional_interfaces",
    [None, [], [_interface(TransportProtocol.grpc, "grpc")]],
)
def test_raises_when_no_non_grpc_transport_survives(
    preferred_transport: TransportProtocol | None,
    additional_interfaces: list[AgentInterface] | None,
) -> None:
    card = _card(preferred_transport, additional_interfaces)

    with pytest.raises(NoSupportedTransportError, match="has no usable transport"):
        strip_grpc_and_select_preferred_transport(card)


@pytest.mark.parametrize(
    "preferred_transport",
    [TransportProtocol.jsonrpc, TransportProtocol.http_json],
)
def test_non_grpc_preferred_transport_needs_no_additional_interfaces(
    preferred_transport: TransportProtocol,
) -> None:
    card = _card(preferred_transport)

    preferred, interfaces = strip_grpc_and_select_preferred_transport(card)

    assert preferred == preferred_transport
    assert interfaces == []


def test_no_grpc_card_is_a_content_preserving_passthrough() -> None:
    interfaces = [
        _interface(TransportProtocol.http_json, "http-json"),
        _interface(TransportProtocol.jsonrpc, "jsonrpc"),
    ]
    card = _card(TransportProtocol.http_json, interfaces)

    preferred, filtered_interfaces = strip_grpc_and_select_preferred_transport(card)

    assert preferred == TransportProtocol.http_json
    assert filtered_interfaces == interfaces
    assert card.additional_interfaces == interfaces


def test_transport_validation_hook_covers_every_document_write_event() -> None:
    event_types = A2AAgent.validate_transport_availability.event_types

    assert set(event_types) == {Insert, Replace, Save, SaveChanges, Update}


def test_transport_validation_hook_rejects_grpc_only_card() -> None:
    agent = _agent(_card(TransportProtocol.grpc, [_interface(TransportProtocol.grpc, "grpc")]))

    with pytest.raises(NoSupportedTransportError):
        agent.validate_transport_availability()


async def _skip_document_validation(*_args: object, **_kwargs: object) -> None:
    return None


async def _run_transport_validation_before_write(
    _registry: type[ActionRegistry],
    instance: A2AAgent,
    event_type: EventTypes,
    action_direction: ActionDirections,
    exclude: list[ActionDirections | str],
) -> None:
    del event_type, exclude

    if action_direction == ActionDirections.BEFORE:
        instance.validate_transport_availability()


@pytest.mark.asyncio
async def test_insert_rejects_grpc_only_card_before_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    get_collection = Mock()
    monkeypatch.setattr(A2AAgent, "validate_self", _skip_document_validation)
    monkeypatch.setattr(A2AAgent, "get_pymongo_collection", classmethod(lambda _cls: get_collection()))
    monkeypatch.setattr(ActionRegistry, "run_actions", classmethod(_run_transport_validation_before_write))
    agent = _agent(_card(TransportProtocol.grpc))

    with pytest.raises(NoSupportedTransportError):
        await agent.insert()

    get_collection.assert_not_called()


@pytest.mark.asyncio
async def test_save_rejects_card_mutated_to_grpc_only_before_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    get_collection = Mock()
    monkeypatch.setattr(A2AAgent, "validate_self", _skip_document_validation)
    monkeypatch.setattr(A2AAgent, "get_pymongo_collection", classmethod(lambda _cls: get_collection()))
    monkeypatch.setattr(ActionRegistry, "run_actions", classmethod(_run_transport_validation_before_write))
    agent = _agent(_card(TransportProtocol.jsonrpc))
    agent.card = _card(TransportProtocol.grpc, [_interface(TransportProtocol.grpc, "grpc")])

    with pytest.raises(NoSupportedTransportError):
        await agent.save()

    get_collection.assert_not_called()
