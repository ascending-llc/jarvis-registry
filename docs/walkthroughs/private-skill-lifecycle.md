# Test a private skill's lifecycle: import, update and revoke

Use this walkthrough to check how a private GitHub skill moves into Jarvis Registry, reaches an authorized developer, receives an update, and is removed from the CLI's managed directory after access is revoked.

This exercise uses the documented GitHub integration and CLI modes as concrete test paths. Skill Gateway also supports other Git providers, including GitLab, and consumers including Cursor. Those product capabilities do not change the GitHub-specific fields or CLI mode names used below.

Registry controls future access to its copy of a skill. A successful CLI sync reconciles the developer's managed local copy. An access change does not remotely erase downloaded files, independent copies, or material already loaded into an AI client.

The steps below are acceptance checks to run in your configured test environment. They do not assert that a particular deployment or AI client has already passed. For product background, see [Skill Gateway](../features/skill-gateway.md) and [AI Skills CLI](../features/ai-skills-cli.md). For installation and authentication, use the CLI's [setup guide](https://github.com/ascending-llc/jarvis-registry-cli/blob/main/docs/setup.md) and [getting started guide](https://github.com/ascending-llc/jarvis-registry-cli/blob/main/docs/getting-started.md).

## Before you start

Use a nonproduction Registry, an approved private GitHub test repository, and three separate test identities:

| Identity | Access for this exercise |
| --- | --- |
| Owner | Creates and manages the GitHub source, imports the skill, and manages sharing |
| Developer A | Has a direct Viewer grant on the imported skill |
| Developer B | Has no direct, group, or public grant on that skill |

Both developers need the applicable `skills-read` scope so you can distinguish a resource access decision from a missing API permission. Use their own logins and separate browser profiles. For CLI tests, use a disposable environment or isolated test OS account for each developer; changing a project path does not isolate cached credentials.

Record the Registry and CLI versions used for the exercise. Choose the CLI mode you intend to test: `claude`, `codex`, or `copilot`. Start with one mode, then repeat the delivery and removal checks for each additional mode. Record any additional consumer, such as Cursor, and its skill-loading setup separately from the chosen CLI mode.

Keep these boundaries separate when interpreting results:

| Boundary | What the check establishes |
| --- | --- |
| GitHub connection | Registry can read the approved repository revision using the importing user's source authorization |
| Registry scope and skill ACL | A user can list and download the particular skill from Registry |
| CLI synchronization | Files and the delivered Registry version are reconciled in a managed local directory |
| AI client loading | The selected client recognizes and uses the delivered skill in the intended project |

Downloading a skill grants no credentials for tools or services it mentions. The sample below needs no downstream service access. A real skill that uses an MCP server or another service still needs that service's separate authorization.

## 1. Prepare the sample repository

Download and extract the [complete sample files](examples/skills/private-skill-samples.zip) into a temporary folder. The archive contains both revisions, including the YAML metadata required for import. Use its files, not text copied from a rendered documentation page.

The revision 1 skill reviews release-note text supplied by the user. It checks for an owner and rollback note. Its supporting checklist makes delivery of a second file visible. You can [preview the skill](examples/skills/revision-1/release-notes-check/SKILL.md) and [checklist](examples/skills/revision-1/release-notes-check/references/checklist.md), but the previews are not substitutes for the complete downloaded files. The sample contains no credentials or executable files and instructs the client not to fetch data, run commands, or change files.

Copy the two reviewed files from the archive's `revision-1/release-notes-check/` folder into the private test repository with this layout:

```text
skills/
└── release-notes-check/
    ├── SKILL.md
    └── references/
        └── checklist.md
```

Keep the folder name and the frontmatter `name: release-notes-check` equal. Import discovery rejects a mismatch. Use this name only once in the exercise so another accessible skill with the same name does not confuse the results; always record the imported skill's ID as well.

The revision folders in this documentation represent successive contents of the same skill. Copy only revision 1 for the first import. Later, replace those same two files with revision 2; do not place both revisions under the import container.

Review the source using your normal repository approval process, then record the approved commit SHA. Repository review is your team's process. The sample's `disable-model-invocation: true` field is client metadata, not a security control; verify the selected client's behavior separately.

## 2. Import an approved private revision

As Owner, create a GitHub skill sync source in Registry using the approved GitHub App configuration and its authorization flow. The importing identity needs access to the private repository. A developer receiving the resulting Registry skill does not need that GitHub connection.

Use these source values:

| Source field | Value for this example |
| --- | --- |
| Owner and repository | The private test repository's GitHub owner and name |
| Ref | The approved commit SHA, for a reproducible import |
| Paths | `skills` — or `["skills"]` in an API request |

The configured path is a **container**. Registry discovers its direct child folders containing `SKILL.md`. With the layout above, `skills` is correct; `skills/release-notes-check` puts `SKILL.md` at the configured path's root and will not discover this skill. See the [GitHub source API](../design/skill-sync-source-api.md) for configuration fields and job responses.

