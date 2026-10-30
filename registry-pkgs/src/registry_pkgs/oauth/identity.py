"""Bind verified provider identities to the users collection shared with Chat."""

import logging
from datetime import UTC, datetime
from typing import Any, Literal, Self

from bson import ObjectId
from pydantic import BaseModel, Field, StrictBool, TypeAdapter, field_validator, model_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from registry_pkgs.models import User
from registry_pkgs.models._generated.user import EmailValue

logger = logging.getLogger(__name__)

_IDENTITY_FIELDS = ("provider", "openidId", "openidIssuer", "googleId", "idOnTheSource", "email")
_UPDATE_ATTEMPTS = 3
_EMAIL_ADAPTER = TypeAdapter(EmailValue)


class VerifiedIdentity(BaseModel):
    provider: Literal["entra", "google"]
    issuer: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    object_id: str | None = Field(default=None, min_length=1)
    email: str | None = None
    email_trusted: StrictBool = False
    display_name: str | None = None
    preferred_username: str | None = None

    @field_validator("issuer")
    @classmethod
    def normalize_issuer(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            raise ValueError("Identity issuer must not be empty")
        return value

    @field_validator("subject", "object_id")
    @classmethod
    def reject_blank_identifier(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Identity identifiers must not be blank")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower() or None
        return value

    @model_validator(mode="after")
    def validate_trusted_email(self) -> Self:
        if self.email_trusted and self.email is not None:
            _EMAIL_ADAPTER.validate_python(self.email)
        return self


class IdentityResolutionError(Exception):
    """A verified identity cannot safely be associated with a user."""


class IdentityConflictError(IdentityResolutionError):
    def __init__(self, reason: str, user_id: ObjectId | str) -> None:
        self.reason = reason
        self.user_id = str(user_id)
        logger.warning("IDENTITY_CONFLICT reason=%s user_id=%s", reason, self.user_id)
        super().__init__(f"Identity conflict: {reason} (user_id={self.user_id})")


class UntrustedEmailError(IdentityResolutionError):
    """Creating a user requires a provider-verified email address."""


class IdentityService:
    async def resolve_or_provision(self, identity: VerifiedIdentity) -> User:
        document = await self._find_match(identity)
        if document is not None:
            return await self._update_matched(identity, document)
        if not identity.email_trusted or not identity.email:
            logger.warning(
                "IDENTITY_UNTRUSTED_EMAIL provider=%s has_email=%s email_trusted=%s",
                identity.provider,
                identity.email is not None,
                identity.email_trusted,
            )
            raise UntrustedEmailError()
        document = self._new_document(identity)
        try:
            result = await User.get_pymongo_collection().insert_one(document)
        except DuplicateKeyError:
            document = await self._find_match(identity)
            if document is None:
                raise
            return await self._update_matched(identity, document)
        document["_id"] = result.inserted_id
        return User.model_validate(document)

    @staticmethod
    def _provider(identity: VerifiedIdentity) -> str:
        return "google" if identity.provider == "google" else "openid"

    @staticmethod
    def _bindings(identity: VerifiedIdentity) -> dict[str, str]:
        if identity.provider == "google":
            return {"googleId": identity.subject}
        fields = {"openidId": identity.subject, "openidIssuer": identity.issuer}
        if identity.object_id is not None:
            fields["idOnTheSource"] = identity.object_id
        return fields

    async def _find_unique(self, query: dict[str, Any], reason: str) -> dict[str, Any] | None:
        documents = await User.get_pymongo_collection().find(query).limit(2).to_list()
        if len(documents) > 1:
            raise IdentityConflictError(reason, documents[0]["_id"])
        return documents[0] if documents else None

    async def _find_subject(self, identity: VerifiedIdentity) -> dict[str, Any] | None:
        if identity.provider == "google":
            return await self._find_unique({"googleId": identity.subject}, "duplicate_subject")
        for issuer in (identity.issuer, {"$exists": False}):
            document = await self._find_unique(
                {"openidId": identity.subject, "openidIssuer": issuer}, "duplicate_subject"
            )
            if document is not None:
                return document
        return None

    async def _find_match(self, identity: VerifiedIdentity) -> dict[str, Any] | None:
        document = await self._find_subject(identity)
        if document is None and identity.provider == "entra" and identity.object_id is not None:
            document = await self._find_unique(
                {
                    "idOnTheSource": identity.object_id,
                    "$or": [{"openidIssuer": identity.issuer}, {"openidIssuer": {"$exists": False}}],
                },
                "duplicate_object_id",
            )
        if document is not None:
            self._check_provider(identity, document)
            return document
        if identity.email_trusted and identity.email:
            document = await self._find_unique({"email": identity.email}, "duplicate_email")
            if document is not None:
                self._check_email_match(identity, document)
        return document

    def _check_provider(self, identity: VerifiedIdentity, document: dict[str, Any]) -> None:
        if document.get("provider") != self._provider(identity):
            raise IdentityConflictError("provider_mismatch", document["_id"])

    def _check_email_match(self, identity: VerifiedIdentity, document: dict[str, Any]) -> None:
        self._check_provider(identity, document)
        checks = {
            "openidIssuer": (identity.issuer, "issuer_mismatch"),
            "googleId" if identity.provider == "google" else "openidId": (
                identity.subject,
                "google_id_mismatch" if identity.provider == "google" else "openid_id_mismatch",
            ),
        }
        if identity.provider == "entra" and identity.object_id is not None:
            checks["idOnTheSource"] = (identity.object_id, "object_id_mismatch")
        for field, (expected, reason) in checks.items():
            if field in document and document[field] != expected:
                raise IdentityConflictError(reason, document["_id"])

    async def _updates(
        self, identity: VerifiedIdentity, document: dict[str, Any], *, update_email: bool
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {
            field: value for field, value in self._bindings(identity).items() if field not in document
        }
        if update_email and identity.email_trusted and identity.email and identity.email != document["email"]:
            owner = await User.get_pymongo_collection().find_one(
                {"email": identity.email, "_id": {"$ne": document["_id"]}}
            )
            if owner is None:
                updates.update(email=identity.email, emailVerified=True)
        return updates

    @staticmethod
    def _snapshot_filter(document: dict[str, Any]) -> dict[str, Any]:
        # Mongo equality to null also matches missing fields: preserve that distinction for CAS.
        fields = {
            field: {"$eq": document[field], "$exists": True} if field in document else {"$exists": False}
            for field in _IDENTITY_FIELDS
        }
        return {"_id": document["_id"], **fields}

    async def _update_matched(self, identity: VerifiedIdentity, document: dict[str, Any]) -> User:
        update_email = True
        user_id = document["_id"]
        for _ in range(_UPDATE_ATTEMPTS):
            updates = await self._updates(identity, document, update_email=update_email)
            if not updates:
                return self._matched_user(identity, document)
            try:
                updated = await User.get_pymongo_collection().find_one_and_update(
                    self._snapshot_filter(document), {"$set": updates}, return_document=ReturnDocument.AFTER
                )
            except DuplicateKeyError:
                if "email" not in updates:
                    raise
                # A different user may acquire the email after the ownership check. Keep the old email.
                update_email = False
            else:
                if updated is not None:
                    return self._matched_user(identity, updated)
            document = await self._find_match(identity)
            if document is None or document["_id"] != user_id:
                raise IdentityConflictError("concurrent_identity_change", user_id)
        raise IdentityConflictError("concurrent_identity_change", user_id)

    def _matched_user(self, identity: VerifiedIdentity, document: dict[str, Any]) -> User:
        # Google resolves group membership by users.email (access_control.group_member_id_for_user).
        # If the current verified email could not be persisted (held by another user, or lost an
        # update race), logging in would sync groups off a stale address that may since have been
        # reassigned to someone else — block it. Entra keys off idOnTheSource, so it is unaffected.
        if (
            identity.provider == "google"
            and identity.email_trusted
            and identity.email
            and document["email"] != identity.email
        ):
            raise IdentityConflictError("google_email_not_persisted", document["_id"])
        return User.model_validate(document)

    def _new_document(self, identity: VerifiedIdentity) -> dict[str, Any]:
        now = datetime.now(UTC)
        user = User(
            **self._bindings(identity),
            provider=self._provider(identity),
            name=identity.display_name,
            username=(identity.email if identity.provider == "google" else identity.preferred_username) or "",
            email=identity.email,
            emailVerified=True,
            role="USER",
            plugins=[],
            termsAccepted=False,
            favorites=[],
            createdAt=now,
            updatedAt=now,
        )
        # Google users are matched to groups by email, not idOnTheSource (see
        # access_control.group_member_id_for_user), so it is not stored for them.
        return user.model_dump(by_alias=True, exclude_none=True)
