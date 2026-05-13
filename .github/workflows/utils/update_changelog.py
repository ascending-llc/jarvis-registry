"""
update_changelog.py
-------------------
Called by the release-changelog GitHub Actions workflow.

Reads release metadata from environment variables set by the workflow,
then performs three mutations to the repo:

  1. Creates  docs/changelog/{tag}.md           (the per-release page)
  2. Prepends a row to the table in             docs/changelog/index.md
  3. Inserts a nav entry in                     mkdocs.yml
        Changelog:
          - Overview: changelog/index.md
          - vX.Y.Z:   changelog/vX.Y.Z.md   ← new
"""

import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

# ── Read env vars ──────────────────────────────────────────────────────────────
tag           = os.environ["RELEASE_TAG"]               # e.g. asc0.3.2
name          = os.environ.get("RELEASE_NAME") or tag   # e.g. "Jarvis Registry asc0.3.2"
body          = os.environ.get("RELEASE_BODY", "")
published_at  = os.environ.get("RELEASE_DATE", "")
release_url   = os.environ.get("RELEASE_URL", "")
is_prerelease = os.environ.get("PRERELEASE", "false").lower() == "true"

# Parse date → YYYY-MM-DD
try:
    date_obj = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    date_str = date_obj.strftime("%B %d, %Y")   # e.g. "May 07, 2026"
    date_iso = date_obj.strftime("%Y-%m-%d")     # for frontmatter
except Exception:
    date_str = datetime.now(UTC).strftime("%B %d, %Y")
    date_iso = datetime.now(UTC).strftime("%Y-%m-%d")

# Tag label and icon
if is_prerelease:
    tag_label = "Pre-release"
    tag_icon  = "🔖"
else:
    tag_label = "Release"
    tag_icon  = "🚀"

slug = tag  # e.g. asc0.3.2  →  changelog/asc0.3.2.md

# ── 1. Create  docs/changelog/{tag}.md ────────────────────────────────────────
changelog_dir = Path("docs/changelog")
changelog_dir.mkdir(parents=True, exist_ok=True)

page_path = changelog_dir / f"{slug}.md"

# Build a one-line description from the first non-empty line of the body
first_line = next(
    (line.strip().lstrip("#").strip() for line in body.splitlines() if line.strip()),
    f"The {tag} release of Jarvis Registry",
)
# Strip markdown from description for frontmatter (remove bold, links, etc.)
description = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", first_line)
description = re.sub(r"[*_`]", "", description)

frontmatter = f"""\
---
title: "{tag_icon} Jarvis Registry {tag}"
description: "{description}"
date: {date_iso}
tags:
  - {tag_label}
---

"""

# Body: keep release content verbatim but add a back-link at the top.
page_content = (
    frontmatter
    + "[← Back to changelog](index.md)\n\n"
    + f"# {tag_icon} Jarvis Registry {tag}\n\n"
    + f"_{date_str}_ · [{tag} on GitHub]({release_url})\n\n"
    + "---\n\n"
    + body.strip()
    + "\n"
)

page_path.write_text(page_content, encoding="utf-8")
print(f"✅  Created {page_path}")

# ── 2. Update  docs/changelog/index.md ────────────────────────────────────────
index_path = changelog_dir / "index.md"

new_row = f"| [{tag}]({slug}.md) | {date_str} | {tag_label} | {description} |\n"

if index_path.exists():
    content = index_path.read_text(encoding="utf-8")
    # Find the header separator row of the table and insert after it
    # Table pattern:  | --- | --- | --- | --- |
    table_sep_re = re.compile(r"(\| *[-:]+ *\| *[-:]+ *\| *[-:]+ *\| *[-:]+ *\|\n)")
    match = table_sep_re.search(content)
    if match:
        insert_pos = match.end()
        content = content[:insert_pos] + new_row + content[insert_pos:]
    else:
        content = content.rstrip("\n") + "\n" + new_row
    index_path.write_text(content, encoding="utf-8")
    print(f"✅  Updated {index_path}")
else:
    # Bootstrap the index from scratch
    index_content = (
        "# Changelog\n\n"
        "Release notes for every version of Jarvis Registry.\n\n"
        "| Version | Date | Type | Description |\n"
        "| ------- | ---- | ---- | ----------- |\n"
        f"{new_row}"
    )
    index_path.write_text(index_content, encoding="utf-8")
    print(f"✅  Created {index_path} (bootstrapped)")

# ── 3. Patch  mkdocs.yml  nav ──────────────────────────────────────────────────
mkdocs_path = Path("mkdocs.yml")
if not mkdocs_path.exists():
    print("⚠️  mkdocs.yml not found — skipping nav update")
    sys.exit(0)

mkdocs_text = mkdocs_path.read_text(encoding="utf-8")

new_nav_entry = f"  - {tag}: changelog/{slug}.md\n"

changelog_section_re = re.compile(
    r"(- Changelog:\s*\n"               # section header
    r"(?:[ \t]+-[ \t]+Overview:.*\n))"  # Overview line
)

if changelog_section_re.search(mkdocs_text):
    mkdocs_text = changelog_section_re.sub(
        r"\g<1>" + new_nav_entry,
        mkdocs_text,
        count=1,
    )
    print(f"✅  Inserted nav entry for {tag} in mkdocs.yml")
else:
    # Changelog section doesn't exist yet — insert before "- Project:" block
    project_re = re.compile(r"(^- Project:)", re.MULTILINE)
    changelog_block = (
        "- Changelog:\n"
        "  - Overview: changelog/index.md\n"
        f"  {new_nav_entry}"
        "\n"
    )
    if project_re.search(mkdocs_text):
        mkdocs_text = project_re.sub(changelog_block + r"\1", mkdocs_text, count=1)
        print("✅  Bootstrapped Changelog nav section in mkdocs.yml")
    else:
        mkdocs_text = mkdocs_text.rstrip("\n") + "\n" + changelog_block
        print("✅  Appended Changelog nav section to mkdocs.yml")

mkdocs_path.write_text(mkdocs_text, encoding="utf-8")
print("Done.")
