"""Regression test guarding against Beanie silently dropping underscore-prefixed event hooks.

Beanie's init_actions (>=2.1.0) skips any @before_event/@after_event method whose name starts
with "_" (https://github.com/BeanieODM/beanie/issues/1316), with no exception for hook-decorated
methods. AS-1767 found this had silently killed A2AAgent._refresh_content_hash,
A2AAgent._validate_transport_availability, and ExtendedMCPServer._refresh_content_hash in
production. This scans every Beanie Document model exported by registry_pkgs.models and verifies
each declared event hook is actually registered by Beanie's real action-registration path, not
just decorated.
"""

import inspect

import pytest
from beanie import Document
from beanie.odm.actions import ActionRegistry
from beanie.odm.utils.init import Initializer

import registry_pkgs.models as registry_pkgs_models


def _document_classes() -> list[type[Document]]:
    seen: set[type[Document]] = set()
    classes: list[type[Document]] = []
    for name in registry_pkgs_models.__all__:
        obj = getattr(registry_pkgs_models, name)
        if inspect.isclass(obj) and issubclass(obj, Document) and obj not in seen:
            seen.add(obj)
            classes.append(obj)
    return classes


def _declared_hooks(document_class: type[Document]) -> dict[str, object]:
    return {
        name: member
        for name, member in inspect.getmembers(document_class, predicate=inspect.isfunction)
        if getattr(member, "has_action", False)
    }


_CLASSES_WITH_HOOKS = [cls for cls in _document_classes() if _declared_hooks(cls)]


@pytest.mark.parametrize("document_class", _CLASSES_WITH_HOOKS, ids=lambda c: c.__name__)
def test_every_declared_event_hook_is_actually_registered(document_class: type[Document]) -> None:
    declared = _declared_hooks(document_class)

    Initializer.init_actions(document_class)

    for name, member in declared.items():
        for event_type in member.event_types:
            registered = ActionRegistry.get_action_list(document_class, event_type, member.action_direction)
            assert name in {action.__name__ for action in registered}, (
                f"{document_class.__name__}.{name} is decorated with an event hook but Beanie never "
                f"registered it for {event_type} - likely because its name starts with '_'"
            )
