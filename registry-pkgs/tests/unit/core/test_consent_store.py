"""Exercise consent Lua scripts and transactions against an in-memory Redis."""

import json
from concurrent.futures import ThreadPoolExecutor

import fakeredis
import pytest

from registry_pkgs.core.consent_store import (
    CLIENT_CONSENT_FIELD,
    LEGACY_CONSENT_GRANT_ID,
    PENDING_CONSENT_TTL_SECONDS,
    ConsentStore,
    PendingConsentStore,
    consent_grant_matches,
    effective_consent_grant_id,
)
from registry_pkgs.core.oauth_state_store import CLIENT_TTL_SECONDS

KEY_PREFIX = "jarvis-auth-server-test"
CONSENT_KEY = f"{KEY_PREFIX}:mcp:consent:user-1:client-1"


@pytest.fixture
def redis_client() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def store(redis_client: fakeredis.FakeRedis) -> ConsentStore:
    return ConsentStore(redis_client=redis_client, key_prefix=KEY_PREFIX)


def test_client_consent_uses_shared_hash_and_slides_ttl(redis_client: fakeredis.FakeRedis, store: ConsentStore) -> None:
    assert store.get_client_grant_id("user-1", "client-1") is None
    assert redis_client.exists(CONSENT_KEY) == 0

    grant_id = store.grant_client_consent("user-1", "client-1")

    assert isinstance(grant_id, str)
    assert grant_id and grant_id != LEGACY_CONSENT_GRANT_ID
    assert redis_client.hget(CONSENT_KEY, CLIENT_CONSENT_FIELD) == grant_id
    assert CLIENT_TTL_SECONDS - 1 <= redis_client.ttl(CONSENT_KEY) <= CLIENT_TTL_SECONDS
    redis_client.expire(CONSENT_KEY, 100)

    assert store.get_client_grant_id("user-1", "client-1") == grant_id
    assert CLIENT_TTL_SECONDS - 1 <= redis_client.ttl(CONSENT_KEY) <= CLIENT_TTL_SECONDS


@pytest.mark.parametrize("existing_grant", [None, LEGACY_CONSENT_GRANT_ID, "existing-grant"])
def test_repeated_client_approval_preserves_the_grant_and_renews_ttl(
    redis_client: fakeredis.FakeRedis, store: ConsentStore, existing_grant: str | None
) -> None:
    if existing_grant is not None:
        redis_client.hset(CONSENT_KEY, CLIENT_CONSENT_FIELD, existing_grant)

    grant_id = store.grant_client_consent("user-1", "client-1")
    redis_client.expire(CONSENT_KEY, 100)

    assert store.grant_client_consent("user-1", "client-1") == grant_id
    if existing_grant is not None:
        assert grant_id == existing_grant
    assert CLIENT_TTL_SECONDS - 1 <= redis_client.ttl(CONSENT_KEY) <= CLIENT_TTL_SECONDS


def test_concurrent_approvals_share_one_grant(store: ConsentStore) -> None:
    with ThreadPoolExecutor(max_workers=4) as executor:
        approvals = [executor.submit(store.grant_client_consent, "user-1", "client-1") for _ in range(16)]
        grant_ids = {approval.result() for approval in approvals}

    assert grant_ids == {store.get_client_grant_id("user-1", "client-1")}
    assert None not in grant_ids


@pytest.mark.parametrize("legacy", [False, True])
def test_reapproval_after_revocation_never_revives_old_credentials(
    redis_client: fakeredis.FakeRedis, store: ConsentStore, legacy: bool
) -> None:
    if legacy:
        redis_client.hset(CONSENT_KEY, CLIENT_CONSENT_FIELD, LEGACY_CONSENT_GRANT_ID)
    old_grant_id = store.grant_client_consent("user-1", "client-1")
    presented_grant_id = None if legacy else old_grant_id

    redis_client.delete(CONSENT_KEY)
    assert not consent_grant_matches(store.get_client_grant_id("user-1", "client-1"), presented_grant_id)

    new_grant_id = store.grant_client_consent("user-1", "client-1")
    assert new_grant_id != old_grant_id
    assert not consent_grant_matches(new_grant_id, presented_grant_id)
    assert consent_grant_matches(new_grant_id, new_grant_id)


