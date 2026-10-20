from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from pydantic import ValidationError
from pymongo import ReturnDocument
from pymongo.errors import AutoReconnect, DuplicateKeyError
from pymongo.results import InsertOneResult

from registry_pkgs.models import User
from registry_pkgs.oauth.identity import (
    IdentityConflictError,
    IdentityService,
    UntrustedEmailError,
    VerifiedIdentity,
)

pytestmark = pytest.mark.asyncio
ISSUER = "https://login.microsoftonline.com/tenant/v2.0"


def identity(**values: Any) -> VerifiedIdentity:
    return VerifiedIdentity(
        **{
            "provider": "entra",
            "issuer": ISSUER,
            "subject": "subject-1",
            "object_id": "object-1",
            "email": "ada@example.com",
            "email_trusted": True,
            "display_name": "Ada",
            "preferred_username": "ADA@example.com",
            **values,
        }
    )


def user(**values: Any) -> dict[str, Any]:
    return {
        "_id": ObjectId(),
        "provider": "openid",
        "email": "ada@example.com",
        "username": "original-name",
        "emailVerified": False,
        "role": "ADMIN",
        **values,
    }


def matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for field, expected in query.items():
        if field == "$or":
            if not any(matches(document, item) for item in expected):
                return False
        elif isinstance(expected, dict):
            for operator, value in expected.items():
                if operator == "$exists" and (field in document) != value:
                    return False
                if operator == "$eq" and document.get(field) != value:
                    return False
                if operator == "$ne" and document.get(field) == value:
                    return False
                assert operator in {"$exists", "$eq", "$ne"}
        elif document.get(field) != expected:
            return False
    return True


class Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    def limit(self, count: int) -> "Cursor":
        self.documents = self.documents[:count]
        return self

    async def to_list(self) -> list[dict[str, Any]]:
        return deepcopy(self.documents)


class MemoryUsers:
    """Evaluate this service's Mongo filters and atomic writes without any external database."""

    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.queries: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.insert_attempts = 0
        self.openid_unique_fields = ("openidId",)
        self.before_insert = None
        self.before_update = None

    def find(self, query: dict[str, Any]) -> Cursor:
        self.queries.append(deepcopy(query))
        return Cursor([doc for doc in self.documents if matches(doc, query)])

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        documents = await self.find(query).limit(1).to_list()
        return documents[0] if documents else None

    def check_unique(self, candidate: dict[str, Any]) -> None:
        for doc in self.documents:
            if doc["_id"] == candidate["_id"]:
                continue
            for fields in (("email",), ("googleId",), self.openid_unique_fields):
                if fields[0] in candidate and all(doc.get(field) == candidate.get(field) for field in fields):
                    raise DuplicateKeyError("simulated unique constraint")

    async def insert_one(self, document: dict[str, Any]) -> InsertOneResult:
        self.insert_attempts += 1
        if self.before_insert is not None:
            callback, self.before_insert = self.before_insert, None
            callback()
        candidate = {"_id": ObjectId(), **deepcopy(document)}
        self.check_unique(candidate)
        self.documents.append(candidate)
        return InsertOneResult(candidate["_id"], True)

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any], *, return_document: bool
    ) -> dict[str, Any] | None:
        assert return_document == ReturnDocument.AFTER
        assert set(update) == {"$set"}
        self.writes.append({"filter": deepcopy(query), "update": deepcopy(update)})
        if self.before_update is not None:
            callback, self.before_update = self.before_update, None
            callback()
        for index, doc in enumerate(self.documents):
            if matches(doc, query):
                candidate = {**doc, **update["$set"]}
                self.check_unique(candidate)
                self.documents[index] = candidate
                return deepcopy(candidate)
        return None


@pytest.fixture
def users(monkeypatch: pytest.MonkeyPatch) -> MemoryUsers:
    collection = MemoryUsers()
    monkeypatch.setattr(User, "get_pymongo_collection", classmethod(lambda cls: collection))
    return collection


async def test_identity_normalizes_issuer_and_email() -> None:
    verified = identity(issuer=f"  {ISSUER}/// ", email=" ADA@Example.COM ")
    assert verified.issuer == ISSUER
    assert verified.email == "ada@example.com"
    assert identity(email="  ").email is None
    with pytest.raises(ValidationError):
        identity(issuer=" / ")
    with pytest.raises(ValidationError):
        identity(provider="keycloak")


