# Identity conflict resolution

The auth server resolves verified Entra or Google identities before granting access. It refuses
ambiguous identities instead of linking accounts by username or silently inheriting another user's
permissions. Confirm the deployed version before diagnosing a report; deleting a user does not
resolve every login failure.

## User-facing errors

- **Conflict:** "We couldn't sign you in because this account conflicts with an existing account.
  Please contact your administrator."
- **Untrusted email:** "We couldn't verify your email address with your organization. Please contact
  your administrator."

Browser logins return these messages on the login page. Device logins mark the device code failed
with the same description. An untrusted email can still sign in through an existing stable subject
or Entra object-ID match, but cannot link by email or create a new account.

## Find and inspect the conflict

Search auth-server logs for `IDENTITY_CONFLICT`. Each event records `reason` and `user_id`, without
email addresses or token contents. Inspect the identified document in the intended database:

```javascript
db.users.findOne(
  {_id: ObjectId("<user_id from the conflict log>")},
  {provider: 1, email: 1, username: 1, openidId: 1, openidIssuer: 1,
   idOnTheSource: 1, googleId: 1, createdAt: 1}
)
```

For duplicate matches the logged ID identifies one candidate; inspect the other documents sharing
the relevant identity field and issuer before deciding which account is canonical. Keep identity
details inside approved admin tools; do not paste tokens or full user documents into tickets.

| Reason | Meaning and action |
| --- | --- |
| `provider_mismatch` | The record belongs to another provider, including a local account. Establish the canonical account; an administrator may change the obsolete account's email or use the reviewed cleanup below. Do not change its provider to force a match. |
| `openid_id_mismatch` | The email is already bound to another OpenID subject, including a reassigned employee address. Confirm ownership and retention, then follow the email-reuse cleanup below if deletion is approved. |
| `google_id_mismatch` | The email is bound to a different Google account. Treat this as distinct identities; review the obsolete account before cleanup. |
| `object_id_mismatch` | The email is bound to a different Entra object ID. Check the actual Entra account and any email reassignment. Do not transfer the old account's permissions automatically. |
| `issuer_mismatch` | The email is bound to another issuer. Check the tenant/app configuration and intended account; never rewrite the stored issuer to match the login. |
| `duplicate_subject` | Multiple records share the subject lookup key. Investigate duplicates and decide which account and dependent resources to retain. |
| `duplicate_object_id` | Multiple records match the Entra object ID under the current or legacy missing issuer. Resolve duplicate ownership before retrying. |
| `duplicate_email` | Multiple records match the normalized email. Check the deployed shared collection's indexes and account ownership. Do not let the resolver pick an arbitrary record. |
| `concurrent_identity_change` | The matched identity changed during a conditional update. Retry once after competing writes settle; investigate persistent changes before any manual cleanup. |

Untrusted email is a separate error, not an `IDENTITY_CONFLICT` event. For Entra, configure the ID-token
optional claim `xms_edov`, ensure the token contains `email`, and correct the user's Entra `mail`
attribute to an appropriate tenant-verified domain when necessary. The claim must be boolean true.
Chat and Registry must use the same Entra app registration (`client_id`) in each environment. See the
[Entra setup guide](../entra-id-setup.md). Google retains its `email_verified` and configured hosted
domain checks.

Never hand-edit `openidId`, `googleId`, `idOnTheSource` or `openidIssuer` to make two identities match.
Never infer ownership from the username or email alone. Do not change user roles as a workaround.

## Reuse an email held by an obsolete identity

1. Establish which identities are involved and confirm that the older account and its dependent data
   may be deleted. Matching email addresses alone do not prove that two identities belong to the same
   person. If the old account or its resources must be retained, resolve ownership first; the cleanup
   script does not merge accounts or transfer resources.
2. Set `OLD_USER_ID` to the obsolete user's exact MongoDB `_id`. Configure the intended database and
   Weaviate connection as described in [Identity data cleanup](../../scripts/README.md#identity-data-cleanup).
   Back up MongoDB and arrange separate vector recovery. Keep writes involving the target user,
   resources, and ACLs paused throughout preview and execution.
3. From the Registry repository root, preview the cleanup before making any changes:

   ```sh
   uv run python scripts/purge_users.py --user-id "$OLD_USER_ID" --dry-run
   ```

4. Review every collection count and blocking finding. Foreign or unrecognized ACLs on authored
   resources block deletion; resolve their ownership and retention rather than removing grants just
   to pass the guard. Unmapped references also block by default. The scan discovers top-level fields
   from up to 500 documents per collection and queries all documents for those fields; nested
   references and fields absent from the sample are outside its coverage.
5. After reviewing the plan and confirming the data loss, apply it to the same exact user ID and
   answer the interactive confirmation:

   ```sh
   uv run python scripts/purge_users.py --user-id "$OLD_USER_ID" --apply
   ```

6. Check the actual deletion counts and confirm that the user and mapped references are absent.
   Repeat the dry-run command; it should report zero planned MongoDB deletions after a completed
   cleanup. Verify the original MCP Server vectors separately and retain the execution record.
   If a run fails, earlier deletions may have succeeded. Resolve the failure, preview the same ID
   again, and review the remaining plan before retrying.
7. Retry a fresh login after resolving the identity/configuration issue. A replacement user must get
   a new `_id` with no inherited ACLs or stored OAuth tokens; an existing valid user must keep their
   original `_id`. Check the provider-specific subject/issuer fields, intended group-derived access,
   and third-party client consent. Confirm the corresponding identity conflict no longer occurs.

The script defaults to dry-run and has no delete-by-email or delete-by-provider mode. Repeat
`--user-id` only for additional reviewed targets. `--force` skips confirmation but not safety checks.
`--allow-unmapped` leaves reported references unmanaged; `--skip-vector` leaves MCP Server vectors
untouched. Neither flag bypasses the shared-resource ACL guard. If vector cleanup is deferred, save
the original server IDs and names before applying, since their MongoDB records will be deleted.

ACL entries referencing the user only through `grantedBy` remain as audit records. File deletion
removes MongoDB metadata only; Chat's external file storage is not touched. Do not replace this
workflow with a manual `users.deleteOne()` or guessed issuer/email updates.