def test_missing_app_consent_does_not_slide_resource_hash_ttl(
    redis_client: fakeredis.FakeRedis, store: ConsentStore
) -> None:
    store.grant_server_consent("user-1", "client-1", "/github")
    redis_client.expire(CONSENT_KEY, 100)

    assert store.get_client_grant_id("user-1", "client-1") is None
    assert 99 <= redis_client.ttl(CONSENT_KEY) <= 100


@pytest.mark.parametrize(
    ("stored", "presented", "matches"),
    [
        (None, None, False),
        (None, "g1", False),
        ("1", None, True),
        ("g1", None, False),
        ("g1", "g1", True),
        ("g2", "g1", False),
        ("1", "", False),
    ],
)
def test_consent_grant_matches(stored: str | None, presented: str | None, matches: bool) -> None:
    assert consent_grant_matches(stored, presented) is matches


@pytest.mark.parametrize(
    ("presented", "effective"),
    [(None, LEGACY_CONSENT_GRANT_ID), ("g1", "g1"), ("", "")],
)
def test_effective_consent_grant_id(presented: str | None, effective: str) -> None:
    assert effective_consent_grant_id(presented) == effective


@pytest.mark.parametrize("kind", ["server", "agent"])
def test_resource_consent_shares_client_hash_and_only_slides_ttl_on_hit(
    redis_client: fakeredis.FakeRedis, store: ConsentStore, kind: str
) -> None:
    grant = getattr(store, f"grant_{kind}_consent")
    has_consent = getattr(store, f"has_{kind}_consent")
    field = "/github" if kind == "server" else "agent:/github"

    assert has_consent("user-1", "client-1", "/github") is False
    assert redis_client.exists(CONSENT_KEY) == 0
    store.grant_client_consent("user-1", "client-1")
    redis_client.expire(CONSENT_KEY, 100)
    assert has_consent("user-1", "client-1", "/github") is False
    assert 99 <= redis_client.ttl(CONSENT_KEY) <= 100

    grant("user-1", "client-1", "/github")
    assert redis_client.hget(CONSENT_KEY, field) == "1"
    redis_client.expire(CONSENT_KEY, 100)
    assert has_consent("user-1", "client-1", "/github") is True
    assert CLIENT_TTL_SECONDS - 1 <= redis_client.ttl(CONSENT_KEY) <= CLIENT_TTL_SECONDS


def test_agent_and_server_consent_fields_do_not_collide(redis_client: fakeredis.FakeRedis, store: ConsentStore) -> None:
    store.grant_server_consent("user-1", "client-1", "/shared")

    assert store.has_server_consent("user-1", "client-1", "/shared") is True
    assert store.has_agent_consent("user-1", "client-1", "/shared") is False

    store.grant_agent_consent("user-1", "client-1", "/shared")

    assert store.has_server_consent("user-1", "client-1", "/shared") is True
    assert store.has_agent_consent("user-1", "client-1", "/shared") is True
    assert set(redis_client.hkeys(CONSENT_KEY)) == {"/shared", "agent:/shared"}


def test_agent_consent_does_not_grant_same_path_server_consent(store: ConsentStore) -> None:
    store.grant_agent_consent("user-1", "client-1", "/shared")

    assert store.has_agent_consent("user-1", "client-1", "/shared") is True
    assert store.has_server_consent("user-1", "client-1", "/shared") is False


def test_pending_consent_peek_and_consume_are_one_shot(redis_client: fakeredis.FakeRedis) -> None:
    store = PendingConsentStore(redis_client=redis_client, key_prefix=KEY_PREFIX)
    payload = {"user_id": "user-1", "client_id": "client-1"}

    store.save("nonce-1", payload)

    key = f"{KEY_PREFIX}:mcp:consent-pending:nonce-1"
    assert json.loads(redis_client.get(key)) == payload
    assert PENDING_CONSENT_TTL_SECONDS - 1 <= redis_client.ttl(key) <= PENDING_CONSENT_TTL_SECONDS
    assert store.peek("nonce-1") == payload
    assert store.consume("nonce-1") == payload
    assert store.consume("nonce-1") is None
