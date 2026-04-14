---
name: local-frontend-check
description: Smoke-test or verify UI behaviour on the local Jarvis Registry frontend running at http://localhost/gateway. Use for manual regression checks, bug-fix verification, and end-to-end confirmation of specific flows without running the automated test suite.
allowed-tools: Bash(playwright-cli:*) Bash(docker:*)
---

## Local Frontend Check Skill

### Fixed Configuration

| Setting | Value |
|---|---|
| Base URL | `http://localhost/gateway` |
| Browser session | Named `jarvis` — keeps a stable, reusable identity across runs |
| Launch flags | `--persistent --headed` — profile saved to disk, browser window visible |
| Backend log container | `jarvis-registry-registry-1` |

---

### Workflow

Follow these phases in order every time this skill is invoked. Do not skip phases.

---

#### Phase 1 — Open the browser

```bash
playwright-cli -s=jarvis open http://localhost/gateway --persistent --headed
```

Then take a snapshot to read the page's current state:

```bash
playwright-cli -s=jarvis snapshot
```

---

#### Phase 2 — Handle authentication

Inspect the snapshot.

**If the app loads directly** (no login form, no auth redirect visible): the persistent profile is still valid. Skip to Phase 3.

**If a login page or auth redirect is visible**: the session has expired or this is a first run. Do the following:

1. Tell the user: *"The browser window is open and showing a login page. Please log in in the headed browser window, then confirm here when done."*
2. Use `AskUserQuestion` to wait for the user's confirmation before continuing.
3. Take a second snapshot and verify the app has loaded (URL is no longer the login page, and the main app UI is visible). If still on the login page, tell the user and repeat.

Because `--persistent` saves the profile to disk, subsequent runs will pick up the stored session automatically and this phase will be skipped.

---

#### Phase 3 — Orient and navigate

Read the user's description of what to check. Navigate to the relevant section of the UI:

- Use `playwright-cli -s=jarvis goto <url>` for direct navigation.
- Use `playwright-cli -s=jarvis snapshot --depth=3` to get a lightweight view of a complex page.
- Use `playwright-cli -s=jarvis click <ref>` to follow navigation links or open panels.

Take snapshots as needed to orient yourself before acting.

---

#### Phase 4 — Perform the described operations

Execute the operations step by step. After each significant action (form submit, dialog confirm, delete button, save button), take a snapshot immediately to observe the result.

Note what you see:

- **Success signals**: confirmation toast/banner, resource list updated, no error visible, HTTP response in UI shows 2xx.
- **Failure signals**: error toast, error dialog, HTTP 4xx/5xx error text, UI stuck in loading state.

Capture a screenshot at any notable point (success or failure):

```bash
playwright-cli -s=jarvis screenshot
```

---

#### Phase 5 — Read backend logs

After triggering the key operation(s), read recent logs from the backend container:

```bash
docker logs jarvis-registry-registry-1 --tail=200 2>&1
```

Look for:
- Python tracebacks or `ERROR`-level log lines near the time of the operation.
- HTTP route log lines showing the request method, path, and status code.
- Any `RuntimeError`, `ValueError`, or `DuplicateKeyError` messages.

If the log output is noisy, filter for the operation's timeframe by scanning the last N lines around when you performed the action.

---

#### Phase 6 — Report findings

Summarise clearly:

1. **Operations performed** — what you did and in what order.
2. **UI result** — what the browser showed after each operation (include snapshot filenames or key text).
3. **Log evidence** — relevant log lines (errors, warnings, or confirmation of a clean request/response).
4. **Verdict** — `PASS` or `FAIL` with a one-sentence reason.

---

### Notes

- Do **not** close the browser session at the end unless the user asks. Leaving it open avoids re-login overhead on follow-up checks.
- If the app is unreachable (`ERR_CONNECTION_REFUSED`), tell the user that the services may not be running and suggest `docker compose up -d`.
- Use `playwright-cli -s=jarvis snapshot <ref>` to inspect a specific element more closely when the full-page snapshot is too large.
- If an operation opens a confirmation dialog, handle it explicitly with `playwright-cli -s=jarvis click <confirm-button-ref>` rather than assuming it auto-dismisses.
