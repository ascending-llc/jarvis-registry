#!/usr/bin/env python3
"""Diagnose Google Workspace group resolution for Registry login, printing Google's full error response.

auth-server resolves a Google user's scope groups with the Cloud Identity
`groups.memberships:searchTransitiveGroups` call. When that call fails, the auth-server log keeps
only Google's truncated error message. This script makes the same call, through the same client and
service account, for each given email and prints the full response.

Run from the repository root with the client's settings in an env file (see
scripts/diagnose_google_groups.env.example):
    uv run python scripts/diagnose_google_groups.py --env-file scripts/.env.google-groups \
        jason@example.com tommy@example.com

Or run it inside the auth-server pod, which already has the settings in its environment:
    kubectl exec -i <auth-server-pod> -- python - jason@example.com tommy@example.com \
        < scripts/diagnose_google_groups.py

Pass a working account and a failing account together so the two responses can be compared.

The customer ID defaults to GOOGLE_CUSTOMER_ID, as in auth-server; --customer-id overrides it. With a
customer ID, the query also gets `parent == 'customers/<id>'`, limiting the search to groups owned by
that Workspace. Without one, Google rejects the query with Error(4013) when the member belongs to a
group outside the Workspace.

Every successful lookup prints each group's owner as `owner: customers/<ID>`, so a run for any working
account reveals the customer ID to put in GOOGLE_CUSTOMER_ID, without Admin Console access.

Exits 0 when every lookup succeeds, 1 when any lookup fails, and 2 on a configuration error.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from registry_pkgs.core.config import GOOGLE_CUSTOMER_ID_PATTERN
from registry_pkgs.core.scope_groups import SCOPE_GROUP_OBJECT_ID_FIELDS, scope_group_for_google_email
from registry_pkgs.google.cloud_identity_client import CloudIdentityGroupsClient

SCOPE_GROUP_NAMES = tuple(SCOPE_GROUP_OBJECT_ID_FIELDS)
REQUIRED_KEY_FIELDS = ("client_email", "private_key")
CUSTOMER_ID_ERROR = "must start with 'C' followed by letters or digits, e.g. C046psxkn"


class DiagnosticSettings(BaseSettings):
    """The settings auth-server uses for Google group resolution, read under the same env var names."""

    model_config = SettingsConfigDict(extra="ignore")

    google_service_account_key_json: str = ""
    google_allowed_hd: str = ""
    google_customer_id: str = ""


class ConfigError(ValueError):
    """A setting is missing or malformed; its message never contains secret values."""


def describe_service_account(key_json: str) -> str:
    """Validate the key's shape and return its non-secret identifiers for display."""
    if not key_json.strip():
        raise ConfigError("GOOGLE_SERVICE_ACCOUNT_KEY_JSON is not set")
    try:
        key = json.loads(key_json)
    except json.JSONDecodeError as e:
        raise ConfigError(
            f"GOOGLE_SERVICE_ACCOUNT_KEY_JSON is not valid JSON (line {e.lineno}, column {e.colno})"
        ) from e
    if not isinstance(key, dict):
        raise ConfigError("GOOGLE_SERVICE_ACCOUNT_KEY_JSON must be a JSON object")
    missing = [field for field in REQUIRED_KEY_FIELDS if not key.get(field)]
    if missing:
        raise ConfigError(f"GOOGLE_SERVICE_ACCOUNT_KEY_JSON is missing: {', '.join(missing)}")
    return f"client_email={key['client_email']} private_key_id={key.get('private_key_id', '(none)')}"


def resolve_customer_id(override: str | None, setting: str) -> str:
    """Return --customer-id when given, else GOOGLE_CUSTOMER_ID, which has no validator in DiagnosticSettings."""
    if override is not None:
        return override
    customer_id = setting.strip()
    if customer_id and not GOOGLE_CUSTOMER_ID_PATTERN.fullmatch(customer_id):
        raise ConfigError(f"GOOGLE_CUSTOMER_ID {CUSTOMER_ID_ERROR}")
    return customer_id


