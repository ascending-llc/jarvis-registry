# Jarvis Registry — Release Notes Style Guide

<!-- Read automatically by github/copilot-release-notes@v1 from this path. -->

## Product context
Jarvis Registry is an enterprise MCP/Agent gateway. Readers are platform engineers,
DevOps leads, and enterprise architects. Write for a technical audience who cares about
security, reliability, and integration impact — not marketing language.

## Tone
- Direct and factual. One sentence per bullet explaining the WHAT and WHY.
- No filler phrases: "exciting", "powerful", "seamless", "leverage", "utilize".
- Use active voice. Prefer "Adds X" over "X has been added".
- **Describe user-visible behavior, not internal mechanics.** Omit transport
  protocols, wire formats, deployment topologies, internal class or module
  names, and other implementation details unless the change directly exposes
  that surface to the user. When in doubt, name the capability (what the user
  can now do) rather than how it is wired underneath.
  This rule applies **even when the PR title or body mentions those details
  explicitly** — many PR descriptions document internal mechanics for
  reviewers, but those details do not belong in user-facing release notes.
  When a feature adds a new entrypoint that fulfills requests by calling
  other services internally, describe the entrypoint and its user-visible
  contract only — not how the request is fulfilled downstream.
- **Verify every specific detail, or drop it.** Only include concrete facts
  (content types, header names, RFC numbers, field names, config keys, env
  vars, file paths, version numbers) if you have read them in the PR's diff,
  body, merge commit, or reviewer notes — not from memory, prior knowledge,
  or inference from surrounding context. If you cannot verify a specific,
  describe the change at a higher level instead. A wrong specific is worse
  than no specific.

## Categories (use these headings in order, omit empty ones)
1. ⚠️ Breaking Changes & Upgrade Notes
2. ✨ Features
3. 🐛 Bug Fixes
4. 🔧 Refactoring & Performance
5. 📦 Dependencies
6. 🌍 Documentation

Set each entry's `tag` to one heading exactly as written above, without the
list number (for example, `✨ Features`). The action renders the headings in
the order their tags first appear in `entries`, so emit `entries` grouped by
category, in the order listed above: breaking changes always come first.

A PR that changes only user-facing guides (`docs/**`, `README.md`) goes under
🌍 Documentation.

## Ordering within each section

List entries in **chronological order: oldest PR first, newest PR last.** The
underlying `git log` returns merges in reverse-chronological order, so you must
explicitly reverse them when writing. Ordering by PR number is a good proxy
when merge timestamps are unavailable.

## Attribution
The action's renderer automatically appends `(#NNN)` to every bullet. Do
**not** include the PR number in your `description` text — if you do, the
final output will show `(#NNN) (#NNN)`. Just write the description; the
action handles attribution.

## Skip rules
Omit these from the output entirely. Decide from the PR title, labels, commit
message, and changed paths (`git show --stat`) — do **not** inspect the diff just
to apply a skip rule.
- Pure whitespace / formatting PRs, as stated by the title or commit message
- PRs that only add a release's changelog page under `docs/changelog/`
- PRs that only change contributor- or agent-facing files: `CLAUDE.md`,
  `AGENTS.md`, `DEVELOPMENT.md`, `CONTRIBUTING.md`, `.github/**`, `.claude/**`
- i18n translation-only PRs unless they add a new language

## Investigating PRs (shell tool usage)

When a PR title and body are not enough to write a useful entry, you may use the
allowed `git` tool to inspect commits — but observe these rules:

- Issue **one** `git <subcommand>` invocation per tool call. Do **not** wrap
  commands in bash constructs (no `for`/`while` loops, no `&&`, `;`, `|`,
  `$(...)`, subshells, or `xargs`), and do not prefix them with `cd`: the
  working directory is already the repository. Only bare `git ...` invocations
  are permitted by the runner.
- **Never quote arguments.** A command containing `'` or `"` anywhere is denied
  by the runner. Use `--oneline`, or an unquoted format such as `--format=%B` or
  `--format=%h%x20%s` (`%x20` is a space).
- If you need data about multiple commits, call `git` multiple times — once
  per commit — instead of looping.
- Prefer cheap commands first: `git log --oneline <base>..<head>`,
  `git log -1 --format=%B <sha>`, `git show --stat <sha>`,
  `git show <sha> -- <single-file>`.
- Avoid full diffs of large changes. If `git show --stat` reports a PR with
  more than ~500 changed lines, stick to the stat + commit message and the
  changed file list; do not request the full patch.
- A "Permission denied" result means the command broke one of the rules above,
  not that git is unavailable. Retry the same query in a permitted form.
- If after one or two `git` calls you still cannot confidently describe the
  change, flag it as uncertain rather than guessing at specifics.