Save the source, complete its GitHub authorization, and trigger a source sync. Wait for a terminal job result. A connected indicator or a successful connection test alone does not demonstrate that Registry persisted the skill.

Record the source ID, job ID, resolved commit SHA, job status, discovery/apply summaries, and any errors or source `syncMessage`. Inspect errors even when a job reports `success`: an ACL inheritance error can be recorded after content is applied without changing that status. Treat `partial_success` as a reason to inspect this skill's result individually.

Open the imported skill and record its ID and Registry version. Confirm its body and `references/checklist.md` match revision 1, and that the skill is enabled. The sample contains `SKILL.md` plus one supporting file; a supporting-file count is not a count of both files.

Acceptance criterion: the recorded job resolved the approved GitHub commit, and the expected skill and checklist are readable in Registry under the recorded skill ID.

## 3. Compare an allowed and an excluded developer

Keep the source private to Owner. Give Developer A a direct Viewer grant on the imported **skill**, and keep Developer B excluded. Check group and public grants as well as direct grants. Sharing the source can propagate access to its imported skills, so it would broaden this exercise beyond the single skill.

Check each developer independently. The two reads used by CLI synchronization are:

```text
GET /gateway/api/v1/skills?enabled=true
GET /gateway/api/v1/skills/<skill-id>/content
```

These paths are relative to the Registry origin used by the CLI. Use the actual skill ID from the import. Browser developer tools can inspect the signed-in Registry session's responses; CLI reads use the developer's bearer token with `skills-read`. Do not record or share tokens, cookies, or authorization headers.

| Check | Developer A | Developer B |
| --- | --- | --- |
| Successful enabled-skill list | Includes the recorded skill ID | Omits the recorded skill ID |
| Direct read of that skill's content | Returns the skill and checklist | Returns an access denial; normally HTTP `403` for an existing skill |

Confirm B can authenticate and obtain a successful list response. A failed login or HTTP `401` does not isolate the skill ACL decision. Likewise, an unavailable permission service is not evidence of the intended denial. If the results differ, check the skill's enabled state, the requested ID, API scope, and direct/group/public grants before moving on.

Use Owner's Registry browser session to manage source configuration and sharing. These operations have their own scope, resource-permission, and session requirements, including CSRF protection for writes. The CLI requests `skills-read`; its token is not an administrative write credential. See [RBAC and scopes](../design/scopes.md) for the permission model.

Acceptance criterion: both developers can authenticate, but only A can list and read this skill.

## 4. Deliver revision 1 into an empty disposable project

Install the CLI using its setup guide. In Developer A's isolated test environment, configure the test Registry and sign in:

```sh
jarvis-registry configure
jarvis-registry auth login
jarvis-registry auth status
jarvis-registry skills show
```

Check the Registry address, granted scopes, credential storage location, and any skipped skill IDs. The CLI normally uses the OS keyring, but its documented Linux fallback and file-only option can store credentials in a local plaintext file. Follow the CLI guide for your environment; avoid including credential files in project artifacts.

Create an empty disposable project directory with no existing skills directories. Replace the example absolute path below with that directory. Keep an explicit project path on every sync command in this exercise; omitting it selects personal scope.

**Directory management can delete files.** Once the CLI manages a directory, it removes entries outside its recorded skill set, apart from its reserved files. For project-scoped Codex and Copilot, it manages the entire `.agents/skills` or `.github/skills` directory, including files another tool or a person may have placed there. An existing directory without a valid CLI marker requires interactive takeover consent; accepting can delete its untracked contents during that same sync. Cleanup runs before the remote list request, so a later connection failure does not protect those contents. For this exercise, decline any takeover prompt and choose a new empty project. Do not put handcrafted skills in these managed directories.

Run one of the following commands for the selected mode:

```sh
jarvis-registry skills sync --mode claude /absolute/path/to/empty-test-project
```

```sh
jarvis-registry skills sync --mode codex /absolute/path/to/empty-test-project
```

```sh
jarvis-registry skills sync --mode copilot /absolute/path/to/empty-test-project
```

Each command syncs the enabled skills available to the signed-in developer, subject to CLI skip settings; it does not select only this sample. The isolated test identity should have access only to content appropriate for the exercise.

| Mode | Sample directory relative to the project | Sync manifest relative to the project |
| --- | --- | --- |
| `claude` | `.claude/skills/jarvis-registry/skills/release-notes-check/` | `.claude/skills/jarvis-registry/skill-lock.json` |
| `codex` | `.agents/skills/release-notes-check/` | `.agents/skills/skill-lock.json` |
| `copilot` | `.github/skills/release-notes-check/` | `.github/skills/skill-lock.json` |

Confirm both `SKILL.md` and `references/checklist.md` exist. In `skill-lock.json`, locate the recorded skill ID and compare its version with Registry. The CLI reconstructs the Markdown frontmatter, so check the name, instructions, metadata, and supporting file contents rather than assuming byte-for-byte equality with the source `SKILL.md`.

