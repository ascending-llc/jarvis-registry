# Skill-scanner lock files

Pinned installs for the two scanners baked into `docker/Dockerfile.skill-scan-worker`, each in its own
isolated virtualenv under `/opt/scanners/{cisco,skillspector}`.

| Scanner | Source | Pinned to |
|---|---|---|
| Cisco `cisco-ai-skill-scanner[bedrock,azure]` | PyPI | `2.3.2` |
| NVIDIA `skillspector` | git `github.com/NVIDIA/SkillSpector` | commit `38a1dfe93325d2f8bfcd4f8d0241eed706334915` (tag `v2.13.3`) |

The installed version is asserted at build time against the adapter's `tool_version`
(`skill-scan-worker/src/skill_scan_worker/adapters/{cisco,skillspector}.py`), which is the single source
of the pinned version — the Dockerfile has no version literal. The build runs
`python -m skill_scan_worker.smoke --check-versions`, which runs each tool's own `--version` and requires
an exact match. A drift fails the build instead of silently returning zero findings at runtime.

## Hash locking

Both tools pull large transitive trees that include `litellm`, which had credential-stealing releases.
Every lock here is hash-locked and installed with `--require-hashes` (`--python-platform` so uv resolves
for the `linux/amd64` image, not the host), so a rebuild cannot silently pick up a different transitive
version.

### Cisco — compiled from `requirements.in`

```bash
uv pip compile --python-version 3.12 --python-platform x86_64-manylinux_2_28 --generate-hashes \
  docker/skill-scanners/cisco/requirements.in -o docker/skill-scanners/cisco/requirements.txt
```

### SkillSpector — exported from its own `uv.lock`

SkillSpector is not on PyPI; the Dockerfile installs it from the pinned git commit with
`--no-deps --no-build-isolation`. Its runtime closure is exported from the `uv.lock` it ships at that
commit, so the image gets the versions upstream tested. From a checkout of commit
`38a1dfe93325d2f8bfcd4f8d0241eed706334915`:

```bash
uv export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.txt
```

Copy the result to `docker/skill-scanners/skillspector/requirements.txt` (it keeps hashes and markers,
excludes optional extras `mcp`/`dev`, and does not include `skillspector` itself). Its only build
requirement is `hatchling` (`[build-system]` at that commit), hash-locked separately so the no-isolation
build uses a pinned backend:

```bash
uv pip compile --python-version 3.12 --python-platform x86_64-manylinux_2_28 --generate-hashes \
  docker/skill-scanners/skillspector/build-requirements.in \
  -o docker/skill-scanners/skillspector/build-requirements.txt
```

## Bumping a scanner version

A version bump is never a single edit. Update **all** of these together:

1. the adapter's `tool_version` (`adapters/cisco.py` / `adapters/skillspector.py`) — the version guard
   reads it, so nothing else carries a version literal;
2. the pin here: Cisco's `requirements.in` version, or SkillSpector's git commit in the Dockerfile plus
   its `build-requirements.in`; then regenerate the hash-locked `requirements.txt` (and
   `build-requirements.txt`) as above;
3. re-record the fixtures: `uv run poe record-skill-scan-fixtures <image> --with-llm` against the rebuilt
   image (format drift must fail `uv run poe test-skill-scan-worker`).
