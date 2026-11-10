# Scanner test fixtures

- `skills/malicious/`, `skills/clean/`: original fixture skills written for this repo. The scanners read them; nothing executes them.
- `cisco/*.json`, `skillspector/*.json`: **synthetic** reports, written by hand. No real tool run produced them; their shape follows the pinned vendor sources (`cisco-ai-skill-scanner` 2.3.2 `ScanResult.to_dict`; SkillSpector v2.13.3 `_format_json`, `Finding.to_dict`, `sanitize_inference_usage`).

  They are not exact: real reports carry fields the adapters ignore (Cisco finding `id`/`analyzer`, SkillSpector `finding_id`/`tags`/`occurrences`, …), and analyzer names and line numbers may differ. The fields the adapters read follow the pinned sources.

## Replacing them with recorded reports

The synthetic files exist only because the scanner image did not exist yet. Once the skill-scan-worker image is built (AS-1986), replace them with real output and fix whatever the parse tests then catch:

```
uv run poe record-skill-scan-fixtures <image> [--with-llm]
```

See `scripts/skill_scan/record_fixtures.py` for the environment variables an LLM recording needs. Re-record on every scanner version bump.