def print_http_error(exc: httpx.HTTPStatusError) -> None:
    response = exc.response
    print(f"  FAILED: HTTP {response.status_code} {response.reason_phrase}")
    print(f"  Request: {exc.request.method} {exc.request.url}")
    try:
        body = json.dumps(response.json(), indent=2)
    except ValueError:
        body = response.text
    print("  Google response body:")
    for line in body.splitlines() or ["(empty)"]:
        print(f"    {line}")


def customer_id_arg(value: str) -> str:
    value = value.strip()
    if not GOOGLE_CUSTOMER_ID_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(f"--customer-id {CUSTOMER_ID_ERROR}")
    return value


async def group_owner(client: CloudIdentityGroupsClient, resource_name: str) -> str:
    """Return the group's `parent` (`customers/<customer ID>` for Google Groups), the value GOOGLE_CUSTOMER_ID takes."""
    try:
        group = await client.get_group(resource_name)
    except httpx.HTTPStatusError as exc:
        return f"(groups.get failed: HTTP {exc.response.status_code})"
    except httpx.HTTPError as exc:
        return f"(groups.get failed: {type(exc).__name__})"
    return group.get("parent", "(no parent field)")


async def diagnose_member(client: CloudIdentityGroupsClient, email: str, allowed_hd: str) -> bool:
    """Look up one member's transitive groups exactly as auth-server does; return whether it succeeded."""
    print(f"\n=== {email}")
    try:
        groups = await client.list_transitive_groups_for_member(email)
    except httpx.HTTPStatusError as exc:
        print_http_error(exc)
        return False
    except httpx.HTTPError as exc:
        print(f"  FAILED: network error {type(exc).__name__}: {exc}")
        return False
    except ValueError as exc:
        # Not only token failures: a non-JSON response body or a malformed membership also raises a ValueError.
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return False

    print(f"  OK: {len(groups)} group(s) returned")
    for group in groups:
        local_part = scope_group_for_google_email(group.email, allowed_hd)
        if local_part is None:
            mapping = f"ignored (domain is not GOOGLE_ALLOWED_HD={allowed_hd or '(unset)'})"
        elif local_part in SCOPE_GROUP_NAMES:
            mapping = f"grants scope group '{local_part}'"
        else:
            mapping = "ignored (not a jarvis-registry-* scope group)"
        print(f"    - {group.email} ({group.display_name or 'no display name'}): {mapping}")
        print(f"      owner: {await group_owner(client, group.resource_name)}")
    if not any(scope_group_for_google_email(g.email, allowed_hd) in SCOPE_GROUP_NAMES for g in groups):
        print("  WARNING: no group grants a scope; Registry login would still be blocked for this user")
    return True


async def run(emails: list[str], settings: DiagnosticSettings, customer_id_override: str | None = None) -> int:
    try:
        account = describe_service_account(settings.google_service_account_key_json)
        customer_id = resolve_customer_id(customer_id_override, settings.google_customer_id)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2

    print(f"Service account: {account}")
    print(f"GOOGLE_ALLOWED_HD: {settings.google_allowed_hd or '(unset: no Google group grants any scope)'}")
    if customer_id:
        print(f"Query: scoped with parent == 'customers/{customer_id}'")
    else:
        print("Query: unscoped (no customer ID; members of groups outside the Workspace fail with Error(4013))")

    client = CloudIdentityGroupsClient(settings.google_service_account_key_json, customer_id=customer_id)
    try:
        results = [await diagnose_member(client, email.strip().lower(), settings.google_allowed_hd) for email in emails]
    finally:
        await client.aclose()

    failed = results.count(False)
    print(f"\nSummary: {len(results) - failed} succeeded, {failed} failed")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("emails", nargs="+", metavar="EMAIL", help="Workspace user email(s) to look up")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Env file with the Google settings; variables already in the environment take precedence",
    )
    parser.add_argument(
        "--customer-id",
        type=customer_id_arg,
        help="Workspace customer ID (starts with 'C'); overrides GOOGLE_CUSTOMER_ID",
    )
    args = parser.parse_args()

    if args.env_file is not None and not args.env_file.is_file():
        parser.error(f"--env-file not found: {args.env_file}")
    settings = DiagnosticSettings(_env_file=args.env_file)
    sys.exit(asyncio.run(run(args.emails, settings, args.customer_id)))


if __name__ == "__main__":
    main()
