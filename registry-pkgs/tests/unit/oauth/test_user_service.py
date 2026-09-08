import pytest

from registry_pkgs.models import User
from registry_pkgs.oauth.user_service import UserService

pytestmark = pytest.mark.asyncio


async def _fake_create(self):
    """Stand in for Beanie's DB-backed create(); returns the constructed document."""
    return self


@pytest.fixture(autouse=True)
def _patch_user(monkeypatch):
    # Construct User documents without Beanie init, and stub the DB write.
    monkeypatch.setattr(User, "get_pymongo_collection", classmethod(lambda cls: None))
    monkeypatch.setattr(User, "create", _fake_create)


async def test_create_google_user_sets_native_fields():
    claims = {
        "auth_provider": "google",
        "name": "Ada",
        "sub": "108273456789",  # opaque numeric — must NOT be used as email
        "idp_id": "108273456789",
        "email": "ada@example.com",
    }
    user = await UserService().create_user(claims)
    assert user is not None
    assert user.provider == "google"
    assert user.googleId == "108273456789"
    assert user.openidId is None
    assert user.email == "ada@example.com"
    assert user.idOnTheSource == "ada@example.com"
    assert user.username == "108273456789"


async def test_create_openid_user_populates_openid_id():
    claims = {
        "auth_provider": "entra",
        "name": "Bob",
        "sub": "bob@example.com",
        "idp_id": "entra-oid-123",
        "email": "bob@example.com",
    }
    user = await UserService().create_user(claims)
    assert user is not None
    assert user.provider == "openid"
    assert user.openidId == "entra-oid-123"  # regression: no longer hardcoded ""
    assert user.googleId is None
    assert user.email == "bob@example.com"
    assert user.idOnTheSource == "entra-oid-123"


async def test_openid_user_without_email_claim_falls_back_to_sub():
    claims = {
        "auth_provider": "entra",
        "sub": "dave@example.com",
        "idp_id": "entra-oid-456",
    }
    user = await UserService().create_user(claims)
    assert user is not None
    assert user.email == "dave@example.com"


async def test_google_user_with_opaque_sub_uses_email_not_sub():
    claims = {
        "auth_provider": "google",
        "sub": "1234567890",
        "idp_id": "1234567890",
        "email": "carol@example.com",
    }
    user = await UserService().create_user(claims)
    assert user is not None
    assert user.email == "carol@example.com"


async def test_google_user_without_email_claim_falls_back_to_sub():
    """Regression test: the managed-agent access token never carries an "email" claim
    (see TokenGrantService._mint_response), and GoogleProvider.get_user_info sets the
    JWT's sub to the verified email — not Google's own opaque numeric sub (that's idp_id)."""
    claims = {
        "auth_provider": "google",
        "sub": "kent.xue@famulei.us",
        "idp_id": "100718105552674358884",
    }
    user = await UserService().create_user(claims)
    assert user is not None
    assert user.email == "kent.xue@famulei.us"
    assert user.idOnTheSource == "kent.xue@famulei.us"
    assert user.googleId == "100718105552674358884"