Run the same command again without another source import or Registry edit. The sample should report `Unchanged`. Read the summary and exit status: a run can deliver some skills while returning an error for others. `Unchanged` means the recorded name/version and expected directory still match; it does not certify that nobody edited files inside the directory. Keep downloaded files unmodified during this exercise.

Open or reload the selected AI client in the disposable project and verify it recognizes the skill using that client's supported workflow. The CLI guide describes Claude's `jarvis-registry:<skill-name>` naming and Codex/Copilot's `<skill-name>` naming. Use a harmless release note such as:

```text
Owner: Test Release Team
Rollback: Restore the previous test configuration.
```

Record the client version and whether its result identifies the supplied owner and rollback note. A file-delivery check and a client-loading check are separate results.

Acceptance criterion: both sample files and the correct Registry version are delivered, a repeat sync is unchanged, and the chosen client's loading result is recorded separately.

## 5. Update the same skill and verify both files

Replace the private repository's two sample files with the complete files from the archive's `revision-2/release-notes-check/` folder. You can [preview the revision 2 skill](examples/skills/revision-2/release-notes-check/SKILL.md) and [checklist](examples/skills/revision-2/release-notes-check/references/checklist.md), but copy from the extracted archive so the metadata is retained. Keep the same source, folder, and skill name. Revision 2 adds a verification-step check.

Review the change, record the new approved commit SHA, set the source ref to that commit, and trigger another source sync. Inspect the finished job, resolved SHA, errors, and skill result as before. Confirm the imported skill keeps its ID and now contains both revised files. Record its actual Registry version.

The GitHub commit SHA and Registry version are different identifiers. An existing imported skill's Registry version increments when it is applied again, including a re-import with unchanged source content. Do not assume revision 2 must have Registry version `2`.

As Developer A, run the same CLI sync command in the same disposable project. Confirm the new verification instruction and checklist item are both present, and the manifest records the new Registry version. Repeat the harmless client check with a release note that omits a verification step; record whether the refreshed client reports it as missing.

Future GitHub-owned changes should go through the source repository and another import. Local downloaded edits are not pushed back to Registry or GitHub and can be replaced by a later update.

Acceptance criterion: the same Registry skill ID receives the approved revision, and both files and its new Registry version reach A's managed project directory.

## 6. Revoke access and check the limits

Before changing access, save a copy of the harmless sample outside every managed skills directory. Keep the original managed copy in place. The separate copy demonstrates the boundary between Registry authorization and files already downloaded.

As Owner, remove A's direct Viewer grant. Check for every other effective grant, including group/public access and grants on the GitHub source. Source-to-skill ACL inheritance inserts missing child grants when a source sync runs; it does not continuously reconcile removals or role changes. Removing source access alone does not remove child grants already copied. Removing a child grant while retaining a source grant can allow a later import to add that child grant again.

Then check as Developer A:

1. Obtain a successful enabled-skill list response and confirm it omits the recorded skill ID. Confirm a direct content read is denied.
2. Before running CLI sync, confirm the existing managed files are still present. An ACL change does not contact the developer's filesystem.
3. Run the same CLI sync command with the same mode and project path. Confirm it succeeds, reports the skill removed, removes its managed directory, and removes its ID from `skill-lock.json`.
4. Confirm the independent copy still exists. Reload or restart the AI client and record its state separately; the ACL change cannot recall material already loaded into a session.
5. As Owner, run another source import. Repeat A's list/content checks to confirm access has not returned through a remaining source grant. Sync again and confirm the managed sample remains absent.

A failed login, HTTP `401`, HTTP `500`, or unavailable Registry does not prove revocation reconciliation. A list failure stops reconciliation against the remote skill set; a previously tracked local copy can remain. Resolve the error and repeat the successful list and sync checks. Do not treat a failing sync as a remote erase operation.

Acceptance criterion: A loses Registry access, a subsequent successful sync removes the managed skill and manifest entry, another import does not restore access, and the independent copy remains. This establishes controlled future delivery and local reconciliation, not remote deletion of every copy.

## Keep an acceptance record

Store sanitized results with your test change or release record:

| Check | Evidence to retain |
| --- | --- |
| Import and update | Registry/CLI versions, source and job IDs, resolved commit SHAs, skill ID, Registry versions, and any errors |
| User access | A/B successful list results and direct content-read status for the same skill ID |
| Delivery | Selected mode, disposable project layout, both file contents, summary/exit status, and matching manifest version |
| Client behavior | Client name/version, loading or reload steps, and observed sample result |
| Revocation | Successful list/content access checks, successful sync removal, absent manifest entry, retained independent copy, and checks after another import |

Keep tokens, cookies, client secrets, and private production skill contents out of that record. Record incomplete or failed checks explicitly so source configuration, authorization, file delivery, and client behavior can be assessed independently.