async def test_subject_wins_over_oid_email_and_accepts_untrusted_email(users: MemoryUsers) -> None:
    existing = user(openidId="subject-1", openidIssuer=ISSUER, idOnTheSource="different-existing-oid")
    users.documents = [existing, user(email="different@example.com", idOnTheSource="object-1")]
    result = await IdentityService().resolve_or_provision(identity(email_trusted=False))
    assert result.id == existing["_id"]
    assert result.idOnTheSource == "different-existing-oid"
    assert result.emailVerified is False
    assert users.queries == [{"openidId": "subject-1", "openidIssuer": ISSUER}]
    assert users.writes == []


async def test_legacy_subject_backfills_once_and_preserves_account_fields(users: MemoryUsers) -> None:
    existing = user(openidId="subject-1")
    users.documents = [existing]
    service = IdentityService()
    result = await service.resolve_or_provision(identity())
    assert result.id == existing["_id"]
    assert users.documents[0] == {**existing, "openidIssuer": ISSUER, "idOnTheSource": "object-1"}
    assert users.writes[0]["filter"]["openidIssuer"] == {"$exists": False}
    assert users.writes[0]["filter"]["idOnTheSource"] == {"$exists": False}
    await service.resolve_or_provision(identity())
    assert len(users.writes) == 1
    assert users.insert_attempts == 0


@pytest.mark.parametrize("legacy", [False, True])
async def test_object_lookup_preserves_different_legacy_subject(users: MemoryUsers, legacy: bool) -> None:
    existing = user(idOnTheSource="object-1", openidId="old-oid-in-sub-field")
    if not legacy:
        existing["openidIssuer"] = ISSUER
    users.documents = [existing]
    result = await IdentityService().resolve_or_provision(identity(email_trusted=False))
    assert result.id == existing["_id"]
    assert result.openidId == "old-oid-in-sub-field"
    assert result.openidIssuer == ISSUER
    assert users.insert_attempts == 0


async def test_duplicate_object_id_across_exact_and_legacy_issuers_is_refused(users: MemoryUsers) -> None:
    users.documents = [user(idOnTheSource="object-1", openidIssuer=ISSUER), user(idOnTheSource="object-1")]
    with pytest.raises(IdentityConflictError, match="duplicate_object_id"):
        await IdentityService().resolve_or_provision(identity())
    assert users.writes == []


async def test_google_subject_match_does_not_write_openid_fields(users: MemoryUsers) -> None:
    existing = user(provider="google", googleId="subject-1")
    users.documents = [existing]
    result = await IdentityService().resolve_or_provision(identity(provider="google", email_trusted=False))
    assert result.id == existing["_id"]
    assert users.documents == [existing]
    assert users.queries == [{"googleId": "subject-1"}]


async def test_email_match_normalizes_case_and_fills_bindings(users: MemoryUsers) -> None:
    existing = user()
    users.documents = [existing]
    result = await IdentityService().resolve_or_provision(identity(email=" ADA@EXAMPLE.COM "))
    assert result.id == existing["_id"]
    assert users.documents[0] == {
        **existing,
        "openidId": "subject-1",
        "openidIssuer": ISSUER,
        "idOnTheSource": "object-1",
    }


@pytest.mark.parametrize("provider", ["local", "google"])
async def test_email_cannot_cross_provider(users: MemoryUsers, provider: str) -> None:
    users.documents = [user(provider=provider)]
    with pytest.raises(IdentityConflictError, match="provider_mismatch"):
        await IdentityService().resolve_or_provision(identity())
    assert users.writes == []
    assert users.insert_attempts == 0


@pytest.mark.parametrize("path", ["subject", "oid"])
async def test_stable_lookup_also_rejects_wrong_provider(users: MemoryUsers, path: str) -> None:
    fields = {"openidId": "subject-1"} if path == "subject" else {"idOnTheSource": "object-1"}
    users.documents = [user(provider="local", openidIssuer=ISSUER, **fields)]
    with pytest.raises(IdentityConflictError, match="provider_mismatch"):
        await IdentityService().resolve_or_provision(identity())


