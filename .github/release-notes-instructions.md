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
Omit these from the output entirely:
- Pure whitespace / formatting commits
- Commits that only update `.github/` workflow files (e.g. "chore: bump actions version")
- Automated dependency bumps that have no user-visible impact
- i18n translation-only commits unless they add a new language

## Uncertainty flagging
If Copilot is not confident what a PR changes (e.g. the PR body is empty or the diff
is ambiguous), place the entry under a separate section:

### 🔍 Needs Review
- [ ] PR #NNN — {title} — *could not determine impact; please review manually*

## Release summary
Start every set of notes with a 2–4 sentence paragraph summarising what this release
is about in plain English. Focus on the most impactful changes, not an exhaustive list.
