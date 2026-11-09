---
name: release-notes-check
description: Check a supplied release note for its owner and rollback note
user-invocable: true
disable-model-invocation: true
---

# Release notes check

Review only the release-note text supplied by the user. Do not fetch data, run commands, or change files.

Report whether the note names an owner and contains a rollback note. Quote the supplied text for each finding. If either item is absent, write "Not provided" rather than inventing it.

Use the checklist in `references/checklist.md`. This is sample revision 1, not a production approval policy.
