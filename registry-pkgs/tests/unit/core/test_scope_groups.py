"""Scope grants depend on IdP identities, never untrusted group display names."""

import pytest

from registry_pkgs.core.config import JarvisBaseSettings
from registry_pkgs.core.scope_groups import (
    SCOPE_GROUP_OBJECT_ID_FIELDS,
    entra_scope_group_ids,
    scope_group_for_google_email,
    scope_groups_for_entra_ids,
    validate_scope_group_object_ids,
)
from registry_pkgs.core.scopes import load_scopes_config, map_groups_to_scopes
from registry_pkgs.testing.fixtures import TEST_SCOPE_GROUP_OBJECT_IDS

pytestmark = pytest.mark.unit


def test_scope_bindings_cover_exactly_the_bundled_roles() -> None:
    assert SCOPE_GROUP_OBJECT_ID_FIELDS.keys() == load_scopes_config()["group_mappings"].keys()


def test_validation_canonicalizes_uuid_representations() -> None:
    values = {field: f" {{{value.upper()}}} " for field, value in TEST_SCOPE_GROUP_OBJECT_IDS.items()}
    assert validate_scope_group_object_ids(values) == TEST_SCOPE_GROUP_OBJECT_IDS


def test_validation_reports_all_missing_invalid_and_duplicate_fields() -> None:
    fields = list(SCOPE_GROUP_OBJECT_ID_FIELDS.values())
    object_id = TEST_SCOPE_GROUP_OBJECT_IDS[fields[2]]
    values = {fields[0]: "", fields[1]: "bad-id", fields[2]: object_id, fields[3]: object_id.upper().replace("-", "")}

    with pytest.raises(ValueError) as error:
        validate_scope_group_object_ids(values)

    for field in fields:
        assert field.upper() in str(error.value)
    assert "is not set" in str(error.value)
    assert "must be a UUID" in str(error.value)
    assert "distinct" in str(error.value)


def test_validation_reports_every_absent_binding() -> None:
    with pytest.raises(ValueError) as error:
        validate_scope_group_object_ids({})
    for field in SCOPE_GROUP_OBJECT_ID_FIELDS.values():
        assert f"{field.upper()} is not set" in str(error.value)


def test_entra_groups_use_canonical_ids_and_configured_order() -> None:
    bindings = entra_scope_group_ids(JarvisBaseSettings(**TEST_SCOPE_GROUP_OBJECT_IDS))
    ids = list(bindings)

    groups = scope_groups_for_entra_ids(
        [ids[1].upper(), ids[0], ids[1], "not-a-uuid", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"], bindings
    )

    assert groups == ["jarvis-registry-admin", "jarvis-registry-power-user"]


def test_unknown_ids_and_display_names_never_grant_scopes() -> None:
    bindings = entra_scope_group_ids(JarvisBaseSettings(**TEST_SCOPE_GROUP_OBJECT_IDS))
    groups = scope_groups_for_entra_ids(["jarvis-registry-admin", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"], bindings)
    assert map_groups_to_scopes(groups) == []


def test_google_only_settings_have_no_entra_bindings() -> None:
    settings = JarvisBaseSettings(auth_provider="google", **dict.fromkeys(SCOPE_GROUP_OBJECT_ID_FIELDS.values()))
    assert entra_scope_group_ids(settings) == {}


@pytest.mark.parametrize(
    ("email", "allowed_hd", "expected"),
    [
        ("jarvis-registry-admin@example.com", "example.com", "jarvis-registry-admin"),
        ("jarvis-registry-admin@EXAMPLE.com", "Example.COM", "jarvis-registry-admin"),
        ("jarvis-registry-admin@other.com", "example.com", None),
        ("jarvis-registry-admin@sub.example.com", "example.com", None),
        ("jarvis-registry-admin@example.com.evil.com", "example.com", None),
        ("jarvis-registry-admin@example.com", "", None),
        ("jarvis-registry-admin@example.com", "   ", None),
        ("jarvis-registry-admin", "example.com", None),
        ("jarvis-registry-admin@evil.com@example.com", "example.com", None),
        ("@example.com", "example.com", None),
        ("jarvis-registry-admin@", "example.com", None),
        ("jarvis registry-admin@example.com", "example.com", None),
        ("jarvis-registry-admin@ example.com", "example.com", None),
        (None, "example.com", None),
        ("", "example.com", None),
    ],
)
def test_google_scope_groups_require_the_complete_allowed_domain(
    email: str | None,
    allowed_hd: str,
    expected: str | None,
) -> None:
    assert scope_group_for_google_email(email, allowed_hd) == expected