@pytest.mark.parametrize(
    ("stored", "incoming", "reason"),
    [
        ({"openidId": "other"}, {}, "openid_id_mismatch"),
        ({"openidId": ""}, {}, "openid_id_mismatch"),
        ({"openidId": None}, {}, "openid_id_mismatch"),
        ({"openidIssuer": "https://other.example.com"}, {}, "issuer_mismatch"),
        ({"openidIssuer": None}, {}, "issuer_mismatch"),
        ({"idOnTheSource": "other"}, {}, "object_id_mismatch"),
        ({"provider": "google", "googleId": "other"}, {"provider": "google"}, "google_id_mismatch"),
    ],
)
async def test_email_identity_conflicts_have_distinct_reasons(
    users: MemoryUsers, stored: dict, incoming: dict, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    existing = user(**stored)
    users.documents = [existing]
    with pytest.raises(IdentityConflictError) as error:
        await IdentityService().resolve_or_provision(identity(**incoming))
    assert error.value.reason == reason
    assert error.value.user_id == str(existing["_id"])
    assert f"IDENTITY_CONFLICT reason={reason} user_id={existing['_id']}" in caplog.text
    assert "ada@example.com" not in caplog.text
    assert users.documents == [existing]
    assert users.writes == []


async def test_local_username_cannot_capture_oidc_login(users: MemoryUsers) -> None:
    local = user(provider="local", email="other@example.com", username="ada@example.com")
    users.documents = [local]
    result = await IdentityService().resolve_or_provision(identity())
    assert result.id != local["_id"]
    assert result.provider == "openid"
    assert all("username" not in query for query in users.queries)
    assert users.documents[0] == local


@pytest.mark.parametrize("provider", ["entra", "google"])
async def test_provision_populates_chat_compatible_fields(users: MemoryUsers, provider: str) -> None:
    result = await IdentityService().resolve_or_provision(identity(provider=provider))
    stored = users.documents[0]
    assert result.id == stored["_id"]
    assert stored["email"] == stored["username"] == "ada@example.com"
    assert stored["emailVerified"] is True
    assert stored["name"] == "Ada"
    assert stored["role"] == "USER"
    assert stored["plugins"] == stored["favorites"] == []
    assert stored["termsAccepted"] is False
    assert stored["createdAt"] == stored["updatedAt"]
    if provider == "entra":
        assert stored["provider"] == "openid"
        assert stored["openidId"] == "subject-1"
        assert stored["openidIssuer"] == ISSUER
        assert stored["idOnTheSource"] == "object-1"
        assert "googleId" not in stored
    else:
        assert stored["provider"] == "google"
        assert stored["googleId"] == "subject-1"
        assert stored["idOnTheSource"] == "ada@example.com"
        assert "openidId" not in stored and "openidIssuer" not in stored


@pytest.mark.parametrize("values", [{"email_trusted": False}, {"email": None}, {"email": " "}])
async def test_provision_requires_trusted_nonempty_email(users: MemoryUsers, values: dict) -> None:
    with pytest.raises(UntrustedEmailError):
        await IdentityService().resolve_or_provision(identity(**values))
    assert users.documents == []
    assert users.insert_attempts == 0


async def test_trusted_email_updates_only_email_and_verification(users: MemoryUsers) -> None:
    existing = user(openidId="subject-1", openidIssuer=ISSUER, idOnTheSource="object-1")
    users.documents = [existing]
    await IdentityService().resolve_or_provision(identity(email="new@example.com"))
    assert users.documents[0] == {**existing, "email": "new@example.com", "emailVerified": True}
    assert users.writes[0]["update"] == {"$set": {"email": "new@example.com", "emailVerified": True}}


async def test_email_held_by_another_user_is_not_overwritten(users: MemoryUsers) -> None:
    existing = user(openidId="subject-1", openidIssuer=ISSUER)
    owner = user(email="new@example.com")
    users.documents = [existing, owner]
    result = await IdentityService().resolve_or_provision(identity(email="new@example.com"))
    assert result.email == "ada@example.com"
    assert result.emailVerified is False
    assert result.idOnTheSource == "object-1"
    assert users.documents[1] == owner


async def test_existing_null_field_is_not_silently_treated_as_missing(users: MemoryUsers) -> None:
    existing = user(openidId="subject-1", openidIssuer=ISSUER, idOnTheSource=None)
    users.documents = [existing]
    await IdentityService().resolve_or_provision(identity())
    assert users.documents == [existing]
    assert users.writes == []


async def test_concurrent_first_login_reuses_winner(users: MemoryUsers) -> None:
    winner = user(openidId="subject-1", openidIssuer=ISSUER, idOnTheSource="object-1")
    users.before_insert = lambda: users.documents.append(winner)
    result = await IdentityService().resolve_or_provision(identity())
    assert result.id == winner["_id"]
    assert users.documents == [winner]
    assert users.insert_attempts == 1


async def test_concurrent_first_login_cannot_steal_reused_email(users: MemoryUsers) -> None:
    winner = user(openidId="another-subject", openidIssuer=ISSUER)
    users.before_insert = lambda: users.documents.append(winner)
    with pytest.raises(IdentityConflictError, match="openid_id_mismatch"):
        await IdentityService().resolve_or_provision(identity())
    assert users.documents == [winner]
    assert users.insert_attempts == 1


async def test_duplicate_insert_without_winner_is_not_retried_forever(users: MemoryUsers, monkeypatch) -> None:
    insert = AsyncMock(side_effect=DuplicateKeyError("unrelated index"))
    monkeypatch.setattr(users, "insert_one", insert)
    with pytest.raises(DuplicateKeyError):
        await IdentityService().resolve_or_provision(identity())
    insert.assert_awaited_once()
    assert users.queries.count({"openidId": "subject-1", "openidIssuer": ISSUER}) == 2


async def test_concurrent_binding_cannot_be_overwritten(users: MemoryUsers) -> None:
    existing = user()
    users.documents = [existing]
    users.before_update = lambda: users.documents[0].update(openidId="other-subject")
    with pytest.raises(IdentityConflictError, match="openid_id_mismatch"):
        await IdentityService().resolve_or_provision(identity())
    assert users.documents[0]["openidId"] == "other-subject"
    assert "openidIssuer" not in users.documents[0]
    assert len(users.writes) == 1


async def test_concurrent_matching_backfill_is_idempotent(users: MemoryUsers) -> None:
    existing = user(openidId="subject-1")
    users.documents = [existing]
    users.before_update = lambda: users.documents[0].update(openidIssuer=ISSUER, idOnTheSource="object-1")
    result = await IdentityService().resolve_or_provision(identity())
    assert result.id == existing["_id"]
    assert result.openidIssuer == ISSUER
    assert result.idOnTheSource == "object-1"
    assert len(users.writes) == 1


async def test_email_uniqueness_race_preserves_old_email_and_still_fills_gaps(users: MemoryUsers) -> None:
    existing = user(openidId="subject-1", openidIssuer=ISSUER)
    owner = user(email="new@example.com")
    users.documents = [existing]
    users.before_update = lambda: users.documents.append(owner)
    result = await IdentityService().resolve_or_provision(identity(email="new@example.com"))
    assert result.email == "ada@example.com"
    assert result.emailVerified is False
    assert result.idOnTheSource == "object-1"
    assert users.documents[1] == owner
    assert len(users.writes) == 2
    assert users.writes[1]["update"] == {"$set": {"idOnTheSource": "object-1"}}


async def test_concurrent_removal_of_identity_is_refused(users: MemoryUsers) -> None:
    users.documents = [user(openidId="subject-1", email="old@example.com")]
    users.before_update = lambda: users.documents[0].update(openidId="another-subject")
    with pytest.raises(IdentityConflictError, match="concurrent_identity_change"):
        await IdentityService().resolve_or_provision(identity(email_trusted=False))
    assert users.documents[0]["openidId"] == "another-subject"


@pytest.mark.parametrize("step", ["subject", "email"])
async def test_ambiguous_matches_are_refused(users: MemoryUsers, step: str) -> None:
    fields = {"openidId": "subject-1", "openidIssuer": ISSUER} if step == "subject" else {}
    users.documents = [user(**fields), user(**fields)]
    with pytest.raises(IdentityConflictError, match=f"duplicate_{step}"):
        await IdentityService().resolve_or_provision(identity())
    assert users.writes == []


async def test_database_failure_propagates(users: MemoryUsers, monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(query: dict) -> Cursor:
        raise AutoReconnect("unavailable")

    monkeypatch.setattr(users, "find", unavailable)
    with pytest.raises(AutoReconnect):
        await IdentityService().resolve_or_provision(identity())
    assert users.insert_attempts == 0


async def test_subject_without_email_can_sign_in_without_changing_existing_email(users: MemoryUsers) -> None:
    existing = user(openidId="subject-1", openidIssuer=ISSUER, idOnTheSource="object-1")
    users.documents = [existing]
    result = await IdentityService().resolve_or_provision(identity(email=None, email_trusted=False))
    assert result.email == existing["email"]
    assert users.documents == [existing]
    assert users.writes == []


async def test_oid_match_fills_subject_and_short_circuits_email(users: MemoryUsers) -> None:
    existing = user(email="old@example.com", idOnTheSource="object-1", openidIssuer=ISSUER)
    users.documents = [existing, user(provider="local")]
    result = await IdentityService().resolve_or_provision(identity(email_trusted=False))
    assert result.id == existing["_id"]
    assert result.openidId == "subject-1"
    assert result.email == "old@example.com"
    assert not any("email" in query for query in users.queries)


async def test_google_email_link_fills_only_google_identity(users: MemoryUsers) -> None:
    existing = user(provider="google")
    users.documents = [existing]
    result = await IdentityService().resolve_or_provision(identity(provider="google", object_id=None))
    assert result.id == existing["_id"]
    assert users.documents == [{**existing, "googleId": "subject-1"}]


async def test_google_subject_cannot_match_local_provider(users: MemoryUsers) -> None:
    users.documents = [user(provider="local", googleId="subject-1")]
    with pytest.raises(IdentityConflictError, match="provider_mismatch"):
        await IdentityService().resolve_or_provision(identity(provider="google"))
    assert users.writes == []


async def test_oid_from_another_issuer_is_not_used(users: MemoryUsers) -> None:
    existing = user(email="another@example.com", idOnTheSource="object-1", openidIssuer="https://other.example.com")
    users.documents = [existing]
    with pytest.raises(UntrustedEmailError):
        await IdentityService().resolve_or_provision(identity(email_trusted=False))
    assert users.documents == [existing]


async def test_null_is_preserved_by_exact_snapshot_guard(users: MemoryUsers) -> None:
    existing = user(openidId="subject-1", openidIssuer=ISSUER, idOnTheSource=None)
    users.documents = [existing]
    await IdentityService().resolve_or_provision(identity(email="new@example.com"))
    assert users.writes[0]["filter"]["idOnTheSource"] == {"$eq": None, "$exists": True}
    assert users.documents[0]["idOnTheSource"] is None


async def test_identity_unique_constraint_failure_is_not_swallowed(users: MemoryUsers, monkeypatch) -> None:
    users.documents = [user(openidId="subject-1")]
    update = AsyncMock(side_effect=DuplicateKeyError("identity already claimed"))
    monkeypatch.setattr(users, "find_one_and_update", update)
    with pytest.raises(DuplicateKeyError):
        await IdentityService().resolve_or_provision(identity())
    update.assert_awaited_once()
    assert "openidIssuer" not in users.documents[0]


async def test_repeated_concurrent_updates_are_bounded(users: MemoryUsers, monkeypatch) -> None:
    users.documents = [user(openidId="subject-1")]
    update = AsyncMock(return_value=None)
    monkeypatch.setattr(users, "find_one_and_update", update)
    with pytest.raises(IdentityConflictError, match="concurrent_identity_change"):
        await IdentityService().resolve_or_provision(identity())
    assert update.await_count == 3


@pytest.mark.parametrize("email", ["not-an-email", "missing-domain@", "missing@dot", 123])
async def test_invalid_trusted_email_is_rejected_before_database_access(users: MemoryUsers, email: Any) -> None:
    existing = user(openidId="subject-1", openidIssuer=ISSUER, idOnTheSource="object-1")
    users.documents = [deepcopy(existing)]
    with pytest.raises(ValidationError):
        await IdentityService().resolve_or_provision(identity(email=email))
    assert users.documents == [existing]
    assert users.queries == users.writes == []
    assert users.insert_attempts == 0


@pytest.mark.parametrize("field", ["subject", "object_id"])
async def test_blank_opaque_identifiers_are_rejected_without_normalizing_valid_values(field: str) -> None:
    with pytest.raises(ValidationError):
        identity(**{field: " \t "})
    assert getattr(identity(**{field: " Opaque-MixedCase "}), field) == " Opaque-MixedCase "


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None])
async def test_email_trust_requires_an_actual_boolean(value: Any) -> None:
    with pytest.raises(ValidationError):
        identity(email_trusted=value)


