# Jarvis Registry — Release Notes Style Guide
# This file is read by github/copilot-release-notes@v1 automatically.
# Drop it at .github/release-notes-instructions.md and the action follows these conventions.

## Product context
Jarvis Registry is an enterprise MCP/Agent gateway. Readers are platform engineers,
DevOps leads, and enterprise architects. Write for a technical audience who cares about
security, reliability, and integration impact — not marketing language.

## Tone
- Direct and factual. One sentence per bullet explaining the WHAT and WHY.
- No filler phrases: "exciting", "powerful", "seamless", "leverage", "utilize".
- Use active voice. Prefer "Adds X" over "X has been added".

## Categories (use these headings in order, omit empty ones)
1. ⚠️ Breaking Changes & Upgrade Notes  ← always first if present
2. ✨ Features
3. 🐛 Bug Fixes
4. 🔧 Refactoring & Performance
5. 📦 Dependencies
6. 🌍 Documentation

## Attribution
End each bullet with the PR number in parentheses: `(#123)`.
For entries with no matching PR, use the short commit SHA: `([a1b2c3d])`.

## Skip rules
Omit these from the output entirely. Evaluate these rules from the PR title and
labels first — do **not** inspect the diff just to apply a skip rule.
- Pure whitespace / formatting commits
- Documentation-only PRs: title starts with `docs:` or `docs(<scope>):`, OR the
  PR carries a `documentation` label, OR every changed path is under `docs/`,
  `*.md`, or mkdocs config (only check paths if title/labels are inconclusive)
- i18n translation-only commits unless they add a new language

## Investigating PRs (shell tool usage)

When a PR title and body are not enough to write a useful entry, you may use the
allowed `git` tool to inspect commits — but observe these rules:

- Issue **one** `git <subcommand>` invocation per tool call. Do **not** wrap
  commands in bash constructs (no `for`/`while` loops, no `&&`, `;`, `|`,
  `$(...)`, subshells, or `xargs`). Only bare `git ...` invocations are
  permitted by the runner.
- If you need data about multiple commits, call `git` multiple times — once
  per commit — instead of looping.
- Prefer cheap commands first: `git log -1 --format=%B <sha>`,
  `git show --stat <sha>`, `git show -- <single-file> <sha>`.
- Avoid full diffs of large changes. If `git show --stat` reports a PR with
  more than ~500 changed lines, stick to the stat + commit message and the
  changed file list; do not request the full patch.
- If after one or two `git` calls you still cannot confidently describe the
  change, flag it under **🔍 Needs Review** rather than guessing.

## Uncertainty flagging
If Copilot is not confident what a PR changes (e.g. the PR body is empty or the diff
is ambiguous), place the entry under a separate section:

### 🔍 Needs Review
- [ ] PR #NNN — {title} — *could not determine impact; please review manually*

## Release summary
Start every set of notes with a 2–4 sentence paragraph summarising what this release
is about in plain English. Focus on the most impactful changes, not an exhaustive list.
