# Skills Sync CLI simulation

This small Go program simulates the Skills Sync behavior expected from the real CLI:

- reads full or cursor-based metadata from Registry;
- hashes the actual local `SKILL.md` and supporting files;
- downloads and atomically replaces new, changed, missing, or locally modified skills;
- removes only directories tracked by its local manifest when Registry returns a tombstone.

## Run

```bash
cd scripts/skills-sync
REGISTRY_TOKEN='<token with skills-proxy-ops>' \
go run .
```

The default Registry URL is `https://jarvis-demo.ascendingdc.com/gateway`, the default destination is
`~/.jarvis/skills/`, and the state file is `~/.jarvis/manifest.json`.

The program automatically loads an optional `.env` from the current directory. Copy `.env.example` to `.env`, set the
token, and choose the appropriate log level:

```dotenv
REGISTRY_TOKEN=<token with skills-proxy-ops>
SKILLS_SYNC_LOG_LEVEL=info
```

The default level is `info`. Use `debug` locally for hash decisions, cursor handling, and HTTP timing; use `warn` or
`error` when quieter output is required. Logs never include the Bearer token or Skill contents.

The Registry URL and token can be supplied through environment variables, and every path can be overridden with a
command-line flag:

```bash
REGISTRY_URL=http://127.0.0.1:8000 \
REGISTRY_TOKEN='<token with skills-proxy-ops>' \
go run . \
  -skills-dir /tmp/jarvis-skills-test/skills \
  -manifest /tmp/jarvis-skills-test/manifest.json
```

Use a temporary directory while testing:

```bash
go run . \
  -registry-url http://127.0.0.1:8000 \
  -token "$REGISTRY_TOKEN" \
  -skills-dir /tmp/jarvis-skills-test/skills \
  -manifest /tmp/jarvis-skills-test/manifest.json \
  -full
```

`-full` is useful for detecting and repairing local edits. Normal mode sends the stored cursor and only processes
skills changed remotely since the previous successful sync.

## Test add, update, local drift, and deletion

```bash
go test ./...
```

Tests use an in-memory HTTP server and temporary directories. They do not require Registry or MongoDB and do not modify
developer data.
