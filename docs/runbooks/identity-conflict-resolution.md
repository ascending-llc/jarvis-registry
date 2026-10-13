# Identity conflict resolution

This runbook covers the manual email-reuse cleanup introduced by AS-1901. AS-1899 will add identity
diagnosis, login error handling, and verification for its `(iss, sub)` resolver. Do not assume that
resolver is already deployed or that deleting a user resolves every login failure.

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
7. Retry login through the identity workflow actually deployed and verify the resulting account and
   access. Add AS-1899-specific identity checks alongside that implementation.

The script defaults to dry-run and has no delete-by-email or delete-by-provider mode. Repeat
`--user-id` only for additional reviewed targets. `--force` skips confirmation but not safety checks.
`--allow-unmapped` leaves reported references unmanaged; `--skip-vector` leaves MCP Server vectors
untouched. Neither flag bypasses the shared-resource ACL guard. If vector cleanup is deferred, save
the original server IDs and names before applying, since their MongoDB records will be deleted.

ACL entries referencing the user only through `grantedBy` remain as audit records. File deletion
removes MongoDB metadata only; Chat's external file storage is not touched. Do not replace this
workflow with a manual `users.deleteOne()` or guessed issuer/email updates.
