# Scanner test fixtures

- `skills/malicious/`, `skills/clean/`: original fixture skills written for this repo. The scanners read them; nothing executes them.
- `cisco/*`, `skillspector/*`: real tool output, recorded with `uv run poe record-skill-scan-fixtures` from a local `linux/amd64` build of `docker/Dockerfile.skill-scan-worker` (`cisco-ai-skill-scanner` 2.3.2, SkillSpector v2.13.3 at `38a1dfe9`) on 2026-11-10.

| File | Scenario | Exit code (Cisco / SkillSpector) |
|---|---|---|
| `clean-nollm.json` | `skills/clean`, no LLM | 0 / 0 |
| `malicious-nollm.json` | `skills/malicious`, no LLM | 0 / 1 (risk score above 50) |
| `malicious-llm.json` | `skills/malicious` with Claude Haiku 4.5 (`anthropic.claude-haiku-4-5-20251001-v1:0`) through a Bedrock application inference profile in `us-east-1` | 0 / 1 |
| `llm-failed.json` | `skills/malicious` with the invalid model id `does-not-exist.invalid-model-v0` | 0 / 2 |
| `forced-error.stderr.txt` | a skill directory that does not exist: neither tool writes a report, so the recorder saves its stderr | 1 / 2 |

The recorder writes no exit code into the fixtures; the adapter tests pass the ones above.

The recorder redacts AWS account ids in ARNs (to `123456789012`) and assumed-role session names, because this repo is public. The adapters read neither.

## Why Haiku 4.5, not a Claude 5.x model

With a Claude 5.x model, an LLM run against `skills/malicious` is not reproducible. The model's content classifier often stops a reply partway through the JSON, because the reply describes credential theft and exfiltration (Bedrock `stopReason: content_filtered`, the Converse form of Claude's `refusal` stop reason). The tool then can't parse that reply, and the scan comes back `partial`/`llm_failed`. Haiku 4.5 returned complete replies, so `malicious-llm.json` shows the success path.

## Re-recording

Re-record on every scanner version bump, then fix whatever the parse tests catch:

```
uv run poe record-skill-scan-fixtures <image> [--with-llm]
```

See `scripts/skill_scan/record_fixtures.py` for the environment variables an LLM recording needs. Use an Anthropic model whose replies are not cut off by its content classifier (Haiku 4.5 today), or `malicious-llm.json` records a degraded run.