@pytest.mark.parametrize("compound_index", [False, True])
async def test_subject_uniqueness_is_respected_without_assuming_index_shape(
    users: MemoryUsers, compound_index: bool
) -> None:
    existing = user(
        email="other@example.com",
        openidId="subject-1",
        openidIssuer="https://other.example.com",
        idOnTheSource="other-object",
    )
    users.documents = [existing]
    if compound_index:
        users.openid_unique_fields = ("openidId", "openidIssuer")
        result = await IdentityService().resolve_or_provision(identity())
        assert result.id != existing["_id"]
        assert result.openidIssuer == ISSUER
        assert len(users.documents) == 2
    else:
        with pytest.raises(DuplicateKeyError):
            await IdentityService().resolve_or_provision(identity())
        assert users.documents == [existing]
        assert users.queries.count({"openidId": "subject-1", "openidIssuer": ISSUER}) == 2
    assert users.insert_attempts == 1
    assert users.writes == []


async def test_untrusted_malformed_email_does_not_block_verified_subject(users: MemoryUsers) -> None:
    existing = user(openidId="subject-1", openidIssuer=ISSUER, idOnTheSource="object-1")
    users.documents = [deepcopy(existing)]
    result = await IdentityService().resolve_or_provision(identity(email="not-an-email", email_trusted=False))
    assert result.id == existing["_id"]
    assert result.email == existing["email"]
    assert users.documents == [existing]
    assert users.writes == []
    assert users.insert_attempts == 0
